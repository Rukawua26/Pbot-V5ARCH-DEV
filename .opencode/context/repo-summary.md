# Repo Summary

This repository is a Binance Futures trading bot with `PAPER`, `SHADOW`, and `REAL` modes.

Core truths:

- `main.py` is the real entrypoint and should only import `run_entrypoint` from `core.bot_app`.
- Heavy bootstrap belongs in `core/bot_app.py`, not `main.py`.
- `config.py` is a legacy proxy; real configuration is in `core/config/manager.py` and `core/config/operational.py`.
- `.env` is loaded by importing `core/config/operational.py`.
- `core/bot_facade.py` is the public runtime contract.
- `core/bot_connection.py` separates connection behavior by mode.
- `core/execution_adapters.py` defines live and shadow-live execution boundaries.

Keep changes small. Do not mix broad refactors with functional fixes.
