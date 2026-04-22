# AGENTS

Guía operativa para agentes que trabajen en `Pbot V5ARCH DEV`.

## Objetivo

Este repositorio contiene un bot de trading para Binance Futures con modos `PAPER`, `SHADOW` y `REAL`. La prioridad es:

1. seguridad operativa
2. protección de capital
3. correctitud de runtime
4. mantenibilidad a largo plazo
5. cambios pequeños y verificables

## Reglas Base

- Haz cambios mínimos y correctos
- No mezcles refactors amplios con cambios funcionales
- No rompas invariantes de runtime por mejorar ergonomía o conveniencia
- En modo `REAL`, los fallos de auth/permisos deben fallar claro
- En modo `PAPER/SHADOW`, degrada de forma segura cuando sea posible
- Nunca asumas que la DB es la verdad final de exposición live; el exchange manda
- No introduzcas `pass`, swallow silencioso de excepciones ni logs ambiguos
- No agregues dependencias nuevas sin necesidad real

## Skills Obligatorias Por Tipo De Trabajo

### Siempre priorizar

- `skills/runtime-ops-and-trading-safety/SKILL.md`
- `skills/security-and-hardening/SKILL.md`
- `skills/code-review-and-quality/SKILL.md`
- `skills/test-driven-development/SKILL.md`

### Según el caso

- Runtime, exchange, reconciliación, watchdog, recovery:
  `skills/runtime-ops-and-trading-safety/SKILL.md`
- Bugs, boot failures, errores extraños, incidentes:
  `skills/debugging-and-error-recovery/SKILL.md`
- Contratos, boundaries, servicios, interfaces internas:
  `skills/api-and-interface-design/SKILL.md`
- Cambios grandes o multiarchivo:
  `skills/incremental-implementation/SKILL.md`
- Diseño previo, especificación o cambios ambiguos:
  `skills/spec-driven-development/SKILL.md`
- Descomposición de trabajo:
  `skills/planning-and-task-breakdown/SKILL.md`
- Simplificación sin cambio de comportamiento:
  `skills/code-simplification/SKILL.md`
- CI, quality gates y automatización:
  `skills/ci-cd-and-automation/SKILL.md`
- ADRs y documentación duradera:
  `skills/documentation-and-adrs/SKILL.md`
- Migraciones o retiro de código legacy:
  `skills/deprecation-and-migration/SKILL.md`

Referencia general:
- `skills/README.md`

## Invariantes Del Bot

- No dejar posiciones reales desnudas sin protección o decisión explícita
- No duplicar side effects de exchange por retries no idempotentes
- Persistir estados relevantes de lifecycle cuando el flujo lo requiera
- Mantener explícitos los estados runtime y sus transiciones
- Si hay duda entre continuar o frenar una ruta real, priorizar seguridad
- Los logs operativos deben identificar símbolo, lado, motivo y estado

## Reglas Estrictas Para Modo REAL

- Cualquier fallo de auth, permisos, balance o conectividad crítica debe fallar claro; no degradar silenciosamente
- No abrir ni gestionar posiciones reales si el estado del exchange no puede verificarse con confianza
- No asumir que una orden fue aceptada, abierta o llenada sin evidencia explícita del exchange o reconciliación posterior
- No reintentar colocación o cierre de órdenes reales de forma no idempotente sin control de duplicación
- No ejecutar recovery automático si existe riesgo de duplicar exposición o cerrar el lado incorrecto
- Si falta `HARD SL` en una posición real, tratarlo como incidente crítico y seguir la ruta de protección definida
- Si el runtime entra en estado ambiguo respecto a exposición real, priorizar `HALT`, alerta y reconciliación antes de continuar
- Los cambios que afecten `REAL` deben considerar explícitamente boot, restart, recovery, wallet sync y emergency flows

## Checklist Adicional Antes De Aceptar Cambios Que Toquen REAL

- Validar qué pasa si Binance responde timeout, reject o respuesta parcial
- Validar qué pasa si el proceso reinicia entre persistencia local y side effect de exchange
- Validar que no queden posiciones u órdenes huérfanas sin ruta de reconciliación
- Validar que los logs y eventos permitan auditar después el lifecycle completo
- Validar que `PAPER/SHADOW` y `REAL` no compartan atajos inseguros
- Validar que cualquier degradación permitida en `PAPER` no se filtre a `REAL`

## Archivos Clave

- `main.py`: entrypoint
- `core/bot_app.py`: bootstrap principal
- `core/bot_facade.py`: fachada del runtime
- `core/bot_connection.py`: conexión y boot con exchange
- `core/execution_service.py`: ejecución live
- `core/execution_adapters.py`: adapters `live` / `shadow_live`
- `core/signals/`: análisis, filtros y planificación de ejecución
- `core/bot_wallet_sync.py`: reconciliación y sync de wallet/posición
- `core/config/manager.py`: umbrales unificados
- `tests/`: regresiones de runtime

## Verificación Mínima

Antes de dar por terminado un cambio que afecte comportamiento:

- correr tests específicos del área tocada
- validar sintaxis/imports
- revisar que no se rompan rutas de arranque

Comandos comunes:

```bash
./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
./.venv/bin/python -m compileall -q main.py core tests
PATH="/home/miguel/Pbot-V5ARCH-DEV/.venv/bin:$PATH" bash scripts/smoke_modular_imports.sh
```

Si el cambio toca runtime crítico, además revisar:

- logs generados
- mensajes de error
- semántica de recovery/restart
- comportamiento en `PAPER_MODE` y `REAL` cuando aplique
- impacto en idempotencia y reconciliación
- si el flujo sigue siendo seguro tras reinicio inesperado

## Límites

- No commitear secretos, `.env`, DBs o logs
- No usar comandos destructivos de git
- No borrar código legacy sin entender por qué existe
- No editar fuera de alcance “ya que estás ahí”

## Estilo De Trabajo Esperado

- primero entender
- luego cambiar
- luego verificar
- luego resumir con claridad qué cambió, por qué y cómo se validó
