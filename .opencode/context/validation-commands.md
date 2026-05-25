# Validation Commands

Use the local venv when it exists: `./.venv/bin/python`.

Base validation sequence aligned with CI:

```bash
./.venv/bin/python -m compileall -q main.py core
PATH="/home/miguel/Pbot-V5ARCH-DEV/.venv/bin:$PATH" bash scripts/smoke_modular_imports.sh
./.venv/bin/python tools/check_no_silent_pass.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python tools/regression_contracts.py
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m unittest tests/test_temporal_invariance.py
```

For a focused security/runtime test:

```bash
SNIPER_DISABLE_FILE_TELEMETRY=1 ./.venv/bin/python -m unittest tests/test_bot_security_runtime.py
```

Run `scripts/smoke_modular_imports.sh` when bootstrap or modular imports change.

Run `tools/regression_contracts.py` when `main.py`, `Bot`, or `BotFacade` contracts change.
