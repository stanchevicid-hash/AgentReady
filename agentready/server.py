"""Web app + JSON API (standard library HTTP server)."""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import shutil
import socket
import threading
import time
from collections import deque
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import verify as verify_mod
from .auth import SESSION_DAYS, Auth
from .badge import badge_svg
from .config import ROOT, ROLE_DESCRIPTIONS, Settings
from .llm import BILLING, TokenFactoryLLM
from .pipeline import STAGES, Pipeline, RunOptions

WEB = ROOT / "web"
EXAMPLES = ROOT / "examples"
# app (PWA) files served from web/
STATIC = {
    "/manifest.webmanifest": "application/manifest+json",
    "/sw.js": "text/javascript; charset=utf-8",
    "/icon-180.png": "image/png",
    "/icon-192.png": "image/png",
    "/icon-512.png": "image/png",
}
PAGES = ("/", "/index.html", "/welcome", "/join", "/verify", "/history", "/stats", "/settings", "/login", "/pair")
COOKIE = "ar_session"
# the page loads only its own code and the QR library; it can't be framed (clickjacking)
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
       "style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
LOOPBACK_HOST = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$")
ACTIVE: dict = {}
_lock = threading.Lock()
# what happened on any device, so the phone and the PC see each other's actions
ACTIVITY: deque = deque(maxlen=200)


def log_activity(kind: str, msg: str, device: dict | None = None, ref: str = "") -> None:
    ACTIVITY.append({"ts": time.time(), "kind": kind, "msg": msg, "ref": ref, "user": (device or {}).get("user", ""),
                     "device_id": (device or {}).get("id", ""), "device": (device or {}).get("device", "")})


def signup_open() -> bool:
    # anyone may create an account (each still sees only their own runs); off by default because
    # every account can make this server run the tests of a repo it chooses
    return os.environ.get("AGENTREADY_SIGNUP") == "open"


def resolve_source(source: str, public: bool) -> str:
    source = (source or "").strip()
    if source.startswith("example:"):
        name = source.split(":", 1)[1]
        path = EXAMPLES / name
        if not re.fullmatch(r"[a-z0-9_-]+", name) or not path.is_dir():
            raise ValueError(f"unknown example '{name}'")
        return str(path)
    if public and not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+(\.git)?/?", source):
        raise ValueError("this public demo accepts example repos or public https://github.com/owner/repo URLs")
    return source


def is_public() -> bool:
    return os.environ.get("AGENTREADY_PUBLIC_DEMO") == "1"


class Handler(BaseHTTPRequestHandler):
    server_version = f"AgentReady/{__version__}"
    settings: Settings = None  # set in serve()
    auth: Auth = None          # set in serve()
    lan_url: str = ""          # set in serve() when listening on all interfaces
    setup_code: str = ""       # set in serve(); creating the owner account from another device needs it

    def log_message(self, fmt, *args):  # quieter console
        pass

    # ------------------------------------------------------------ helpers
    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200, extra: dict | None = None) -> None:
        self._send(status, json.dumps(obj, default=str).encode(), "application/json", extra)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if not 0 <= length <= 1_000_000:
            raise ValueError("body too large")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    def _token(self) -> str | None:
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c[COOKIE].value if COOKIE in c else None

    def _cookie(self, token: str, max_age: int) -> dict:
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return {"Set-Cookie": f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}{secure}"}

    def _ip(self) -> str:
        return self.client_address[0]

    def _local(self) -> bool:
        """This very computer, opened as localhost. A web page that rebinds its own domain to 127.0.0.1
        comes from loopback too, but with its own name in Host, so it doesn't count."""
        return self._ip() in ("127.0.0.1", "::1") and bool(LOOPBACK_HOST.match(self.headers.get("Host", "")))

    def _ua(self) -> str:
        return self.headers.get("User-Agent", "")

    def _me(self) -> dict | None:
        return self.auth.session(self._token())

    def _gate(self) -> dict | None | bool:
        """Session of the signed-in device; False after sending 401. Public demo: no accounts (None)."""
        if is_public():
            return None
        me = self._me()
        if not me:
            self._json({"error": "sign in required", "auth": True}, 401)
            return False
        return me

    def _mine(self, record: dict | None, me: dict | None) -> bool:
        """Does this run / verification belong to the signed-in account? (Public demo: shared.)"""
        if record is None:
            return False
        if me is None:
            return True
        return (record.get("owner") or self.auth.owner()) == me["user"]

    def _cross_site(self) -> bool:
        """A request another website made the browser send (CSRF). Our pages always send JSON from their own
        origin; a form on another site can't set that content type, and browsers name the real Origin."""
        origin = self.headers.get("Origin")
        # behind a proxy (Nebius endpoint) the public host comes as X-Forwarded-Host; another site can't set it
        if origin and urlparse(origin).netloc not in (self.headers.get("Host", ""), self.headers.get("X-Forwarded-Host", "")):
            return True
        return self.command == "POST" and not self.headers.get("Content-Type", "").startswith("application/json")

    def _token_ok(self) -> bool:
        # public demo only: starting things needs the shared access token
        token = os.environ.get("AGENTREADY_ACCESS_TOKEN")
        return not token or self.headers.get("X-Access-Token") == token

    def _run_dir(self, rid: str) -> Path | None:
        # "_"-prefixed folders hold verifier results and accounts, never runs
        if not re.fullmatch(r"[\w-]+", rid) or rid.startswith("_"):
            return None
        d = Path(self.settings.runs_dir) / rid
        return d if (d / "run.json").is_file() else None

    def _verify_dir(self) -> Path:
        d = Path(self.settings.runs_dir) / "_verify"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load_run(self, rid: str) -> dict | None:
        with _lock:
            p = ACTIVE.get(rid)
        if p is not None:
            with p._lock:
                d = p.state.to_dict()
                d["usage"] = p.llm.usage.snapshot()
                return json.loads(json.dumps(d, default=str))
        d = self._run_dir(rid)
        return json.loads((d / "run.json").read_text(encoding="utf-8")) if d else None

    def _base_url(self) -> tuple[str, bool]:
        """URL another device can open, and whether it is reachable from another device at all."""
        if self.lan_url:
            return self.lan_url, True
        host = self.headers.get("Host", "")
        proto = self.headers.get("X-Forwarded-Proto", "http")
        local = re.match(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", host) is not None
        return f"{proto}://{host}", not local

    # ------------------------------------------------------------ GET
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path in PAGES or path.startswith(("/run/", "/verify/")):
            return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path in STATIC:
            return self._send(200, (WEB / path[1:]).read_bytes(), STATIC[path])
        if path == "/api/health":
            s = self.settings
            return self._json({
                "version": __version__, "api_key_set": bool(s.api_key), "tavily_set": bool(s.tavily_key),
                "out_of_credits": BILLING["out_of_credits"],
                "models": s.models, "roles": ROLE_DESCRIPTIONS, "executor": s.executor,
                "public": is_public(), "token_required": bool(os.environ.get("AGENTREADY_ACCESS_TOKEN")),
                "examples": sorted(p.name for p in EXAMPLES.iterdir() if p.is_dir()) if EXAMPLES.exists() else [],
                "stages": [{"key": k, "label": label} for k, label in STAGES],
            })
        if path == "/api/auth/status":
            me = None if is_public() else self._me()
            return self._json({"accounts": not is_public(), "has_users": self.auth.has_users(),
                               "setup_code_needed": not self.auth.has_users() and not self._local(),
                               "signup": "open" if signup_open() or self._local() else "invite",
                               "user": me and me["user"], "device_id": me and me["id"], "device": me and me["device"]})
        m = re.fullmatch(r"/api/runs/([\w-]+)/badge\.svg", path)
        if m:  # badges are embedded in READMEs, so they stay public
            run = self._load_run(m.group(1))
            return self._send(200, badge_svg(((run or {}).get("metrics") or {}).get("score_after")).encode(), "image/svg+xml")

        me = self._gate()
        if me is False:
            return
        if path == "/api/devices":
            base, reachable = self._base_url()
            return self._json({"devices": self.auth.devices(me["user"]), "current": me["id"],
                               "base_url": base, "reachable": reachable, "lan_url": self.lan_url})
        if path == "/api/activity":
            since = float((parse_qs(url.query).get("since") or ["0"])[0] or 0)
            return self._json({"now": time.time(), "items": [a for a in ACTIVITY if a["ts"] > since
                                                             and (me is None or a["user"] == me["user"])]})
        if path == "/api/runs":
            out = []
            root = Path(self.settings.runs_dir)
            if root.exists():
                for d in sorted(root.iterdir(), reverse=True)[:60]:
                    run = self._load_run(d.name)
                    if not self._mine(run, me):
                        continue
                    m2 = run.get("metrics") or {}
                    out.append({"id": run["id"], "source": run["options"]["source"], "status": run["status"],
                                "created": run["created"], "score_before": m2.get("score_before"),
                                "score_after": m2.get("score_after"), "label": run["options"].get("label", "")})
            return self._json(out)
        if path == "/api/verify":
            items = []
            for f in sorted(self._verify_dir().glob("*.json"), reverse=True)[:200]:
                v = json.loads(f.read_text(encoding="utf-8"))
                if not self._mine(v, me):
                    continue
                if len(items) >= 60:
                    break
                items.append({k: v.get(k) for k in ("id", "source", "status", "created", "score", "files")})
            return self._json(items)
        m = re.fullmatch(r"/api/verify/([\w-]+)", path)
        if m:
            f = self._verify_dir() / f"{m.group(1)}.json"
            v = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
            # someone else's: answer exactly as if it didn't exist
            return self._json(v) if self._mine(v, me) else self._json({"error": "verification not found"}, 404)
        m = re.fullmatch(r"/api/runs/([\w-]+)(/agents\.md)?", path)
        if m:
            run = self._load_run(m.group(1))
            if not self._mine(run, me):
                return self._json({"error": "run not found"}, 404)
            if m.group(2):
                return self._send(200, (run.get("agents_md") or "").encode(), "text/markdown; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="AGENTS.md"'})
            return self._json(run)
        self._json({"error": "not found"}, 404)

    # ------------------------------------------------------------ DELETE
    def do_DELETE(self):
        path = urlparse(self.path).path
        if self._cross_site():
            return self._json({"error": "cross-site request refused"}, 403)
        if is_public() and not os.environ.get("AGENTREADY_ACCESS_TOKEN"):
            return self._json({"error": "deleting is disabled on this public demo"}, 403)
        if not self._token_ok():
            return self._json({"error": "access token required"}, 401)
        me = self._gate()
        if me is False:
            return
        m = re.fullmatch(r"/api/devices/([0-9a-f]{8})", path)
        if m and me:
            ok = self.auth.revoke(me["user"], m.group(1))
            if ok:
                log_activity("device", "signed out a device", me)
            return self._json({"revoked": ok}, 200 if ok else 404)
        m = re.fullmatch(r"/api/verify/([\w-]+)", path)
        if m:
            f = self._verify_dir() / f"{m.group(1)}.json"
            v = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
            if not self._mine(v, me):
                return self._json({"error": "verification not found"}, 404)
            if v.get("status") == "running":
                return self._json({"error": "this verification is still running"}, 409)
            f.unlink()
            return self._json({"deleted": m.group(1)})
        m = re.fullmatch(r"/api/runs/([\w-]+)", path)
        if not m:
            return self._json({"error": "not found"}, 404)
        rid = m.group(1)
        if not self._mine(self._load_run(rid), me):
            return self._json({"error": "run not found"}, 404)
        with _lock:
            p = ACTIVE.get(rid)
            if p is not None and p.state.status in ("queued", "running"):
                return self._json({"error": "this run is still in progress"}, 409)
            ACTIVE.pop(rid, None)
        d = self._run_dir(rid)
        if not d:
            return self._json({"error": "run not found"}, 404)
        shutil.rmtree(d, ignore_errors=True)
        self._json({"deleted": rid})

    # ------------------------------------------------------------ POST
    def do_POST(self):
        path = urlparse(self.path).path
        if self._cross_site():
            return self._json({"error": "cross-site request refused"}, 403)
        try:
            body = self._body()
        except (ValueError, json.JSONDecodeError):
            return self._json({"error": "invalid JSON"}, 400)

        if path.startswith("/api/auth/"):
            return self._auth_post(path, body)
        if path == "/api/erase":
            return self._erase(body)

        cancel = re.fullmatch(r"/api/runs/([\w-]+)/cancel", path)
        if path not in ("/api/runs", "/api/verify", "/api/pair", "/api/invite") and not cancel:
            return self._json({"error": "not found"}, 404)
        if not self._token_ok():
            return self._json({"error": "access token required to start runs"}, 401)
        me = self._gate()
        if me is False:
            return
        public = is_public()
        # runs execute the cloned repo's tests: on the public demo only token holders may start or stop them
        if public and path not in ("/api/pair", "/api/invite") and not os.environ.get("AGENTREADY_ACCESS_TOKEN"):
            return self._json({"error": "starting runs needs an access token on this public demo"}, 403)
        if cancel:
            with _lock:
                p = ACTIVE.get(cancel.group(1))
            if p is None or not self._mine({"owner": p.state.owner}, me):
                return self._json({"error": "run not found"}, 404)
            if p.state.status not in ("queued", "running"):
                return self._json({"error": "this run is not in progress"}, 409)
            p.cancel()
            log_activity("run_done", f"stopped an agent test: {p.opts.label}", me, f"/run/{p.state.id}")
            return self._json({"cancelling": p.state.id})
        if path == "/api/pair":
            if not me:
                return self._json({"error": "pairing needs accounts (not available on the public demo)"}, 400)
            base, reachable = self._base_url()
            code = self.auth.pair_code(me["user"])
            return self._json({"code": code, "url": f"{base}/pair?code={code}", "reachable": reachable,
                               "expires_in": 300})
        if path == "/api/invite":
            if not me:
                return self._json({"error": "invites need accounts (not available on the public demo)"}, 400)
            base, _ = self._base_url()
            code = self.auth.invite_code(me["user"])
            return self._json({"code": code, "url": f"{base}/join?code={code}", "expires_in": 24 * 3600})
        if path == "/api/verify":
            return self._start_verify(body, public, me)
        return self._start_run(body, public, me)

    def _auth_post(self, path: str, body: dict) -> None:
        if is_public():
            return self._json({"error": "accounts are off on the public demo"}, 400)
        ua = self._ua()
        try:
            if path == "/api/auth/setup":
                # whoever creates the first account owns the server: from another device prove you can
                # see this server's console
                code = str(body.get("setup_code", "")).strip()
                if not self._local() and not (self.setup_code and hmac.compare_digest(code, self.setup_code)):
                    return self._json({"error": "enter the setup code shown in the AgentReady console"}, 403)
                user = self.auth.setup(body.get("username", ""), body.get("password", ""))
                token = self.auth.login(user, body.get("password", ""), ua, self._ip())
            elif path == "/api/auth/signup":
                user = self.auth.signup(body.get("username", ""), body.get("password", ""),
                                        str(body.get("invite", "")), allowed=signup_open() or self._local())
                token = self.auth.login(user, body.get("password", ""), ua, self._ip())
            elif path == "/api/auth/login":
                token = self.auth.login(body.get("username", ""), body.get("password", ""), ua, self._ip())
            elif path == "/api/auth/claim":
                token = self.auth.claim(str(body.get("code", "")), ua)
            elif path == "/api/auth/logout":
                self.auth.logout(self._token() or "")
                return self._json({"ok": True}, extra=self._cookie("", 0))
            else:
                return self._json({"error": "not found"}, 404)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        except PermissionError as exc:
            return self._json({"error": str(exc)}, 401)
        me = self.auth.session(token)
        log_activity("device", {"claim": "paired", "signup": "created the account"}.get(path.rsplit("/", 1)[1], "signed in"), me)
        self._json({"user": me["user"], "device_id": me["id"]}, extra=self._cookie(token, SESSION_DAYS * 86400))

    def _erase(self, body: dict) -> None:
        """Delete this account's runs, verifications, devices and the account itself. Other accounts stay."""
        if is_public():
            return self._json({"error": "nothing to erase on the public demo"}, 400)
        me = self._gate()
        if me is False:
            return
        user = me["user"]
        with _lock:
            if any(p.state.status in ("queued", "running") and self._mine({"owner": p.state.owner}, me)
                   for p in ACTIVE.values()):
                return self._json({"error": "stop the run in progress first"}, 409)
        # which runs and checks are this account's: decided before the account (and maybe ownership) goes
        root = Path(self.settings.runs_dir)
        runs = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith("_") and self._mine(self._load_run(d.name), me)]
        mine = [(f, json.loads(f.read_text(encoding="utf-8"))) for f in self._verify_dir().glob("*.json")]
        checks = [f for f, v in mine if self._mine(v, me)]
        # a check still running would write its result back after the account is gone
        if any(v.get("status") == "running" for f, v in mine if self._mine(v, me)):
            return self._json({"error": "wait for the code check in progress to finish first"}, 409)
        try:
            self.auth.erase(user, str(body.get("password", "")), self._ip())
        except PermissionError as exc:
            return self._json({"error": str(exc)}, 401)
        with _lock:
            for d in runs:
                ACTIVE.pop(d.name, None)
        for d in runs:
            shutil.rmtree(d, ignore_errors=True)
        for f in checks:
            f.unlink(missing_ok=True)
        for a in [a for a in ACTIVITY if a["user"] == user]:
            ACTIVITY.remove(a)
        if not self.auth.has_users():
            # the server is unclaimed again: a code that was printed (and maybe seen) before must not claim it
            Handler.setup_code = secrets.token_urlsafe(9)
            print(f"No accounts left. Creating one from another device needs this new setup code: {Handler.setup_code}")
        self._json({"erased": True}, extra=self._cookie("", 0))

    def _start_verify(self, body: dict, public: bool, me: dict | None) -> None:
        patch = str(body.get("patch") or "")
        if not patch.strip():
            return self._json({"error": "paste the code or the diff the AI wrote"}, 400)
        raw = str(body.get("source") or "").strip()
        try:
            source = resolve_source(raw, public) if raw else ""  # no repo: the code is checked on its own
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        vid = verify_mod.new_id()
        path = self._verify_dir() / f"{vid}.json"
        files = verify_mod.changed_files(patch) if verify_mod.is_diff(patch) else []
        label = raw[:200] or (", ".join(files[:3]) if files else "Code snippet")
        state = {"id": vid, "source": label, "status": "running", "created": time.time(),
                 "checks": [], "files": [], "score": None, "error": "", "device": (me or {}).get("device", ""),
                 "owner": (me or {}).get("user", ""),
                 "patch": patch[:200_000]}
        lock = threading.Lock()

        def save():
            with lock:
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(state, default=str), encoding="utf-8")
                tmp.replace(path)

        def work():
            try:
                llm = TokenFactoryLLM(self.settings) if self.settings.api_key else None
                res = verify_mod.verify(source, patch, llm, None if public else (body.get("test_cmd") or None),
                                        on_check=lambda c: (state["checks"].append(c), save()))
                state.update(files=res["files"], score=verify_mod.score(state["checks"]), status="done")
                log_activity("verify_done", f"code check finished: {label} ({state['score']})", me, f"/verify/{vid}")
            except Exception as exc:
                state.update(status="error", error=str(exc)[:600])
                log_activity("verify_done", f"code check failed: {label}", me, f"/verify/{vid}")
            save()

        save()
        log_activity("verify_start", f"started a code check: {label}", me, f"/verify/{vid}")
        threading.Thread(target=work, daemon=True, name=f"verify-{vid}").start()
        self._json({"id": vid}, HTTPStatus.CREATED)

    def _start_run(self, body: dict, public: bool, me: dict | None) -> None:
        with _lock:
            running = [p for p in ACTIVE.values() if p.state.status in ("queued", "running")]
        if public and running:
            return self._json({"error": "a run is already in progress on this demo server, please wait",
                               "active": running[0].state.id}, 429)
        try:
            source = resolve_source(body.get("source", ""), public)
            cap = (lambda v, lo, hi, d: max(lo, min(hi, int(v if v not in (None, "") else d))))
            opts = RunOptions(
                source=source,
                tasks=cap(body.get("tasks"), 1, 6 if public else 30, 5),
                repeats=cap(body.get("repeats"), 1, 2 if public else 5, 2),
                parallel=cap(body.get("parallel"), 1, 6 if public else 16, 4),
                max_steps=cap(body.get("max_steps"), 6, 24 if public else 60, 24),
                executor=self.settings.executor if public else (body.get("executor") or self.settings.executor),
                test_cmd=None if public else (body.get("test_cmd") or None),
                label=str(body.get("source", ""))[:120],
            )
            llm = TokenFactoryLLM(self.settings)
        except Exception as exc:
            return self._json({"error": str(exc)}, 400)
        pipe = Pipeline(self.settings, llm, opts)
        pipe.state.owner = (me or {}).get("user", "")
        with _lock:
            ACTIVE[pipe.state.id] = pipe
        pipe.save(force=True)
        rid, label = pipe.state.id, opts.label

        def work():
            st = pipe.run()
            m = st.metrics or {}
            msg = (f"agent test finished: {label} ({m.get('score_before')} → {m.get('score_after')})"
                   if st.status == "done" else f"agent test stopped: {label}")
            log_activity("run_done", msg, me, f"/run/{rid}")

        log_activity("run_start", f"started an agent test: {label}", me, f"/run/{rid}")
        threading.Thread(target=work, daemon=True, name=f"run-{rid}").start()
        self._json({"id": rid}, HTTPStatus.CREATED)


def mark_interrupted(runs_dir: Path) -> int:
    """Runs left "running" by a server that stopped will never finish: say so instead of spinning forever."""
    n = 0
    for f in Path(runs_dir).glob("*/run.json"):
        try:
            run = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if run.get("status") in ("queued", "running"):
            run.update(status="error", error="interrupted: the AgentReady server stopped during this run",
                       finished=run.get("finished") or f.stat().st_mtime)
            f.write_text(json.dumps(run, default=str), encoding="utf-8")
            n += 1
    return n


def _lan_ip() -> str:
    # UDP "connect" sends nothing; it just makes the OS pick the outward-facing interface
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "<this-computer's-ip>"


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    settings = Settings.from_env()
    Path(settings.runs_dir).mkdir(parents=True, exist_ok=True)
    mark_interrupted(Path(settings.runs_dir))
    Handler.settings = settings
    Handler.auth = Auth(Path(settings.runs_dir) / "_auth")
    Handler.setup_code = secrets.token_urlsafe(9)
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}"
    print(f"AgentReady: {url}  (Ctrl+C to stop)")
    if host == "0.0.0.0":
        Handler.lan_url = f"http://{_lan_ip()}:{port}"
        print(f"On your phone (same Wi-Fi): open Settings > Connect a phone on this PC and scan the QR code.")
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    if not is_public() and not Handler.auth.has_users():
        print(f"No account yet. Creating it from another device needs this setup code: {Handler.setup_code}")
    if not settings.api_key:
        print("WARNING: NEBIUS_API_KEY is not set; runs cannot start until you add it to .env")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
