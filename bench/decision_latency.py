"""Decision latency: Jev vs a small LLM on the *same* four page states.

Replays the saved `agent-browser snapshot -i` fixtures of Selenium's demo web form, asks each
model what to do next, and reports latency and whether the answer was right. No browser needed.

    TYPESAFE_API_KEY=... OPENAI_API_KEY=... python bench/decision_latency.py [runs]

The baseline is any OpenAI-compatible chat endpoint:
    BASELINE_BASE_URL (default https://api.openai.com/v1)   BASELINE_MODEL (default gpt-4o-mini)

This measures the DECISION only. A full agent step also pays for the snapshot and the action,
which are identical for both, so the gap you feel end-to-end is smaller than the gap shown here.
"""

from __future__ import annotations

import http.client
import json
import os
import statistics as st
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hunch.adapters import Jev  # noqa: E402
from hunch.core import Observation, build_request, parse_snapshot  # noqa: E402

GOAL = ("Fill in the text input with the name, fill in the password, write the comment in the textarea, "
        "then submit the form.")
VALUES = ["name", "password", "comment"]
CASES = [  # fixture, actions already taken, expected (operation, target label, value)
    ("form_0_empty.txt", [], ("TYPE_TEXT", "Text input", "name")),
    ("form_1_name.txt", ['typed the name into textbox "Text input"'], ("TYPE_TEXT", "Password", "password")),
    ("form_2_password.txt", ['typed the name into textbox "Text input"', 'typed the password into textbox "Password"'],
     ("TYPE_TEXT", "Textarea", "comment")),
    ("form_3_comment.txt", ['typed the name into textbox "Text input"', 'typed the password into textbox "Password"',
                            'typed the comment into textbox "Textarea"'], ("CLICK", "Submit", None)),
]
FIXTURES = Path(__file__).parent / "fixtures"


def jev_decide(jev, state, questions, clickable, typable):
    answers, _ = jev.ask(state, questions)
    op = answers["operation"]["choice"]
    if op == "CLICK":
        return op, clickable[answers["click_target"]["choice"]], None
    if op == "TYPE_TEXT":
        return op, typable[answers["type_target"]["choice"]], answers["type_value"]["choice"]
    return op, "", None


class ChatBaseline:
    def __init__(self):
        base = urlsplit(os.environ.get("BASELINE_BASE_URL", "https://api.openai.com/v1"))
        self.host, self.path = base.netloc, base.path.rstrip("/") + "/chat/completions"
        self.model = os.environ.get("BASELINE_MODEL", "gpt-4o-mini")
        self.key = os.environ["OPENAI_API_KEY"]
        self.conn = http.client.HTTPSConnection(self.host, timeout=30)   # kept alive, same as Jev

    def decide(self, state, clickable, typable):
        prompt = (state + "\n\nReply with JSON only: {\"operation\": \"CLICK\"|\"TYPE_TEXT\"|\"DONE\", "
                  "\"target\": \"<the [ref] of the element>\", \"value\": \"<name of the value to type, or null>\"}")
        body = json.dumps({"model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": "You drive a web browser one step at a time."},
                                        {"role": "user", "content": prompt}]})
        self.conn.request("POST", self.path, body=body, headers={"Authorization": f"Bearer {self.key}",
                                                                 "Content-Type": "application/json"})
        data = json.loads(self.conn.getresponse().read())
        out = json.loads(data["choices"][0]["message"]["content"])
        ref = str(out.get("target", "")).strip("[]@ ")
        return out.get("operation"), (clickable | typable).get(ref, ref), out.get("value")


def correct(got, want):
    op, label, value = got
    return op == want[0] and f'"{want[1]}"' in (label or "") and (want[2] is None or value == want[2])


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    jev = Jev()
    baseline = ChatBaseline() if os.environ.get("OPENAI_API_KEY") else None
    rows = {"jev": [], "baseline": []}
    hits = {"jev": 0, "baseline": 0}
    total = 0
    for fixture, history, want in CASES:
        raw = (FIXTURES / fixture).read_text()
        obs = Observation(parse_snapshot(raw), url="https://www.selenium.dev/selenium/web/web-form.html",
                          origin="https://www.selenium.dev", title="Web form", raw=raw)
        state, questions, clickable, typable = build_request(GOAL, VALUES, history, obs)
        for _ in range(runs):
            total += 1
            t = time.perf_counter()
            got = jev_decide(jev, state, questions, clickable, typable)
            rows["jev"].append((time.perf_counter() - t) * 1000)
            hits["jev"] += correct(got, want)
            if baseline:
                t = time.perf_counter()
                got = baseline.decide(state, clickable, typable)
                rows["baseline"].append((time.perf_counter() - t) * 1000)
                hits["baseline"] += correct(got, want)

    def line(name, key):
        ms = sorted(rows[key])
        return (f"{name:22} median {st.median(ms):6.0f} ms   p90 {ms[int(len(ms) * 0.9) - 1]:6.0f} ms   "
                f"correct {hits[key]}/{total}")

    print(f"{len(CASES)} page states x {runs} runs, one warm connection each\n")
    print(line(f"Jev ({jev.model})", "jev"))
    if baseline:
        print(line(baseline.model, "baseline"))
        print(f"\nJev is {st.median(rows['baseline']) / st.median(rows['jev']):.1f}x faster per decision on this task.")
    else:
        print("(set OPENAI_API_KEY to add the LLM baseline)")


if __name__ == "__main__":
    main()
