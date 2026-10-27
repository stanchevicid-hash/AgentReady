"""HTTP-level tests for the Token Factory and Tavily clients against a local mock server."""

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentready import research  # noqa: E402
from agentready.config import Settings  # noqa: E402
from agentready.llm import BILLING, LLMError, TokenFactoryLLM  # noqa: E402

SEEN = []


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        SEEN.append(("GET", self.path, self.headers.get("Authorization")))
        self._send({"data": [{"id": "nvidia/nemotron-3-super-120b-a12b"}, {"id": "nvidia/Nemotron-3-Nano-30B-A3B"},
                             {"id": "meta-llama/Llama-3.3-70B-Instruct"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(("POST", self.path, body))
        if body.get("model") == "broke":  # an account with no credits left
            self.send_response(402)
            self.send_header("Content-Length", "2")
            self.end_headers()
            return self.wfile.write(b"{}")
        if self.path == "/search":
            return self._send({"results": [{"title": "unittest docs", "url": "https://docs.python.org/3/library/unittest.html",
                                            "content": "Command-Line Interface"}]})
        self._send({"choices": [{"message": {"content": "<think>pondering</think>{\"ok\": true}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7}})


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_token_factory_roles_and_usage(self):
        s = Settings(api_key="k", base_url=self.base,
                     models={"nano": "nvidia/nemotron-3-nano-30b-a3b", "super": "nvidia/nemotron-3-super-120b-a12b",
                             "ultra": "nvidia/nemotron-3-ultra"})
        llm = TokenFactoryLLM(s)
        models = llm.resolve_models()
        self.assertEqual(models["nano"], "nvidia/Nemotron-3-Nano-30B-A3B")  # case-insensitive match
        self.assertEqual(models["ultra"], "nvidia/nemotron-3-super-120b-a12b")  # documented fallback
        self.assertTrue(any("ultra" in n for n in llm.resolution_notes))
        self.assertEqual(llm.chat_json("nano", [{"role": "user", "content": "hi"}]), {"ok": True})
        self.assertEqual(llm.usage.snapshot()["nano"]["prompt_tokens"], 11)
        post = [x for x in SEEN if x[0] == "POST" and x[1] == "/chat/completions"][-1][2]
        self.assertEqual(post["model"], "nvidia/Nemotron-3-Nano-30B-A3B")
        self.assertIn(("GET", "/models", "Bearer k"), SEEN)

    def test_out_of_credits_is_reported_once(self):
        llm = TokenFactoryLLM(Settings(api_key="k", base_url=self.base, models={"nano": "broke"}))
        before = len(SEEN)
        with self.assertRaisesRegex(LLMError, "out of credits"):
            llm.chat("nano", [{"role": "user", "content": "hi"}])
        self.assertEqual(len(SEEN) - before, 1)  # not retried
        self.assertTrue(BILLING["out_of_credits"])
        llm.models["nano"] = "nvidia/Nemotron-3-Nano-30B-A3B"
        llm.chat("nano", [{"role": "user", "content": "hi"}])
        self.assertFalse(BILLING["out_of_credits"])  # a call that works clears it

    def test_tavily(self):
        old = research.TAVILY_URL
        research.TAVILY_URL = self.base + "/search"
        try:
            c = research.TavilyClient("tv")
            hits = research.research_tooling(c, {"test_runner": "unittest", "dependencies": []})
            self.assertEqual(hits[0]["url"], "https://docs.python.org/3/library/unittest.html")
            self.assertEqual(c.calls, 1)
        finally:
            research.TAVILY_URL = old


if __name__ == "__main__":
    unittest.main()
