#!/usr/bin/env bash
# Ask "what would you click here?" about whatever page the session is on, without touching it.
# Useful inside an LLM agent loop: ~200 ms for a calibrated answer, or an escalation if unsure.
set -euo pipefail
hunch --session "${AGENT_BROWSER_SESSION:-hunch}" --dry-run --quiet --goal "${1:?usage: second_opinion.sh \"<goal>\"}"
