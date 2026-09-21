"""Pure decision logic: snapshot parsing, the Jev request, guards and verification.

Nothing in this module touches the network or the browser, so all of it is unit-tested.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

CLICKABLE = {"button", "link", "checkbox", "radio", "switch", "tab", "menuitem", "option", "combobox"}
TYPABLE = {"textbox", "searchbox", "spinbutton"}
MAX_ELEMENTS = 120
PAGE_TEXT_CHARS = 800

# Words that make a click worth a second opinion. Deliberately broad: a false stop costs
# one LLM turn, a false go can cost a deleted account.
RISKY = re.compile(
    r"\b(delete|remove|erase|pay|purchase|buy now|place order|checkout|send|publish|post|"
    r"cancel|deactivate|transfer|revoke|unsubscribe|terminate|wipe|reset)\b",
    re.I,
)
_LINE = re.compile(r'^\s*-\s+(\w+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\[([^\]]*)\](.*)$')

OPS = {
    "CLICK": "The next thing to do is click a button, link or other control.",
    "TYPE_TEXT": "The next thing to do is type one of the available values into a text field that is still empty.",
    "SCROLL_DOWN": "The element needed next is probably further down the page and is not in the list.",
    "WAIT": "The page is still loading or processing the last action; nothing should be clicked yet.",
    "DONE": "Everything the GOAL asks for has already been achieved, judging by the current page.",
    "BLOCKED": "Nothing on this page can move the GOAL forward, or an error / verification step needs a human.",
}
PASSIVE = {"DONE", "WAIT"}  # neither touches the page


class Escalate(Exception):
    """Stop and hand the task back to a human or an LLM, with the reason and the evidence."""

    def __init__(self, reason: str, **detail):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


@dataclass
class Element:
    ref: str
    role: str
    label: str
    filled: bool = False
    disabled: bool = False

    def describe(self) -> str:
        state = (" · filled" if self.filled else " · empty") if self.role in TYPABLE else ""
        return f'{self.role} "{self.label}"{state}{" · disabled" if self.disabled else ""}'


@dataclass
class Observation:
    elements: list[Element]
    url: str = ""
    origin: str = ""
    title: str = ""
    text: str = ""
    raw: str = ""
    ms: int = 0
    fingerprint: str = field(default="", init=False)

    def __post_init__(self):
        self.fingerprint = hashlib.sha1(f"{self.url}\n{self.raw}\n{self.text}".encode()).hexdigest()


def parse_snapshot(snapshot: str) -> list[Element]:
    """Interactive elements from `agent-browser snapshot -i`, in document order.

    Order matters: Jev reads adjacency. A product-name row followed by an "Add to cart" row
    is how it tells six identical buttons apart. Field *contents* are reduced to
    filled/empty so that typed values never travel to the model.
    """
    out: list[Element] = []
    for line in snapshot.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        role, label, attrs, tail = m.group(1), (m.group(2) or "").strip(), m.group(3), m.group(4)
        ref = re.search(r"\bref=(e\d+)", attrs)
        if not ref or (role not in CLICKABLE and role not in TYPABLE):
            continue
        out.append(Element(ref.group(1), role, label[:80] or "(no label)",
                           filled=bool(re.match(r"\s*:\s*\S", tail)), disabled="disabled" in attrs))
    return out[:MAX_ELEMENTS]


def build_request(goal: str, value_names: list[str], history: list[str], obs: Observation):
    """One request, several heads: the operation plus a speculative target for every operation
    that needs one. Only the head matching the chosen operation is used — two decisions, one
    round trip. Returns (state, questions, clickable, typable)."""
    table = "\n".join(f"[{e.ref}] {e.describe()}" for e in obs.elements) or "(no interactive elements)"
    done = "\n".join(f"{i}. {h}" for i, h in enumerate(history, 1)) or "(none yet)"
    state = (
        f"GOAL: {goal}\n\nVALUES AVAILABLE TO TYPE: {', '.join(value_names) or '(none)'}\n\n"
        f"ACTIONS ALREADY TAKEN:\n{done}\n\nCURRENT PAGE: {obs.title} — {obs.url}\n\n"
        + (f"VISIBLE TEXT (start of page): {obs.text}\n\n" if obs.text else "")
        + f"ELEMENTS ON THE PAGE, in document order:\n{table}"
    )
    clickable = {e.ref: e.describe() for e in obs.elements if e.role in CLICKABLE and not e.disabled}
    typable = {e.ref: e.describe() for e in obs.elements if e.role in TYPABLE and not e.disabled}

    ops = dict(OPS)
    if not clickable:
        ops.pop("CLICK")
    if not typable or not value_names:
        ops.pop("TYPE_TEXT")
    questions: dict = {"operation": {
        "type": "choice", "criteria": ops,
        "instructions": "Which single operation should be performed next to make progress on the GOAL, "
                        "given the actions already taken?"}}
    if clickable:
        questions["click_target"] = {
            "type": "choice", "criteria": clickable,
            "instructions": "If the next operation is a click, which element should be clicked next? "
                            "Do not repeat an action already taken."}
        questions["irreversible"] = {
            "type": "noul",
            "instructions": "If the next operation is a click: would clicking that element delete data, send a "
                            "message, spend money, or cause another effect that cannot be undone? Signing in, "
                            "navigating, opening a menu and searching are not irreversible."}
    if typable and value_names:
        questions["type_target"] = {
            "type": "choice", "criteria": typable,
            "instructions": "If the next operation is typing, which text field should be filled next?"}
        questions["type_value"] = {
            "type": "choice", "criteria": {k: f"the {k}" for k in value_names},
            "instructions": "If the next operation is typing, which available value belongs in the field "
                            "that should be filled next?"}
    return state, questions, clickable, typable


def top(answer: dict, n: int = 3) -> list[dict]:
    probs = sorted((answer.get("probabilities") or {}).items(), key=lambda kv: -kv[1])[:n]
    return [{"option": k, "p": round(v, 2)} for k, v in probs]


def confidence(answer: dict) -> float:
    return float(answer.get("confidence", 0))


def verified(obs: Observation, done_url: str | None, done_text: str | None) -> bool | None:
    """Success is decided here, in code. None means no verifier was supplied."""
    if not (done_url or done_text):
        return None
    ok_url = bool(re.search(done_url, obs.url)) if done_url else True
    ok_text = (done_text.lower() in obs.text.lower()) if done_text else True
    return ok_url and ok_text


def settle_operation(answer: dict, is_verified: bool | None, min_conf: float) -> str:
    """Apply the confidence gate to the operation head and return the operation to perform.

    The gate protects ACTIONS. A page that is merely mid-load makes Jev split DONE/WAIT —
    neither touches the page, so that is a reason to keep waiting, not to call for help.
    A hesitant BLOCKED is still a stop.
    """
    choice = answer["choice"]
    contenders = {c["option"] for c in top(answer, 2) if c["p"] >= 0.15}
    if choice in PASSIVE and is_verified is False:
        return "WAIT" if contenders <= PASSIVE else choice
    if confidence(answer) < min_conf and choice != "BLOCKED":
        raise Escalate("low_confidence", question="operation", confidence=round(confidence(answer), 2),
                       candidates=top(answer))
    return choice


def guard_click(label: str, p_irreversible: float, allow_risky: bool) -> None:
    """Two independent signals, either one stops the click: a word list in code and Jev's own
    estimate that the effect cannot be undone."""
    if allow_risky:
        return
    word = bool(RISKY.search(label))
    if word or p_irreversible >= 0.5:
        raise Escalate("risky_click", target=label, p_irreversible=round(p_irreversible, 2), matched_guard_word=word)


def require_confident(which: str, answer: dict, min_conf: float, labels: dict) -> None:
    if confidence(answer) < min_conf:
        cands = top(answer)
        raise Escalate("low_confidence", question=which, confidence=round(confidence(answer), 2), candidates=cands,
                       labels={c["option"]: labels.get(c["option"], c["option"]) for c in cands})
