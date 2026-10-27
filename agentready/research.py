"""Tavily web research: grounds the improved AGENTS.md in the official docs of the
tools and libraries the repository actually uses."""

from __future__ import annotations

import json
import urllib.request

TAVILY_URL = "https://api.tavily.com/search"


class TavilyClient:
    def __init__(self, api_key: str, timeout: float = 30):
        self.api_key = api_key
        self.timeout = timeout
        self.calls = 0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, max_results: int = 3) -> list[dict]:
        if not self.enabled:
            return []
        payload = {
            "api_key": self.api_key,  # accepted for backwards compatibility
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
        }
        req = urllib.request.Request(
            TAVILY_URL,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.calls += 1
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "content": (r.get("content") or "")[:600]}
            for r in data.get("results", [])
        ]


def research_tooling(client: TavilyClient, tooling: dict, max_topics: int = 4) -> list[dict]:
    """Look up official docs for the test runner and the main dependencies."""
    if not client.enabled:
        return []
    topics: list[str] = []
    runner = tooling.get("test_runner")
    if runner:
        topics.append(f"{runner} official documentation run a single test from the command line")
    for dep in tooling.get("dependencies", [])[: max_topics - len(topics)]:
        topics.append(f"{dep} python library official documentation")
    findings = []
    for topic in topics[:max_topics]:
        try:
            for hit in client.search(topic, max_results=2):
                findings.append({"topic": topic, **hit})
        except Exception as exc:  # research is best-effort, never fatal
            findings.append({"topic": topic, "title": "search failed", "url": "", "content": str(exc)[:200]})
    return findings
