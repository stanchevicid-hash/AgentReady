"""HTTP-level checks for sign-in, device pairing, the activity feed and API protection (no network, no model)."""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentready import server  # noqa: E402
from agentready.auth import Auth  # noqa: E402
from agentready.config import Settings  # noqa: E402

PC = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0"
PHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/604.1"


class Client:
    def __init__(self, base, ua):
        self.base, self.ua, self.cookie, self.host = base, ua, "", None

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": self.ua,
                                              "Cookie": self.cookie, **({"Host": self.host} if self.host else {})})
        try:
            with urllib.request.urlopen(req) as r:
                status, headers, raw = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            status, headers, raw = e.code, e.headers, e.read()
            e.close()
        sc = headers.get("Set-Cookie")
        if sc:
            self.cookie = sc.split(";")[0]
        return status, (json.loads(raw) if raw and headers.get("Content-Type", "").startswith("application/json") else raw)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = Path(tempfile.mkdtemp())
        server.Handler.settings = Settings(runs_dir=cls.runs)
        server.Handler.auth = Auth(cls.runs / "_auth")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_sign_in_pair_and_activity(self):
        pc, phone = Client(self.base, PC), Client(self.base, PHONE)
        # locked until there is an account
        self.assertEqual(pc.call("GET", "/api/runs")[0], 401)
        self.assertFalse(pc.call("GET", "/api/auth/status")[1]["has_users"])
        self.assertEqual(pc.call("POST", "/api/auth/setup", {"username": "ana", "password": "short"})[0], 400)
        # a page on another domain rebound to 127.0.0.1 (or another device) can't grab the owner account
        rebound = Client(self.base, PC)
        rebound.host = "evil.example"
        st, r = rebound.call("POST", "/api/auth/setup", {"username": "eve", "password": "correct horse"})
        self.assertEqual(st, 403, r)
        self.assertTrue(rebound.call("GET", "/api/auth/status")[1]["setup_code_needed"])
        self.assertFalse(pc.call("GET", "/api/auth/status")[1]["setup_code_needed"])
        st, me = pc.call("POST", "/api/auth/setup", {"username": "ana", "password": "correct horse"})
        self.assertEqual(st, 200, me)
        self.assertEqual(pc.call("GET", "/api/runs")[0], 200)
        # only one owner account
        self.assertEqual(Client(self.base, PC).call("POST", "/api/auth/setup",
                                                    {"username": "eve", "password": "12345678"})[0], 400)
        self.assertEqual(Client(self.base, PC).call("POST", "/api/auth/login",
                                                    {"username": "ana", "password": "wrong pass"})[0], 401)

        # the PC makes a one-time code, the phone claims it and is signed in
        st, pair = pc.call("POST", "/api/pair", {})
        self.assertEqual(st, 200)
        self.assertIn("/pair?code=", pair["url"])
        self.assertEqual(phone.call("GET", "/api/runs")[0], 401)
        self.assertEqual(phone.call("POST", "/api/auth/claim", {"code": pair["code"]})[0], 200)
        self.assertEqual(phone.call("GET", "/api/runs")[0], 200)
        self.assertEqual(Client(self.base, PHONE).call("POST", "/api/auth/claim", {"code": pair["code"]})[0], 401)

        devices = pc.call("GET", "/api/devices")[1]["devices"]
        names = sorted(d["device"] for d in devices)
        self.assertEqual(names, ["Windows PC · Chrome", "iPhone · Safari"])
        feed = pc.call("GET", "/api/activity?since=0")[1]["items"]
        self.assertTrue(any(a["msg"] == "paired" and a["device"].startswith("iPhone") for a in feed))

        # the PC signs the phone out
        phone_id = next(d["id"] for d in devices if d["device"].startswith("iPhone"))
        self.assertEqual(pc.call("DELETE", f"/api/devices/{phone_id}")[0], 200)
        self.assertEqual(phone.call("GET", "/api/runs")[0], 401)

        # internal folders can't be reached as runs
        (self.runs / "_verify").mkdir(exist_ok=True)
        self.assertEqual(pc.call("DELETE", "/api/runs/_auth")[0], 404)
        self.assertEqual(pc.call("DELETE", "/api/runs/_verify")[0], 404)
        self.assertTrue((self.runs / "_auth" / "auth.json").exists())
        # the app page can't be framed and only loads what it needs
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")
            self.assertIn("frame-ancestors 'none'", r.headers.get("Content-Security-Policy", ""))
        # stopping needs a run that is actually in progress
        self.assertEqual(pc.call("POST", "/api/runs/nope/cancel", {})[0], 409)

        # erase everything: needs the password, then the account, devices and results are gone
        (self.runs / "20990101-000000-abcd").mkdir()
        (self.runs / "20990101-000000-abcd" / "run.json").write_text("{}")
        self.assertEqual(pc.call("POST", "/api/erase", {"password": "nope"})[0], 401)
        self.assertEqual(pc.call("POST", "/api/erase", {"password": "correct horse"})[0], 200)
        self.assertEqual(sorted(p.name for p in self.runs.iterdir()), ["_auth"])
        self.assertFalse(pc.call("GET", "/api/auth/status")[1]["has_users"])
        self.assertEqual(pc.call("GET", "/api/runs")[0], 401)


if __name__ == "__main__":
    unittest.main()
