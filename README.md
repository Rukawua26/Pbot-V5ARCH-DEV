# Pbot V5ARCH DEV

Bot cuantitativo para Binance Futures con runtime modular, escaneo dinamico 1H, filtros estructurales, ejecucion segura y operacion en modos `PAPER`, `REAL` y `shadow_live`.

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![Status](https://img.shields.io/badge/Status-Active-22c55e)
![CI](https://github.com/Rukawua26/Pbot-V5ARCH-DEV/actions/workflows/ci.yml/badge.svg?branch=main)

## Resumen

- Entrypoint minimalista en `main.py`; el bootstrap real vive en `core/bot_app.py`.
- Configuracion real centralizada en `core/config/manager.py` y `core/config/operational.py`.
- Escaneo de mercado 1H con filtro macro 4H, triaje dinamico por liquidez y veto por latencia.
- Pipeline de senales por agentes (`MT`, `SR`, `G`) con watchlist de breakout y filtro `SHOCK`.
- Runtime con reconciliacion de arranque, adopcion de huerfanas, `HARD SL` y cierre de emergencia si el estado live queda ambiguo.
- Adaptadores de ejecucion `live` y `shadow_live` para simular rechazos, latencia y fills parciales sin contaminar la logica de negocio.
- Operacion asistida por Telegram, watchdog, telemetria runtime y suite de regresion en CI.

## Capacidades actuales

| Area | Estado | Detalle |
|---|---|---|
| Runtime modular | Activo | `Bot`, `BotFacade`, ciclos, IO loops y monitorizacion desacoplados |
| Triage dinamico | Activo | Top pares por liquidez, spread, volumen y latencia |
| Motor de senales | Activo | Analisis 1H, veto macro 4H, votos de agentes y decision final |
| Breakout watchlist | Activo | Seguimiento pasivo/semi-activo de oportunidades vetadas o en espera |
| Exit engine | Activo | Salidas dinamicas, trailing ATR, breakeven y degradacion de confianza |
| Reconciliacion | Activo | Recovery DB/exchange, intents, orfanas, `LOST_IN_TRANSMISSION` |
| Ejecucion segura | Activo | `HARD SL`, cierres de emergencia y protecciones de runtime |
| Telemetria | Activo | `logs/execution_events.jsonl`, runtime metrics y scorecards |
| Operacion remota | Activo | Comandos Telegram para auditoria, inteligencia y control |
| Docker/systemd | Disponible | Despliegue en VPS o contenedor |

## Modos operativos

| Modo | Configuracion | Comportamiento |
|---|---|---|
| `PAPER` | `PAPER_MODE=true` | Usa capital virtual. Si hay credenciales, valida conectividad; si no, puede seguir con endpoints publicos. |
| `REAL` | `PAPER_MODE=false` | Requiere credenciales y permisos validos de Binance Futures; errores de auth/permisos abortan el arranque. |
| `shadow_live` | `EXECUTION_BACKEND=shadow_live` | Mantiene runtime real pero simula latencia, rechazo, slippage y fills parciales. |
| `TESTNET` | `USE_TESTNET=true` | Activa sandbox cuando el backend lo soporta; en `PAPER` puede degradar a mercado publico real para lecturas. |

## Arquitectura

### Runtime

```text
main.py
  -> core.bot_app.run_entrypoint()
     -> Bot(BotFacade)
        -> bootstrap de servicios, modelos, runtime state y loops
```

### Modulos clave

| Ruta | Rol |
|---|---|
| `main.py` | Entrypoint real del proceso |
| `core/bot_app.py` | Bootstrap pesado, clase `Bot`, event loop y wiring principal |
| `core/bot_facade.py` | Contrato publico del runtime |
| `core/bot_connection.py` | Conexion a Binance y reglas por modo operativo |
| `core/reconciliation.py` | Recovery de estado DB/exchange al arranque |
| `core/execution_adapters.py` | Backends `live` y `shadow_live` |
| `core/execution_service.py` | Puerto de ejecucion contra exchange |
| `core/bot_guardian.py` | Vigilancia y protecciones sobre posiciones activas |
| `core/bot_wallet_sync.py` | Sincronizacion de wallet y capital |
| `core/command_router.py` | Router de comandos Telegram |
| `core/signals/` | Contexto, analisis, filtros y ejecucion de senales |
| `core/strategy/` | Agentes, consenso y filtros de estrategia |
| `tests/` | Regresiones runtime, guardrails y contratos |

## Seguridad runtime

- El exchange manda sobre la DB para exposicion real y estado de ordenes/posiciones.
- No se dejan posiciones reales sin `HARD SL`.
- Si el `HARD SL` no puede re-adjuntarse por rechazo tipo `would trigger immediately (-2021)`, el bot ejecuta `Emergency Market Close`.
- `LOST_IN_TRANSMISSION` solo se declara tras agotar verificacion en posiciones activas, ordenes abiertas y consulta por `origClientOrderId`.
- Si el estado live queda ambiguo, el comportamiento esperado es `HALT` o reconciliacion antes de continuar.
- Hay guardrail para bloquear `pass` silenciosos en `core/` mediante CI.

### Estados runtime de orden/trade

- `PENDING_SEND`
- `PENDING_EXCHANGE_OPEN`
- `ENTRY_FILLED_AWAITING_POSITION_SYNC`
- `OPEN`
- `CLOSING_INITIATED`

## Configuracion

`.env` se carga automaticamente desde `core/config/operational.py`.

### Variables importantes

| Variable | Uso |
|---|---|
| `BINANCE_API_KEY`, `BINANCE_API_SECRET` | Credenciales Binance Futures |
| `PAPER_MODE` | Alterna `PAPER`/`REAL` |
| `PAPER_INITIAL_BALANCE` | Capital virtual inicial |
| `USE_TESTNET` | Sandbox/testnet cuando el backend lo soporta |
| `EXECUTION_BACKEND` | `live` o `shadow_live` |
| `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` | Operacion remota y alertas |
| `TRIAGE_MAX_WORKERS` | Concurrencia del escaneo |
| `PARTIAL_FILL_TIMEOUT_SECONDS` | Timeout para fills parciales |
| `PENDING_SEND_STALE_SECONDS` | Expiracion de intents huerfanas |
| `GLOBAL_ENTRY_COOLDOWN_SECONDS` | Cooldown global de entradas |
| `HARD_SL_ATTACH_MAX_RETRIES` | Reintentos para adjuntar stop loss |
| `WATCHDOG_HEARTBEAT_PATH` | Ruta del heartbeat del watchdog |

### Variables para `shadow_live`

- `SHADOW_SIM_LATENCY_MIN_MS`
- `SHADOW_SIM_LATENCY_MAX_MS`
- `SHADOW_SIM_REJECT_RATE`
- `SHADOW_SIM_PARTIAL_FILL_RATE`
- `SHADOW_SIM_PARTIAL_COMPLETE_RATE`
- `SHADOW_SIM_PRICE_OUT_OF_RANGE_RATE`
- `SHADOW_SIM_MIN_PARTIAL_RATIO`

## Instalacion

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Dependencias principales actuales:

- `ccxt`
- `pandas`
- `ta`, `pandas_ta`
- `scikit-learn`, `xgboost`, `lightgbm`, `imbalanced-learn`
- `requests`, `websockets`, `websocket-client`
- `optuna`

## Ejecucion

### Local

```bash
./.venv/bin/python main.py
```

### systemd user

```bash
bash tools/install_watchdog_systemd.sh
systemctl --user status sniper-ai.service --no-pager
```

Plantillas portables disponibles:

- `deploy/systemd/sniper-ai.service.template`
- `deploy/systemd/sniper-ai-watchdog.service.template`

### Docker

```bash
docker compose up --build -d
```

Notas del despliegue Docker actual:

- Imagen base `python:3.12-slim`
- Usuario no root
- Persistencia en `./data/db` y `./data/models`
- `SNIPER_DB_PATH=/app/data/sniper_brain.db`

## Operacion diaria

| Tarea | Comando |
|---|---|
| Ver estado | `systemctl --user status sniper-ai.service --no-pager` |
| Iniciar | `systemctl --user start sniper-ai.service` |
| Detener | `systemctl --user stop sniper-ai.service` |
| Reiniciar | `systemctl --user restart sniper-ai.service` |
| Logs en vivo | `journalctl --user -u sniper-ai.service -f` |
| Ultimos logs | `journalctl --user -u sniper-ai.service -n 100 --no-pager` |
| Reinstalar servicio | `bash tools/install_watchdog_systemd.sh` |
| Actualizar dependencias | `source .venv/bin/activate && pip install -r requirements.txt` |

## Telemetria y observabilidad

- `sniper.log`: log operativo principal.
- `logs/execution_events.jsonl`: eventos estructurados de ejecucion.
- Runtime monitor con metricas de memoria y salud del proceso.
- Scorecards y reportes de rendimiento diarios.
- `watchdog` y heartbeat para supervision externa.

Eventos de ejecucion relevantes:

- `ENTRY_ORDER_ACK`
- `PARTIAL_FILL_COMPLETED`
- `PARTIAL_FILL_TIMEOUT_CANCEL`
- `PARTIAL_FILL_CANCEL_FAILED`
- `EMERGENCY_CLOSE_EXECUTED`
- `EMERGENCY_CLOSE_FAILED_HALT`

## Comandos Telegram utiles

### Control

- `/on`, `/resume`
- `/off`, `/pause`
- `/panic`, `/closeall`
- `/reset`
- `/rebase_capital`
- `/test`

### Auditoria

- `/status`
- `/audit_report`
- `/open`
- `/targets`
- `/signals`
- `/shadow_stats`
- `/sre_intent`
- `/tiers`
- `/top`

### Analisis e inteligencia

- `/trade_detail <symbol>`
- `/trade <id>`
- `/thinking`
- `/watchlist`
- `/intelligence`
- `/agents`
- `/explain <symbol>`
- `/dna <symbol>`
- `/paper_review`
- `/performance_trends`
- `/shadow_report`

Algunos comandos heredados o remotos fueron deshabilitados a proposito en este despliegue para evitar ejecuciones falsas o dependencias ausentes.

## Validacion minima

Orden base alineado con CI:

```bash
./.venv/bin/python -m compileall -q main.py core
PATH="/home/miguel/Pbot-V5ARCH-DEV/.venv/bin:$PATH" bash scripts/smoke_modular_imports.sh
./.venv/bin/python tools/check_no_silent_pass.py
./.venv/bin/python tools/regression_contracts.py
./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
./.venv/bin/python -m unittest tests/test_temporal_invariance.py
```

Cobertura destacada en `tests/`:

- reconciliacion y wallet sync
- contratos de adaptadores de ejecucion
- flows avanzados de runtime
- watchdog y graceful shutdown
- guardrails de riesgo, leverage y smart exit
- invariancia temporal y seguridad runtime

## Estructura del proyecto

```text
.
|-- main.py
|-- core/
|   |-- bot_app.py
|   |-- bot_facade.py
|   |-- reconciliation.py
|   |-- execution_adapters.py
|   |-- commands/
|   |-- config/
|   `-- strategy/
|-- tests/
|-- tools/
|-- deploy/systemd/
|-- docs/runbooks/
|-- Dockerfile
`-- docker-compose.yml
```

## Documentacion adicional

- `CONTRIBUTING.md`
- `SECURITY.md`
- `SPEC.md`
- `BOT_TECHNICAL_ROADMAP.md`
- `RELEASE_FREEZE_REPORT_2026-04-01.md`
- `docs/runbooks/sre-intent-recovery.md`

## Seguridad del repo

- No subas `.env`, bases `.db`, logs, modelos binarios ni reportes generados con datos locales.
- Usa secretos de entorno o gestor de secretos del servidor para credenciales.
- Antes de operar en `REAL`, valida permisos de Futures, tamaño de cuenta y rutas de recovery.
