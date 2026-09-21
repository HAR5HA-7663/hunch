"""The two things hunch talks to: the agent-browser CLI and the Jev API. Both are small on
purpose, and both are replaced by fakes in the test-suite."""

from __future__ import annotations

import http.client
import json
import os
import shlex
import subprocess
import time

from .core import PAGE_TEXT_CHARS, Escalate, Observation, parse_snapshot

JEV_HOST, JEV_PATH = "api.typesafe.ai", "/v1/systemone"
DEFAULT_MODEL = "jev-1.13.0"  # pinned: "jev-latest" floats and can change decisions under you


class AgentBrowser:
    """Thin wrapper over `agent-browser --session NAME ...`."""

    def __init__(self, session: str, page_text: bool = True):
        self.session, self.page_text = session, page_text

    def _run(self, *args: str, timeout: int = 60) -> str:
        p = subprocess.run(["agent-browser", "--session", self.session, *args],
                           capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip()

    def open(self, url: str) -> None:
        self._run("open", url)

    def observe(self) -> Observation:
        started = time.perf_counter()
        snap = self._run("snapshot", "-i")
        chars = PAGE_TEXT_CHARS if self.page_text else 0
        js = ("JSON.stringify({u: location.origin + location.pathname, o: location.origin, t: document.title, "
              f"x: (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').slice(0, {chars})}})")
        out = self._run("eval", js)
        try:
            info = json.loads(out)
            if isinstance(info, str):  # some versions print the JSON string quoted
                info = json.loads(info)
        except ValueError:
            info = {}
        # The query string is dropped on purpose: it is where reset tokens and session ids live.
        return Observation(parse_snapshot(snap), url=info.get("u", ""), origin=info.get("o", ""),
                           title=info.get("t", ""), text=info.get("x", ""), raw=snap,
                           ms=round((time.perf_counter() - started) * 1000))

    def click(self, ref: str) -> None:
        self._run("click", f"@{ref}")

    def fill(self, ref: str, value: str) -> None:
        self._run("fill", f"@{ref}", value)

    def scroll_down(self) -> None:
        self._run("scroll", "down", "600")


class Jev:
    """Stdlib-only client with one kept-alive connection (saves a TLS handshake per step)."""

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL, timeout: float = 4.0):
        self.key = api_key or resolve_key()
        self.model, self.timeout, self.conn = model, timeout, None

    def ask(self, state: str, questions: dict) -> tuple[dict, dict]:
        body = json.dumps({"model": self.model, "state": state, "questions": questions})
        for attempt in (1, 2):  # one reconnect: the server may have closed an idle socket
            try:
                if self.conn is None:
                    self.conn = http.client.HTTPSConnection(JEV_HOST, timeout=self.timeout)
                self.conn.request("POST", JEV_PATH, body=body, headers={
                    "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
                resp = self.conn.getresponse()
                raw = resp.read()
                if resp.status != 200:
                    raise Escalate("jev_error", status=resp.status, body=raw[:200].decode(errors="replace"))
                data = json.loads(raw)
                return data["answers"], data.get("usage", {})
            except (OSError, http.client.HTTPException, ValueError, KeyError) as e:
                self.conn = None
                if attempt == 2:
                    raise Escalate("jev_error", error=f"{type(e).__name__}: {e}") from e
        raise AssertionError("unreachable")


def resolve_key() -> str:
    """TYPESAFE_API_KEY, or the output of HUNCH_KEY_CMD (so the key can stay in a vault)."""
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    cmd = os.environ.get("HUNCH_KEY_CMD", "").strip()
    if not key and cmd:
        try:
            key = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            key = ""
    if not key:
        raise SystemExit("hunch: set TYPESAFE_API_KEY (get one at https://console.typesafe.ai/keys) "
                         "or HUNCH_KEY_CMD to a command that prints it")
    return key
