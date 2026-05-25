---
name: repo-validation
description: Use when validating changes, closing a task, choosing test commands, checking CI parity, or when the user asks to run tests, smoke tests, compile checks, or regression contracts.
---

# Repo Validation

Prefer the local venv: `./.venv/bin/python`.

Base sequence:

```bash
./.venv/bin/python -m compileall -q main.py core
PATH="/home/miguel/Pbot-V5ARCH-DEV/.venv/bin:$PATH" bash scripts/smoke_modular_imports.sh
./.venv/bin/python tools/check_no_silent_pass.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/regression_contracts.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m unittest tests/test_temporal_invariance.py
```

Use narrower tests when appropriate, but report what was skipped and why.

Always run `scripts/smoke_modular_imports.sh` after bootstrap/import changes.

Always run `tools/regression_contracts.py` after changes to `main.py`, `Bot`, or `BotFacade` contracts.
