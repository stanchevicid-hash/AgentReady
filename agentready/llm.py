"""Nebius Token Factory client (OpenAI-compatible) with per-role Nemotron routing.

Standard library only, so AgentReady runs anywhere Python 3.9+ runs, including
inside a Nebius Serverless Job container.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .config import Settings


class LLMError(RuntimeError):
    pass


@dataclass
class RoleUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    errors: int = 0

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "seconds": round(self.seconds, 2),
            "errors": self.errors,
        }


@dataclass
class UsageTracker:
    roles: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, role: str, prompt: int, completion: int, seconds: float, error: bool = False) -> None:
        with self._lock:
            u = self.roles.setdefault(role, RoleUsage())
            u.calls += 1
            u.prompt_tokens += prompt
            u.completion_tokens += completion
            u.seconds += seconds
            if error:
                u.errors += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {r: u.to_dict() for r, u in self.roles.items()}


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    text = _THINK_RE.sub("", text or "")
    # Some reasoning models emit an unterminated think block when truncated.
    if "<think>" in text.lower() and "</think>" not in text.lower():
        text = text[: text.lower().index("<think>")]
    return text.strip()


def extract_json(text: str):
    """Pull the first JSON object/array out of a model reply (handles ```json fences)."""
    text = strip_reasoning(text)
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidates = [fence.group(1)] if fence else []
    candidates.append(text)
    for cand in candidates:
        cand = cand.strip()
        for opener, closer in (("{", "}"), ("[", "]")):
            start = cand.find(opener)
            while start != -1:
                depth, in_str, esc = 0, False, False
                for i in range(start, len(cand)):
                    ch = cand[i]
                    if in_str:
                        if esc:
                            esc = False
                        elif ch == "\\":
                            esc = True
                        elif ch == '"':
                            in_str = False
                        continue
                    if ch == '"':
                        in_str = True
                    elif ch == opener:
                        depth += 1
                    elif ch == closer:
                        depth -= 1
                        if depth == 0:
                            try:
                                return json.loads(cand[start : i + 1])
                            except json.JSONDecodeError:
                                break
                start = cand.find(opener, start + 1)
    raise LLMError("model reply did not contain valid JSON")


class TokenFactoryLLM:
    """Routes each call to the Nemotron model assigned to its role (nano/super/ultra)."""

    def __init__(self, settings: Settings, usage: UsageTracker | None = None, timeout: float = 180):
        if not settings.api_key:
            raise LLMError(
                "NEBIUS_API_KEY is not set. Create a key at https://tokenfactory.nebius.com "
                "and put it in .env (see .env.example)."
            )
        self.settings = settings
        self.usage = usage or UsageTracker()
        self.timeout = timeout
        self.models = dict(settings.models)
        self.resolution_notes: list[str] = []

    # ---------------------------------------------------------------- HTTP
    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.settings.base_url}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def list_models(self) -> list[str]:
        data = self._request("GET", "/models")
        return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))

    def resolve_models(self) -> dict:
        """Make sure every role points at a model Token Factory actually serves."""
        try:
            available = self.list_models()
        except Exception as exc:  # network issues: keep configured IDs
            self.resolution_notes.append(f"could not list models ({exc}); using configured IDs")
            return self.models
        avail_lower = {m.lower(): m for m in available}
        nemotrons = [m for m in available if "nemotron" in m.lower()]
        for role in ("super", "nano", "ultra"):
            wanted = self.models.get(role, "")
            if wanted.lower() in avail_lower:
                self.models[role] = avail_lower[wanted.lower()]
                continue
            matches = [m for m in nemotrons if role in m.lower()]
            matches.sort(key=lambda m: ("nemotron-3" not in m.lower(), m.endswith("-fast"), m))
            if matches:
                self.resolution_notes.append(f"{role}: '{wanted}' not served, using '{matches[0]}'")
                self.models[role] = matches[0]
            else:
                fallback = {"ultra": "super", "nano": "super"}.get(role)
                if fallback and self.models.get(fallback) in available:
                    self.resolution_notes.append(
                        f"{role}: no Nemotron {role} model served, falling back to {fallback} "
                        f"('{self.models[fallback]}')"
                    )
                    self.models[role] = self.models[fallback]
                else:
                    self.resolution_notes.append(f"{role}: '{wanted}' not found in model list")
        return self.models

    # ---------------------------------------------------------------- chat
    def chat(
        self,
        role: str,
        messages: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.2,
        retries: int = 3,
    ) -> str:
        return self.chat_with_usage(role, messages, max_tokens, temperature, retries)[0]

    def chat_with_usage(
        self,
        role: str,
        messages: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.2,
        retries: int = 3,
    ) -> tuple[str, int, int]:
        model = self.models[role]
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            **self.settings.extra_body,
        }
        last_err: Exception | None = None
        for attempt in range(retries):
            started = time.time()
            try:
                data = self._request("POST", "/chat/completions", payload)
                usage = data.get("usage") or {}
                pt, ct = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
                self.usage.add(role, pt, ct, time.time() - started)
                choice = (data.get("choices") or [{}])[0]
                content = strip_reasoning((choice.get("message") or {}).get("content") or "")
                if not content and choice.get("finish_reason") == "length" and attempt < retries - 1:
                    # A reasoning model spent the whole budget thinking: give it more room once.
                    payload["max_tokens"] = min(int(payload["max_tokens"]) * 2, 16000)
                    continue
                return content, pt, ct
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")[:500]
                hint = {401: "the NEBIUS_API_KEY is not valid",
                        402: "the Token Factory account is out of credits: top up at tokenfactory.nebius.com"}.get(exc.code)
                last_err = LLMError(f"{model}: HTTP {exc.code}: {hint or body}")
                self.usage.add(role, 0, 0, time.time() - started, error=True)
                if exc.code in (400, 401, 402, 403, 404):  # retrying can't fix these
                    break
            except Exception as exc:  # timeouts, connection resets
                last_err = LLMError(f"{model}: {exc}")
                self.usage.add(role, 0, 0, time.time() - started, error=True)
            time.sleep(1.5 * (attempt + 1))
        raise last_err or LLMError("unknown LLM error")

    def chat_json(self, role: str, messages: list[dict], max_tokens: int = 2048):
        reply = self.chat(role, messages, max_tokens=max_tokens, temperature=0.1)
        try:
            return extract_json(reply)
        except LLMError:
            retry = messages + [
                {"role": "assistant", "content": reply[:4000]},
                {"role": "user", "content": "That was not valid JSON. Reply with ONLY the JSON, nothing else."},
            ]
            return extract_json(self.chat(role, retry, max_tokens=max_tokens, temperature=0.0))
