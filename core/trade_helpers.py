from __future__ import annotations

import importlib.util
import time
from contextlib import nullcontext
from typing import Any, Dict, Optional

from config import Config
from core.risk_policy import evaluate_entry_risk_decision, record_risk_decision
from core.symbol_utils import normalize_position_symbol
from core.trade_state import open_trade_statuses
from learning import shadow_logger


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _clamp_leverage_1_to_10(raw_leverage) -> int:
    try:
        lev = int(float(raw_leverage))
    except (TypeError, ValueError):
        lev = 10
    return max(1, min(lev, 10))


def _fail_safe_close_when_sl_missing(
    bot, symbol: str, side: str, amount: float
) -> bool:
    for attempt in range(1, 4):
        try:
            bot.execution.close_position(symbol, side, amount)
            return True
        except Exception as error:
            bot.log(
                f"⚠️ FAIL_SAFE_CLOSE intento {attempt}/3 (chase limit) fallido en {symbol}: {error}"
            )
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
    for attempt in range(1, 3):
        try:
            bot.log(f"🧯 FAIL_SAFE_CLOSE intento MARKET {attempt}/2 en {symbol}")
            bot.execution.create_reduce_only_market_order(symbol, side, amount)
            return True
        except Exception as error:
            bot.log(
                f"⚠️ FAIL_SAFE_CLOSE intento MARKET {attempt}/2 fallido en {symbol}: {error}"
            )
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
    return False


def _validate_entry_preconditions(bot, symbol: str, is_shadow: bool) -> Optional[str]:
    existing_state = (getattr(bot, "active_trades", {}) or {}).get(symbol)
    decision = evaluate_entry_risk_decision(
        bot,
        symbol,
        is_shadow,
        existing_state=existing_state,
        is_trading_halted_fn=shadow_logger.is_trading_halted,
    )
    if decision:
        record_risk_decision(bot, decision, symbol=symbol, is_shadow=is_shadow)
        bot.log(decision.log_message)
        return decision.reason

    return None


def _validate_symbol_entry(bot, symbol: str, is_shadow: bool) -> Optional[str]:
    symbol_base = symbol.split("/")[0]
    controls = bot._load_runtime_symbol_controls()
    if symbol_base in controls.get("blocked", set()):
        bot.log(f"🧱 BLOQUEADO por matriz táctica: {symbol}")
        return "SYMBOL_BLOCKED_MATRIX"

    if not is_shadow:
        execution = getattr(bot, "execution", None)
        is_quarantined = getattr(execution, "is_symbol_quarantined", None)
        get_remaining = getattr(
            execution, "get_symbol_quarantine_remaining_seconds", None
        )
        if callable(is_quarantined) and is_quarantined(symbol):
            remaining_s = int(get_remaining(symbol) if callable(get_remaining) else 0)
            bot.log(
                f"🚫 SYMBOL_QUARANTINE_ACTIVE {symbol}: bloqueada apertura real por degradación cancel_all ({remaining_s}s restantes)."
            )
            return "SYMBOL_QUARANTINED"

    return None


def _calculate_pnl_and_metrics(
    trade: Dict[str, Any],
    exit_price: float,
    fees: float,
    side: str,
) -> Dict[str, Any]:
    amt = float(trade["amount"])
    pnl_bruto_usd = (exit_price - trade["entry"]) * amt
    if side == "SELL":
        pnl_bruto_usd *= -1

    pnl_neto_usd = pnl_bruto_usd - fees
    val = trade["entry"] * amt
    pnl_neto_percent = (pnl_neto_usd / val) * 100 if val > 0 else 0

    entry_price = trade["entry"]
    mae_price = trade.get("mae_price", entry_price)
    mfe_price = trade.get("mfe_price", entry_price)

    if side == "BUY":
        mae_percent = ((entry_price - mae_price) / entry_price) * 100 if mae_price else 0
        mfe_percent = ((mfe_price - entry_price) / entry_price) * 100 if mfe_price else 0
    else:
        mae_percent = ((mae_price - entry_price) / entry_price) * 100 if mae_price else 0
        mfe_percent = ((entry_price - mfe_price) / entry_price) * 100 if mfe_price else 0

    return {
        "amt": amt,
        "pnl_bruto_usd": pnl_bruto_usd,
        "pnl_neto_usd": pnl_neto_usd,
        "pnl_neto_percent": pnl_neto_percent,
        "mae_percent": mae_percent,
        "mfe_percent": mfe_percent,
    }


def _safe_log_signal_alert(bot, **kwargs) -> None:
    brain = getattr(bot, "brain", None)
    method = getattr(brain, "log_signal_alert", None)
    lock = getattr(bot, "db_lock", None)
    if not callable(method):
        return
    with (lock or nullcontext()):
        method(**kwargs)


def _safe_update_signal_alert_status(bot, entry_client_order_id, status) -> None:
    brain = getattr(bot, "brain", None)
    method = getattr(brain, "update_signal_alert_status", None)
    lock = getattr(bot, "db_lock", None)
    if not callable(method):
        return
    with (lock or nullcontext()):
        method(entry_client_order_id, status)


def _order_looks_filled(order: dict) -> bool:
    if not isinstance(order, dict):
        return False
    status = str(
        order.get("status") or (order.get("info") or {}).get("status") or ""
    ).lower()
    return status in {"closed", "filled"}


def _exchange_position_is_flat(bot, symbol: str) -> bool:
    fetch_positions = getattr(getattr(bot, "execution", None), "fetch_positions", None)
    if not callable(fetch_positions):
        raise RuntimeError(
            "No se puede confirmar exposición cero: fetch_positions no disponible"
        )

    positions = fetch_positions() or []
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        if normalize_position_symbol(pos.get("symbol", "")) != symbol:
            continue
        contracts = pos.get("contracts")
        if contracts is None:
            contracts = (pos.get("info") or {}).get("positionAmt", 0)
        if abs(float(contracts or 0.0)) > 0.0:
            return False
    return True


def _sanitize_context(bot, context):
    data_service = getattr(bot, "data_service", None)
    sanitizer = getattr(data_service, "sanitize_context", None)
    if callable(sanitizer):
        return sanitizer(context)
    if isinstance(context, dict):
        return dict(context)
    return {}


def _get_local_open_trade_counts(bot):
    open_statuses = open_trade_statuses()
    states = {}
    try:
        states.update(getattr(bot, "active_trades", {}) or {})
    except Exception as error:
        logger = getattr(bot, "log", None)
        if callable(logger):
            logger(f"🛑 No se pudo leer active_trades local para conteo: {error}")
        return int(getattr(Config, "MAX_OPEN_TRADES", 1)), int(
            getattr(Config, "MAX_SHADOW_TRADES", 0)
        )

    brain = getattr(bot, "brain", None)
    loader = getattr(brain, "load_active_trade_states", None)
    if callable(loader):
        try:
            for symbol, state in (loader() or {}).items():
                states.setdefault(symbol, state)
        except Exception as error:
            logger = getattr(bot, "log", None)
            if callable(logger):
                logger(f"🛑 No se pudo cargar estado persistido para conteo: {error}")
            return int(getattr(Config, "MAX_OPEN_TRADES", 1)), int(
                getattr(Config, "MAX_SHADOW_TRADES", 0)
            )

    num_real = 0
    num_shadow = 0
    for state in states.values():
        if not isinstance(state, dict):
            continue
        status = str(state.get("status") or "").upper()
        if status not in open_statuses:
            continue
        if bool(state.get("is_shadow", False)):
            num_shadow += 1
        else:
            num_real += 1
    return num_real, num_shadow
