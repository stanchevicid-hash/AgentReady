"""Login, sessions and device pairing (stdlib only).

Accounts are emails (or plain usernames). The first account owns the server; more people join on
this computer or with a one-time invite from an existing account (or freely when sign-up is open).
Each account sees only its own runs. Every browser that signs in is a "device"; a signed-in device can
show a one-time pairing code (as a QR) that signs another device (a phone) into the same account.
Session tokens are only stored hashed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path

PBKDF2_ROUNDS = 200_000
SESSION_DAYS = 30
PAIR_SECONDS = 300
INVITE_SECONDS = 24 * 3600
MAX_FAILS, LOCK_SECONDS = 8, 300


def _hash_pw(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS).hex()


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def device_name(user_agent: str) -> str:
    ua = user_agent or ""
    kind = ("iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Android phone" if "Android" in ua
            else "Windows PC" if "Windows" in ua else "Mac" if "Macintosh" in ua else "Linux PC" if "Linux" in ua
            else "Device")
    browser = ("Edge" if "Edg/" in ua else "Chrome" if "Chrome/" in ua else "Firefox" if "Firefox/" in ua
               else "Safari" if "Safari/" in ua else "")
    return f"{kind} · {browser}" if browser else kind


class Auth:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.file = self.root / "auth.json"
        self._lock = threading.Lock()
        self._pair: dict[str, dict] = {}       # code -> {user, expires}
        self._invites: dict[str, dict] = {}    # code -> {by, expires}
        self._fails: dict[str, list] = {}      # ip -> [count, locked_until]
        self.data = json.loads(self.file.read_text(encoding="utf-8")) if self.file.exists() else \
            {"users": {}, "sessions": {}}

    def _save(self) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        tmp.replace(self.file)

    # ------------------------------------------------------------ account
    def has_users(self) -> bool:
        return bool(self.data["users"])

    def owner(self) -> str:
        """The first account. Runs saved before accounts were separate belong to it."""
        return self.data.get("owner") or next(iter(self.data["users"]), "")

    def _find(self, username: str) -> str | None:
        name = (username or "").strip().lower()
        return next((u for u in self.data["users"] if u.lower() == name), None)

    def _add_user(self, username: str, password: str) -> str:
        username = (username or "").strip().lower()
        if not re.fullmatch(r"[\w.+@-]{2,80}", username):
            raise ValueError("enter an email (or a username: letters, digits, . _ - + @)")
        if len(password or "") < 8:
            raise ValueError("password must be at least 8 characters")
        if self._find(username):
            raise ValueError("an account with this email already exists; sign in instead")
        salt = secrets.token_bytes(16)
        self.data["users"][username] = {"salt": salt.hex(), "hash": _hash_pw(password, salt), "created": time.time()}
        return username

    def setup(self, username: str, password: str) -> str:
        with self._lock:
            if self.data["users"]:
                raise ValueError("an account already exists; sign in instead")
            user = self._add_user(username, password)
            self.data["owner"] = user
            self._save()
            return user

    def signup(self, username: str, password: str, invite: str = "", allowed: bool = False) -> str:
        """A new, separate account. Needs a valid one-time invite unless the server allows sign-up."""
        with self._lock:
            if not self.data["users"]:
                raise ValueError("no owner yet: create the first account")
            inv = self._invites.get(invite or "")
            if not allowed and (not inv or inv["expires"] < time.time()):
                raise PermissionError("you need an invite: ask someone with an account for an invite link")
            user = self._add_user(username, password)
            if not allowed:
                self._invites.pop(invite, None)  # one use
            self._save()
            return user

    def invite_code(self, by: str) -> str:
        code = secrets.token_urlsafe(12)
        with self._lock:
            now = time.time()
            self._invites = {c: i for c, i in self._invites.items() if i["expires"] > now}
            self._invites[code] = {"by": by, "expires": now + INVITE_SECONDS}
        return code

    # ------------------------------------------------------------ sessions
    def _new_session(self, user: str, ua: str, via: str) -> str:
        token = secrets.token_urlsafe(32)
        self.data["sessions"][_h(token)] = {"id": secrets.token_hex(4), "user": user, "device": device_name(ua),
                                            "via": via, "created": time.time(), "seen": time.time()}
        self._save()
        return token

    def _check(self, username: str, password: str, ip: str) -> bool:
        """Password check with a per-IP lockout; call with the lock held."""
        count, until = self._fails.get(ip, [0, 0])
        if until > time.time():
            raise PermissionError(f"too many attempts, try again in {int(until - time.time())} s")
        found = self._find(username)
        u = self.data["users"].get(found) if found else None
        # hash even for an unknown name, so response time doesn't tell which usernames exist
        salt = bytes.fromhex(u["salt"]) if u else b"\0" * 16
        ok = hmac.compare_digest(u["hash"] if u else "", _hash_pw(password or "", salt)) and u is not None
        if not ok:
            count += 1
            self._fails[ip] = [0, time.time() + LOCK_SECONDS] if count >= MAX_FAILS else [count, 0]
            return False
        self._fails.pop(ip, None)
        return True

    def login(self, username: str, password: str, ua: str, ip: str) -> str:
        with self._lock:
            if not self._check(username, password, ip):
                raise PermissionError("wrong email or password")
            return self._new_session(self._find(username), ua, "password")

    def session(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self._lock:
            s = self.data["sessions"].get(_h(token))
            if not s:
                return None
            now = time.time()
            if now - s["seen"] > SESSION_DAYS * 86400:
                del self.data["sessions"][_h(token)]
                self._save()
                return None
            if now - s["seen"] > 60:  # don't rewrite the file on every request
                s["seen"] = now
                self._save()
            return dict(s)

    def logout(self, token: str) -> None:
        with self._lock:
            if self.data["sessions"].pop(_h(token or ""), None):
                self._save()

    def devices(self, user: str) -> list[dict]:
        with self._lock:
            return sorted((dict(s) for s in self.data["sessions"].values() if s["user"] == user),
                          key=lambda s: -s["seen"])

    def revoke(self, user: str, device_id: str) -> bool:
        with self._lock:
            for k, s in list(self.data["sessions"].items()):
                if s["user"] == user and s["id"] == device_id:
                    del self.data["sessions"][k]
                    self._save()
                    return True
        return False

    def erase(self, user: str, password: str, ip: str) -> None:
        """Delete this account and its sessions (the right to have your data erased). Others stay."""
        with self._lock:
            if not self._check(user, password, ip):
                raise PermissionError("wrong password")
            was_owner = self.owner() == user
            self.data["users"].pop(user, None)
            self.data["sessions"] = {k: s for k, s in self.data["sessions"].items() if s["user"] != user}
            self._pair = {c: p for c, p in self._pair.items() if p["user"] != user}
            self._invites = {c: i for c, i in self._invites.items() if i["by"] != user}
            if was_owner:
                self.data["owner"] = next(iter(self.data["users"]), "")
            self._save()

    # ------------------------------------------------------------ pairing
    def pair_code(self, user: str) -> str:
        code = secrets.token_urlsafe(9)
        with self._lock:
            now = time.time()
            self._pair = {c: p for c, p in self._pair.items() if p["expires"] > now}
            self._pair[code] = {"user": user, "expires": now + PAIR_SECONDS}
        return code

    def claim(self, code: str, ua: str) -> str:
        with self._lock:
            p = self._pair.pop(code or "", None)  # one use only
            if not p or p["expires"] < time.time():
                raise PermissionError("this pairing code expired or was already used; make a new one")
            return self._new_session(p["user"], ua, "paired")
