#!/usr/bin/env bash
# Sign in to a public demo store. Success is the URL, checked in code.
# Point the same three flags at your own staging login: values come from the environment,
# so the password never appears on the command line or in the model's input.
set -euo pipefail
export DEMO_USER=standard_user DEMO_PASS=secret_sauce
hunch --url https://www.saucedemo.com/ \
      --goal "Sign in to the store with the username and the password." \
      --value username=env:DEMO_USER --value password=env:DEMO_PASS \
      --done-url '/inventory\.html$'
