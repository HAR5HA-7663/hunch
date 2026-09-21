"""The step loop: observe → one Jev request → act → verify in code → repeat, or escalate."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterator
from urllib.parse import urlsplit

from . import core
from .core import Escalate, Observation

# exit codes
DONE_VERIFIED, ERROR, ESCALATE, DONE_UNVERIFIED = 0, 1, 2, 3


@dataclass
class Config:
    goal: str
    values: dict[str, str] = field(default_factory=dict)  # name -> text to type. Only names reach Jev.
    url: str | None = None
    allow_origins: set[str] = field(default_factory=set)
    done_url: str | None = None
    done_text: str | None = None
    max_steps: int = 15
    min_conf: float = 0.75
    settle_ms: int = 2500
    wait_ms: int = 6000
    max_waits: int = 3
    allow_risky: bool = False
    dry_run: bool = False


@dataclass
class Result:
    status: str
    code: int
    steps: list[dict]
    history: list[str]
    jev_calls: int
    seconds: float
    final_url: str
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        totals = {k: sum(s.get(k, 0) for s in self.steps) for k in ("observe_ms", "jev_ms", "action_ms")}
        return {"status": self.status, "steps": len(self.steps), "jev_calls": self.jev_calls,
                "seconds": self.seconds, "totals_ms": totals, "final_url": self.final_url,
                "history": self.history, **self.extra}


class Agent:
    """`browser` needs open/observe/click/fill/scroll_down; `jev` needs ask(state, questions)."""

    def __init__(self, browser, jev, config: Config, on_step: Callable[[dict], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.browser, self.jev, self.cfg = browser, jev, config
        self.on_step = on_step or (lambda row: None)
        self.sleep = sleep

    # -- helpers ---------------------------------------------------------------------------
    def _verified(self, obs: Observation):
        return core.verified(obs, self.cfg.done_url, self.cfg.done_text)

    def _settle(self, before: str, budget_ms: int, stop_when_verified: bool = False) -> tuple[Observation, bool]:
        """Watch for the page to change after an action (or, while waiting, for success)."""
        deadline = time.perf_counter() + budget_ms / 1000
        obs = self.browser.observe()
        while (obs.fingerprint == before and not (stop_when_verified and self._verified(obs))
               and time.perf_counter() < deadline):
            self.sleep(0.2)
            obs = self.browser.observe()
        return obs, obs.fingerprint != before

    # -- the loop --------------------------------------------------------------------------
    def run(self) -> Result:
        cfg, started = self.cfg, time.perf_counter()
        history: list[str] = []
        steps: list[dict] = []
        calls = idle = waits = 0

        if cfg.url:
            self.browser.open(cfg.url)
        obs = self.browser.observe()
        allowed = set(cfg.allow_origins)
        if not allowed:
            allowed = {"{0.scheme}://{0.netloc}".format(urlsplit(cfg.url))} if cfg.url else {obs.origin}

        def finish(status: str, code: int, **extra) -> Result:
            return Result(status, code, steps, history, calls, round(time.perf_counter() - started, 2), obs.url, extra)

        try:
            for n in range(1, cfg.max_steps + 1):
                if obs.origin not in allowed:
                    raise Escalate("left_allowed_origin", origin=obs.origin, allowed=sorted(allowed))
                if self._verified(obs):
                    return finish("done_verified", DONE_VERIFIED)

                state, questions, clickable, typable = core.build_request(cfg.goal, list(cfg.values), history, obs)
                t = time.perf_counter()
                answers, usage = self.jev.ask(state, questions)
                calls += 1
                op = answers["operation"]
                row = {"step": n, "t_ms": round((time.perf_counter() - started) * 1000), "observe_ms": obs.ms,
                       "jev_ms": round((time.perf_counter() - t) * 1000), "tokens": usage.get("input_tokens"),
                       "elements": len(obs.elements), "op_conf": round(core.confidence(op), 2)}
                try:
                    choice = core.settle_operation(op, self._verified(obs), cfg.min_conf)
                except Escalate as e:
                    e.detail["step"] = n
                    raise
                row["op"] = choice
                label = None
                t = time.perf_counter()

                if choice == "CLICK":
                    tgt = answers["click_target"]
                    core.require_confident("click_target", tgt, cfg.min_conf, clickable)
                    label = clickable[tgt["choice"]]
                    p_irrev = float((answers.get("irreversible") or {}).get("noul", 0))
                    core.guard_click(label, p_irrev, cfg.allow_risky)
                    row.update(target=label, target_conf=round(core.confidence(tgt), 2), p_irreversible=round(p_irrev, 2))
                    if not cfg.dry_run:
                        self.browser.click(tgt["choice"])
                        history.append(f"clicked {label}")
                elif choice == "TYPE_TEXT":
                    tgt, val = answers["type_target"], answers["type_value"]
                    core.require_confident("type_target", tgt, cfg.min_conf, typable)
                    core.require_confident("type_value", val, cfg.min_conf, {})
                    field_name = typable[tgt["choice"]].split(" · ")[0]
                    label = f"{field_name} <- {val['choice']}"
                    row.update(target=label, target_conf=round(min(core.confidence(tgt), core.confidence(val)), 2))
                    if not cfg.dry_run:
                        self.browser.fill(tgt["choice"], cfg.values[val["choice"]])
                        history.append(f"typed the {val['choice']} into {field_name}")
                elif choice == "SCROLL_DOWN":
                    if not cfg.dry_run:
                        self.browser.scroll_down()
                        history.append("scrolled down")
                elif choice == "WAIT":
                    if not cfg.dry_run:
                        waits += 1
                        if waits > cfg.max_waits:
                            raise Escalate("stuck_waiting", step=n, waited_ms=cfg.wait_ms * cfg.max_waits,
                                           jev_top=op["choice"])
                        obs, changed = self._settle(obs.fingerprint, cfg.wait_ms, stop_when_verified=True)
                        row.update(action_ms=round((time.perf_counter() - t) * 1000), page_changed=changed)
                        steps.append(row)
                        self.on_step(row)
                        continue
                elif choice == "DONE":
                    steps.append(row)
                    self.on_step(row)
                    if self._verified(obs) is False:
                        raise Escalate("done_not_verified", step=n, operation_candidates=core.top(op),
                                       note="Jev says the goal is done but the verifier disagrees")
                    return finish("done_unverified", DONE_UNVERIFIED,
                                  note="Jev says done; pass --done-url / --done-text to have it confirmed in code")
                elif choice == "BLOCKED":
                    steps.append(row)
                    self.on_step(row)
                    raise Escalate("blocked", step=n)

                row["action_ms"] = round((time.perf_counter() - t) * 1000)
                waits = 0
                if cfg.dry_run:
                    steps.append(row)
                    self.on_step(row)
                    return finish("dry_run", DONE_VERIFIED, decision=row, operation_candidates=core.top(op))

                obs, changed = self._settle(obs.fingerprint, cfg.settle_ms)
                row["page_changed"] = changed
                steps.append(row)
                self.on_step(row)
                idle = 0 if changed else idle + 1
                if not changed and history:
                    history[-1] += " — this had NO visible effect"
                if idle >= 2:
                    raise Escalate("no_progress", step=n, last_target=label,
                                   note="two actions in a row changed nothing on the page")

            if self._verified(obs):
                return finish("done_verified", DONE_VERIFIED)
            raise Escalate("step_limit", max_steps=cfg.max_steps)
        except Escalate as e:
            return finish("escalate", ESCALATE, reason=e.reason, detail=e.detail, page_title=obs.title,
                          visible_text=obs.text[:400], elements=[f"[{x.ref}] {x.describe()}" for x in obs.elements[:40]])

    def iter_steps(self) -> Iterator[dict]:  # convenience for library users
        rows: list[dict] = []
        self.on_step = rows.append
        result = self.run()
        yield from rows
        yield result.as_dict()
