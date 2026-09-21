#!/usr/bin/env bash
# Fill in and submit Selenium's public demo form. This is the run shown in the README GIF.
set -euo pipefail
export DEMO_PASSWORD="${DEMO_PASSWORD:-correct-horse-battery}"
hunch --url https://www.selenium.dev/selenium/web/web-form.html \
      --goal "Fill in the text input with the name, fill in the password, write the comment in the textarea, then submit the form." \
      --value name=literal:"Ada Lovelace" \
      --value password=env:DEMO_PASSWORD \
      --value comment=literal:"Filled in by hunch - one Jev decision per step." \
      --done-text "Received!"
