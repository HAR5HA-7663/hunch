---
name: hunch
description: Fast path for routine browser steps. Use when a browser task is a known, repeatable flow (sign in, fill a form, walk a wizard, run a QA checklist) and agent-browser is the browser tool. hunch lets Jev make each step's decision in ~150 ms and only hands control back when it is unsure.
---

The canonical copy of this skill lives at [`skills/hunch/SKILL.md`](../../skills/hunch/SKILL.md) so it can be installed with:

```bash
npx skills add HAR5HA-7663/hunch
```

Copy it into `.claude/skills/hunch/SKILL.md` by hand if you prefer.
