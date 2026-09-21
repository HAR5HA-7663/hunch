"""Offline tests: a scripted fake browser and a scripted fake Jev. No network, no Chrome.

    python -m unittest discover -s tests -v
"""

import unittest

from hunch import Agent, Config
from hunch.agent import DONE_UNVERIFIED, DONE_VERIFIED, ESCALATE
from hunch.cli import parse_values
from hunch.core import Escalate, Observation, build_request, guard_click, parse_snapshot, settle_operation, verified

LOGIN = '''- heading "Welcome back" [level=2, ref=e5]
- textbox "Email Address *" [ref=e7]
- textbox "Password *" [ref=e17]
- button [ref=e18]
- checkbox "Remember me" [checked=false, ref=e19]
- button "Sign In" [ref=e10]
- link "Forgot password?" [ref=e9]'''
LOGIN_EMAIL = LOGIN.replace('"Email Address *" [ref=e7]', '"Email Address *" [ref=e7]: a@b.co')
LOGIN_BOTH = LOGIN_EMAIL.replace('"Password *" [ref=e17]', '"Password *" [ref=e17]: ••••••')
LOADING = LOGIN_BOTH.replace('button "Sign In" [ref=e10]', 'button "Signing in..." [disabled, ref=e10]')
HOME = '- link "Account settings" [ref=e2]\n- button "New client" [ref=e3]'


def obs(snapshot, url="https://app.test/login", text=""):
    return Observation(parse_snapshot(snapshot), url=url, origin="https://app.test", title="t", text=text, raw=snapshot)


def choice(option, conf=0.99, **others):
    return {"type": "choice", "choice": option, "confidence": conf, "probabilities": {option: conf, **others}}


class FakeBrowser:
    """Replays a list of observations; every action advances to the next one."""

    def __init__(self, pages):
        self.pages, self.i, self.actions = pages, 0, []

    def open(self, url): self.actions.append(("open", url))
    def observe(self): return self.pages[min(self.i, len(self.pages) - 1)]
    def click(self, ref): self.actions.append(("click", ref)); self.i += 1
    def fill(self, ref, value): self.actions.append(("fill", ref, value)); self.i += 1
    def scroll_down(self): self.actions.append(("scroll",)); self.i += 1


class FakeJev:
    def __init__(self, replies):
        self.replies, self.states = list(replies), []

    def ask(self, state, questions):
        self.states.append(state)
        return self.replies.pop(0), {"input_tokens": 100}


def agent(pages, replies, **cfg):
    browser, jev = FakeBrowser(pages), FakeJev(replies)
    config = Config(goal="Sign in with the email and the password.", values={"email": "a@b.co", "password": "hunter2"},
                    done_url=r"^https://app\.test/(?!login)", settle_ms=0, wait_ms=0, **cfg)
    return Agent(browser, jev, config, sleep=lambda s: None), browser, jev


TYPE_EMAIL = {"operation": choice("TYPE_TEXT"), "type_target": choice("e7"), "type_value": choice("email")}
TYPE_PASS = {"operation": choice("TYPE_TEXT"), "type_target": choice("e17"), "type_value": choice("password")}
CLICK_SIGNIN = {"operation": choice("CLICK"), "click_target": choice("e10"), "irreversible": {"type": "noul", "noul": 0.06}}


class Parsing(unittest.TestCase):
    def test_keeps_interactive_elements_in_document_order(self):
        els = parse_snapshot(LOGIN)
        self.assertEqual([e.ref for e in els], ["e7", "e17", "e18", "e19", "e10", "e9"])
        self.assertEqual(els[2].label, "(no label)")           # icon button
        self.assertNotIn("heading", {e.role for e in els})

    def test_field_contents_become_filled_or_empty_never_the_value(self):
        els = {e.ref: e for e in parse_snapshot(LOGIN_EMAIL)}
        self.assertTrue(els["e7"].filled)
        self.assertFalse(els["e17"].filled)
        self.assertNotIn("a@b.co", els["e7"].describe())

    def test_disabled_controls_are_not_offered_as_targets(self):
        _, _, clickable, _ = build_request("g", [], [], obs(LOADING))
        self.assertNotIn("e10", clickable)


class Privacy(unittest.TestCase):
    def test_typed_values_never_reach_jev_only_their_names(self):
        a, browser, jev = agent([obs(LOGIN), obs(LOGIN_EMAIL), obs(LOGIN_BOTH), obs(HOME, url="https://app.test/home")],
                                [TYPE_EMAIL, TYPE_PASS, CLICK_SIGNIN])
        self.assertEqual(a.run().code, DONE_VERIFIED)
        sent = "\n".join(jev.states)
        self.assertNotIn("hunter2", sent)
        self.assertNotIn("a@b.co", sent)
        self.assertIn("VALUES AVAILABLE TO TYPE: email, password", sent)
        self.assertIn(("fill", "e17", "hunter2"), browser.actions)   # the value goes to the browser, locally


class Loop(unittest.TestCase):
    def test_login_flow_is_three_decisions_and_success_is_checked_in_code(self):
        a, browser, _ = agent([obs(LOGIN), obs(LOGIN_EMAIL), obs(LOGIN_BOTH), obs(HOME, url="https://app.test/home")],
                              [TYPE_EMAIL, TYPE_PASS, CLICK_SIGNIN])
        r = a.run()
        self.assertEqual((r.status, r.jev_calls), ("done_verified", 3))
        self.assertEqual([x[0] for x in browser.actions], ["fill", "fill", "click"])

    def test_already_signed_in_costs_zero_jev_calls(self):
        a, _, _ = agent([obs(HOME, url="https://app.test/home")], [])
        r = a.run()
        self.assertEqual((r.status, r.jev_calls), ("done_verified", 0))

    def test_unsure_about_the_target_escalates_without_clicking(self):
        shaky = {"operation": choice("CLICK"), "click_target": choice("e10", 0.55, e9=0.4), "irreversible": {"noul": 0.1}}
        a, browser, _ = agent([obs(LOGIN_BOTH)], [shaky])
        r = a.run()
        self.assertEqual((r.code, r.extra["reason"], r.extra["detail"]["question"]), (ESCALATE, "low_confidence", "click_target"))
        self.assertEqual(browser.actions, [])

    def test_a_loading_page_is_waited_on_not_escalated(self):
        torn = {"operation": choice("DONE", 0.72, WAIT=0.22)}     # below the gate, but both options are passive
        a, browser, _ = agent([obs(LOADING), obs(HOME, url="https://app.test/home")], [torn])
        browser.i = 0
        pages = iter([obs(LOADING), obs(HOME, url="https://app.test/home")])
        browser.observe = lambda: next(pages, obs(HOME, url="https://app.test/home"))
        self.assertEqual(a.run().status, "done_verified")

    def test_waiting_forever_escalates(self):
        a, _, _ = agent([obs(LOADING)], [{"operation": choice("WAIT")}] * 5, max_waits=2)
        r = a.run()
        self.assertEqual((r.code, r.extra["reason"]), (ESCALATE, "stuck_waiting"))

    def test_hesitant_blocked_is_still_a_stop_and_reports_the_page_text(self):
        a, browser, _ = agent([obs(LOGIN_BOTH, text="Invalid email or password")], [{"operation": choice("BLOCKED", 0.6, WAIT=0.2)}])
        r = a.run()
        self.assertEqual((r.extra["reason"], r.extra["visible_text"]), ("blocked", "Invalid email or password"))
        self.assertEqual(browser.actions, [])

    def test_actions_that_change_nothing_twice_escalate_instead_of_looping(self):
        a, browser, _ = agent([obs(LOGIN_BOTH)], [CLICK_SIGNIN] * 5)
        browser.click = lambda ref: browser.actions.append(("click", ref))   # page never changes
        r = a.run()
        self.assertEqual((r.extra["reason"], len(browser.actions)), ("no_progress", 2))
        self.assertIn("NO visible effect", r.history[-1])

    def test_leaving_the_allowed_origin_stops_everything(self):
        elsewhere = Observation(parse_snapshot(HOME), url="https://evil.test/", origin="https://evil.test", raw=HOME)
        a, browser, _ = agent([elsewhere], [], url="https://app.test/login")
        r = a.run()
        self.assertEqual((r.extra["reason"], r.jev_calls), ("left_allowed_origin", 0))

    def test_jev_saying_done_is_never_enough_on_its_own(self):
        a, _, _ = agent([obs(LOGIN_BOTH)], [{"operation": choice("DONE", 0.9, CLICK=0.3)}])
        self.assertEqual(a.run().extra["reason"], "done_not_verified")
        a, _, _ = agent([obs(LOGIN_BOTH)], [{"operation": choice("DONE")}])
        a.cfg.done_url = None
        self.assertEqual(a.run().code, DONE_UNVERIFIED)

    def test_dry_run_decides_but_never_acts(self):
        a, browser, _ = agent([obs(LOGIN)], [TYPE_EMAIL], dry_run=True)
        r = a.run()
        self.assertEqual((r.status, browser.actions), ("dry_run", []))
        self.assertEqual(r.extra["decision"]["target"], 'textbox "Email Address *" <- email')


class Guards(unittest.TestCase):
    def test_either_signal_stops_an_irreversible_click(self):
        with self.assertRaises(Escalate):
            guard_click('button "Delete account"', 0.1, allow_risky=False)      # word list
        with self.assertRaises(Escalate):
            guard_click('button "Proceed"', 0.8, allow_risky=False)             # Jev's estimate
        guard_click('button "Sign In"', 0.06, allow_risky=False)
        guard_click('button "Delete account"', 0.9, allow_risky=True)

    def test_risky_click_is_refused_inside_the_loop(self):
        page = '- button "Save changes" [ref=e1]\n- button "Delete account" [ref=e2]'
        a, browser, _ = agent([obs(page)], [{"operation": choice("CLICK"), "click_target": choice("e2"), "irreversible": {"noul": 0.84}}])
        r = a.run()
        self.assertEqual((r.extra["reason"], browser.actions), ("risky_click", []))

    def test_confidence_gate_only_guards_actions(self):
        self.assertEqual(settle_operation(choice("DONE", 0.7, WAIT=0.25), False, 0.75), "WAIT")
        self.assertEqual(settle_operation(choice("BLOCKED", 0.5), False, 0.75), "BLOCKED")
        with self.assertRaises(Escalate):
            settle_operation(choice("CLICK", 0.6, TYPE_TEXT=0.35), False, 0.75)

    def test_verifier(self):
        o = obs(HOME, url="https://app.test/home", text="Form submitted Received!")
        self.assertIsNone(verified(o, None, None))
        self.assertTrue(verified(o, r"/home$", "received"))
        self.assertFalse(verified(o, r"/login", None))


class Cli(unittest.TestCase):
    def test_values_come_from_env_or_literals(self):
        import os
        os.environ["HUNCH_TEST_PW"] = "s3cret"
        self.assertEqual(parse_values(["user=literal:ada", "pw=env:HUNCH_TEST_PW"]), {"user": "ada", "pw": "s3cret"})
        for bad in ("nope", "x=file:/etc/passwd", "pw=env:HUNCH_NOT_SET"):
            with self.assertRaises(SystemExit):
                parse_values([bad])


if __name__ == "__main__":
    unittest.main()
