---
name: hunch
description: Fast path for routine browser steps. Use when a browser task is a known, repeatable flow (sign in, fill a form, walk a wizard, run a QA checklist) and agent-browser is the browser tool. hunch lets Jev make each step's decision in ~150 ms and only hands control back when it is unsure.
---

# hunch

Run routine browser flows through `hunch` instead of deciding every step yourself. Take over only when it escalates.

## When to use it

- The flow is ordinary: log in, fill fields you already have values for, click through to a known page.
- You can state how success looks (a URL pattern or a piece of text).

Do not use it for open-ended exploration, for anything needing free-form typed text, or on pages inside iframes / shadow DOM.

## How

```bash
hunch --session <agent-browser session> --url <start url> \
      --goal "<plain, direct instruction>" \
      --value email=env:QA_EMAIL --value password=env:QA_PASSWORD \
      --done-url '<regex the URL must match>' --quiet
```

- Pass secrets with `env:`, never `literal:`. Only value *names* are sent to the model.
- Always give `--done-url` or `--done-text`. Without one, a "done" is unverified (exit 3).
- Keep goals literal and sequential. Jev reads word for word.

## Reading the result

| exit | meaning | what you do |
|---|---|---|
| 0 | goal verified in code | carry on |
| 2 | escalated | read `reason`, `visible_text`, `history`, `elements`, then continue the task yourself with agent-browser from the current page |
| 3 | model says done, nothing verified it | check the page yourself |

Reasons: `low_confidence` (it names the candidates), `blocked` (usually an error on the page: read `visible_text`),
`risky_click` (a destructive control: confirm with the user before clicking it yourself), `no_progress`,
`stuck_waiting`, `left_allowed_origin`, `done_not_verified`, `step_limit`, `jev_error`.

Never pass `--allow-risky` on your own initiative. A `risky_click` escalation is a request for human confirmation.

## Second opinion

`hunch --dry-run --quiet --goal "<what you are trying to do>"` returns the element it would act on, with confidence,
without touching the page. Cheap to call when a snapshot is large and you want a fast pointer.
