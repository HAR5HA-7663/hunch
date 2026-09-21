"""hunch — a browser agent that acts on a hunch and asks for help when the hunch is weak.

  hunch --url https://www.selenium.dev/selenium/web/web-form.html \\
        --goal "Fill in the text input with the name and the password, then submit the form." \\
        --value name=literal:Ada --value password=env:DEMO_PASSWORD \\
        --done-text "Received!"

Each step is ONE request to Jev (TypeSafe's System One model): it picks the operation and
the target together in ~150 ms. The action runs through agent-browser. Success is checked
in code. If Jev is unsure, the page stops changing, or a click looks irreversible, hunch
stops and prints a report so a human or an LLM can take over.

exit codes: 0 goal verified · 2 escalated (see "reason") · 3 done but unverified · 1 error
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .adapters import DEFAULT_MODEL, AgentBrowser, Jev
from .agent import Agent, Config


def parse_values(specs: list[str]) -> dict[str, str]:
    values = {}
    for spec in specs:
        name, sep, src = spec.partition("=")
        kind, _, ref = src.partition(":")
        if not sep or not name or kind not in ("env", "literal"):
            raise SystemExit(f"hunch: --value wants NAME=env:VAR or NAME=literal:text, got {spec!r}")
        if kind == "env":
            if ref not in os.environ:
                raise SystemExit(f"hunch: environment variable {ref} (for value '{name}') is not set")
            values[name] = os.environ[ref]
        else:
            values[name] = ref
    return values


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="hunch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--goal", required=True, help="what to achieve, in plain language")
    ap.add_argument("--url", help="page to open first (otherwise the session's current page is used)")
    ap.add_argument("--value", action="append", default=[], metavar="NAME=env:VAR|NAME=literal:TEXT",
                    help="something hunch may type. Only the NAME is sent to Jev, never the text")
    ap.add_argument("--done-url", metavar="REGEX", help="success = the URL matches this")
    ap.add_argument("--done-text", metavar="TEXT", help="success = the page shows this text")
    ap.add_argument("--allow-origin", action="append", default=[], metavar="ORIGIN",
                    help="refuse to act anywhere else (default: the origin of --url)")
    ap.add_argument("--session", default=os.environ.get("AGENT_BROWSER_SESSION", "hunch"), help="agent-browser session name")
    ap.add_argument("--min-conf", type=float, default=0.75, help="below this confidence, escalate instead of acting")
    ap.add_argument("--max-steps", type=int, default=15)
    ap.add_argument("--settle-ms", type=int, default=2500, help="how long to watch for a page change after an action")
    ap.add_argument("--wait-ms", type=int, default=6000, help="how long one WAIT watches a loading page")
    ap.add_argument("--max-waits", type=int, default=3)
    ap.add_argument("--allow-risky", action="store_true", help="permit clicks the guard considers irreversible")
    ap.add_argument("--dry-run", action="store_true", help="decide for the current page, act on nothing")
    ap.add_argument("--no-page-text", action="store_true", help="send only the element table, no visible page text")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--quiet", action="store_true", help="print only the final JSON summary")
    ap.add_argument("--version", action="version", version=f"hunch {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config(goal=args.goal, values=parse_values(args.value), url=args.url,
                    allow_origins=set(args.allow_origin), done_url=args.done_url, done_text=args.done_text,
                    max_steps=args.max_steps, min_conf=args.min_conf, settle_ms=args.settle_ms,
                    wait_ms=args.wait_ms, max_waits=args.max_waits, allow_risky=args.allow_risky, dry_run=args.dry_run)
    on_step = None if args.quiet else (lambda row: print(json.dumps(row), flush=True))
    agent = Agent(AgentBrowser(args.session, page_text=not args.no_page_text), Jev(model=args.model), config, on_step)
    result = agent.run()
    print(json.dumps(result.as_dict(), indent=None if args.quiet else 2))
    return result.code


if __name__ == "__main__":
    sys.exit(main())
