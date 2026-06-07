# Known Bugs — Prevención de Regresiones
> Antes de modificar cualquier archivo listado aquí, verificar reglas preventivas.
> Máximo 10 entradas. Lo antiguo (>30 días) se archiva en `known-bugs-archive.md`.

| Fecha | Bug / Síntoma | Causa Raíz | Archivos | Regla Preventiva |
| :--- | :--- | :--- | :--- | :--- |
| 2026-06-06 | Señales 72-80% no entran operaciones | `REQUIRE_GHOST_MODEL_FOR_TRADING=True` bloquea sin modelo ML cargado. No hay logging visible del rechazo. | `core/config/operational.py`, `core/signals/execution.py` | No cambiar `REQUIRE_GHOST_MODEL_FOR_TRADING` a `True` sin verificar `ghost_model` existe. Mantener logging de rechazo en `execution.py` para `prob_final >= 65`. |
| 2026-06-06 | Señales SELL ≥70% vetadas por `VETO_KAVA: RIESGO EXCESIVO` | `MAX_ENTRY_SL_PCT=1.2%` demasiado bajo para SL basado en ATR×2.0. Ni BTC/ETH pasan el filtro. | `core/config/operational.py`, `tools/strategy.py` | No bajar `MAX_ENTRY_SL_PCT` por debajo de 3.0 sin verificar ATR promedio de los símbolos objetivo. |
| 2026-06-06 | Señales vetadas por `SHOCK DEMASIADO CERCA < 0.40%` | `SHOCK_MIN_DIST_PCT=0.4` filtra señales válidas con shock cercano pero manejable. | `core/config/manager.py`, `core/signals/filters.py` | No subir `SHOCK_MIN_DIST_PCT` por encima de 0.2 sin verificar ratio de falsos positivos. |
| 2026-06-06 | Señales con prob alta no ajustan tamaño por similitud histórica | Similarity search se ejecutaba después del sizing; `similarity_boost` no afectaba posición. | `core/trade_entry.py` | No mover similarity search después del sizing. Mantener `sizing_multiplier` aplicado post-sizing. |
| 2026-06-06 | `MIN_NOTIONAL_VALUE=12` fijo, no configurable, excluye capital pequeño | No había env var para sobrescribirlo. | `core/config/manager.py`, `core/config/strategy.py` | No subir `MIN_NOTIONAL_VALUE` sin verificar balance×leverage del usuario. Mantener env-override. | |
