# AGENTS

## Alcance

- Este repo es un bot de trading para Binance Futures con modos `PAPER`, `SHADOW` y `REAL`.
- Si un cambio toca ejecución, reconciliación, wallet sync, watchdog o recovery, trátalo como cambio de runtime crítico.

## Fuentes de verdad

- El entrypoint real es `main.py`; solo importa `run_entrypoint` desde `core.bot_app`.
- `config.py` es solo un proxy legacy; la configuración real vive en `core/config/manager.py` y `core/config/operational.py`.
- `.env` se carga al importar `core/config/operational.py`; evita asumir que falta setup explícito en cada script.
- CI en `.github/workflows/ci.yml` es la referencia para el orden mínimo de validación.

## Arquitectura que importa

- `core/bot_app.py` hace el bootstrap pesado y construye `Bot`; no metas lógica nueva en `main.py`.
- `core/bot_facade.py` concentra el contrato público del runtime; conserva sus métodos cuando muevas lógica entre módulos.
- `core/bot_connection.py` separa el comportamiento de conexión por modo: en `PAPER` puede degradar a endpoints públicos; en `REAL` fallos de auth/permisos deben abortar.
- `core/execution_adapters.py` define los backends `live` y `shadow_live`; no mezcles simulación con flujo real fuera de esa frontera.

## Invariantes operativos

- El exchange manda sobre la DB para exposición real y estado de órdenes/posiciones.
- No dejes posiciones reales sin `HARD SL` ni agregues retries no idempotentes que puedan duplicar exposición.
- Si el estado live queda ambiguo, prioriza `HALT` y reconciliación antes de continuar.
- No introduzcas `pass` silenciosos en `core/`; CI lo bloquea salvo una allowlist mínima.

## Skills a cargar

- Siempre que el trabajo toque runtime o seguridad, revisa `skills/runtime-ops-and-trading-safety/SKILL.md`.
- Para cambios de endurecimiento o datos externos, revisa `skills/security-and-hardening/SKILL.md`.
- Para cambios funcionales, revisa `skills/test-driven-development/SKILL.md` y deja cobertura en `tests/`.
- Usa `skills/README.md` como índice de skills curadas; las listadas ahí sí aplican a este repo.

## Verificación mínima

- Usa la venv local cuando exista: `./.venv/bin/python`.
- Orden base alineado con CI:

```bash
./.venv/bin/python -m compileall -q main.py core
PATH="/home/miguel/Pbot-V5ARCH-DEV/.venv/bin:$PATH" bash scripts/smoke_modular_imports.sh
./.venv/bin/python tools/check_no_silent_pass.py
./.venv/bin/python tools/regression_contracts.py
./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
./.venv/bin/python -m unittest tests/test_temporal_invariance.py
```

- Para un test puntual usa `./.venv/bin/python -m unittest tests/test_bot_security_runtime.py`.
- Si cambias bootstrap o imports modulares, corre siempre `scripts/smoke_modular_imports.sh`.
- Si cambias contratos de `main.py`, `Bot` o `BotFacade`, corre siempre `tools/regression_contracts.py`.

## Límites de cambio

- Haz cambios pequeños; no mezcles refactors amplios con fixes funcionales.
- No borres código legacy sin entender si mantiene compatibilidad o recovery.
- No commitees `.env`, bases `.db`, logs ni reportes generados desde datos locales.
