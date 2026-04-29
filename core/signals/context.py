from datetime import datetime

from strategy import Strategy


def _safe_series_float(df, column, default=0.0):
    try:
        if df is not None and column in df.columns:
            return float(df[column].iloc[-1])
    except Exception:
        return float(default)
    return float(default)


def _build_symbol_context(bot, symbol_raw, symbol, df_main, price, ind, audit_signal):
    decision = {"signal": audit_signal, "mode": ind.get("mode", "NONE")}
    raw_metrics = Strategy.compute_runtime_snapshot(df_main)
    if not raw_metrics:
        raise KeyError("RAW_TA_UNAVAILABLE")

    ema_ref = float(raw_metrics.get("ema", price) or price)
    trend_label = "RANGO"
    current_adx = float(
        raw_metrics.get("adx", ind.get("adx", _safe_series_float(df_main, "adx", 0.0)))
    )
    current_rsi = float(raw_metrics.get("rsi", _safe_series_float(df_main, "rsi", 50.0)))
    current_atr = float(raw_metrics.get("atr", _safe_series_float(df_main, "atr", 0.0)))
    volume_now = _safe_series_float(df_main, "volume_raw", _safe_series_float(df_main, "volume", 0.0))
    volume_ma = float(raw_metrics.get("volume_ma", _safe_series_float(df_main, "volume_ma", 0.0)))
    close_raw = _safe_series_float(df_main, "close", price)
    open_raw = _safe_series_float(df_main, "open", price)
    high_raw = _safe_series_float(df_main, "high", price)
    low_raw = _safe_series_float(df_main, "low", price)
    ema_dist_pct_raw = ((close_raw - float(ema_ref)) / float(ema_ref) * 100.0) if ema_ref else 0.0
    bb_lower = float(raw_metrics.get("bb_lower", 0.0))
    bb_upper = float(raw_metrics.get("bb_upper", 0.0))
    bb_width_raw = float(raw_metrics.get("bb_width", 0.0))
    bb_pos_raw = float(raw_metrics.get("bb_pos", 0.5))

    if current_adx > 25:
        trend_label = "UP" if price > ema_ref else "DOWN"

    vol_rel = (volume_now / volume_ma) if volume_ma > 0 else 0.0
    atr_pct_raw = (current_atr / close_raw) if close_raw > 0 else 0.0

    ctx = {
        "features_version": "v2_raw_plus_model",
        "raw_rows": int(raw_metrics.get("rows", 0)),
        "rsi": current_rsi,
        "adx": current_adx,
        "close": close_raw,
        "ema": float(ema_ref),
        "df_1h": df_main,
        "atr": current_atr,
        "atr_pct": atr_pct_raw,
        "trend": trend_label,
        "regime": ind.get("regime", "NORMAL"),
        "veto_reason": ind.get("veto_reason"),
        "z_score": ind.get("z_score", 0.0),
        "vol_24h": float(
            bot._snapshot_tickers.get(symbol_raw, {}).get("quoteVolume", 0)
            or bot._snapshot_tickers.get(symbol, {}).get("quoteVolume", 0)
            or 0
        )
        if hasattr(bot, "_snapshot_tickers") and bot._snapshot_tickers
        else 0.0,
        "tier": ind.get("tier", "IRON"),
        "spread": ind.get("spread", 0.0),
        "vol_rel": float(vol_rel),
        "open_raw": open_raw,
        "high_raw": high_raw,
        "low_raw": low_raw,
        "close_raw": close_raw,
        "ema_raw": float(ema_ref),
        "rsi_raw": current_rsi,
        "adx_raw": current_adx,
        "atr_raw": current_atr,
        "atr_pct_raw": atr_pct_raw,
        "volume_raw": volume_now,
        "volume_ma_raw": volume_ma,
        "vol_rel_raw": float(vol_rel),
        "ema_dist_pct_raw": ema_dist_pct_raw,
        "bb_pos_raw": bb_pos_raw,
        "bb_width_raw": bb_width_raw,
        "model_rsi": _safe_series_float(df_main, "rsi", current_rsi),
        "model_adx": _safe_series_float(df_main, "adx", current_adx),
        "model_atr": _safe_series_float(df_main, "atr", current_atr),
        "model_volume": _safe_series_float(df_main, "volume", volume_now),
        "model_dist_ema": _safe_series_float(df_main, "dist_ema", raw_metrics.get("dist_ema", ema_dist_pct_raw / 100.0)),
        "model_z_score": _safe_series_float(df_main, "z_score", ind.get("z_score", 0.0)),
        "model_bb_pos": _safe_series_float(df_main, "bb_pos", bb_pos_raw),
        "model_bb_width": _safe_series_float(df_main, "bb_width", bb_width_raw),
    }

    ob_status = Strategy.detect_order_block(df_main, symbol)
    ctx["ob_status"] = ob_status
    ctx["btc_delta_tf"] = getattr(bot, "market_btc_change_tf", 0.0)
    ctx["funding_rate"] = (
        bot._get_cached_funding_rate(symbol) if audit_signal in ["BUY", "SELL"] else 0.0
    )
    ctx["market_hour"] = datetime.now().hour
    market_breadth = getattr(bot, "market_breadth", {}) or {}
    ctx["market_breadth_sentiment"] = market_breadth.get("sentiment", "NEUTRAL")
    ctx["market_breadth_dump_ratio"] = float(market_breadth.get("dump_ratio", 0.0) or 0.0)
    ctx["market_breadth_pump_ratio"] = float(market_breadth.get("pump_ratio", 0.0) or 0.0)

    raw_log_count = int(getattr(bot, "_raw_snapshot_log_count", 0) or 0)
    if raw_log_count < 5:
        bot.log(
            f"🧪 RAW_TA {symbol}: rows={ctx['raw_rows']} "
            f"RSI={ctx['rsi_raw']:.2f} ADX={ctx['adx_raw']:.2f} ATR={ctx['atr_raw']:.6f} EMA={ctx['ema_raw']:.6f}"
        )
        bot._raw_snapshot_log_count = raw_log_count + 1

    return decision, ctx, ob_status, vol_rel


def _update_signal_diagnostics(
    bot, symbol, audit_signal, prob_final, mode, votos, ind, signal_stats
):
    if audit_signal in ["BUY", "SELL"]:
        signal_stats[audit_signal] += 1
    else:
        signal_stats["NEUTRAL"] += 1

    curr_rag_imp = ind.get("rag_impact", 0.0)
    bot.global_rag_impact = (bot.global_rag_impact * 0.98) + (curr_rag_imp * 0.02)

    if curr_rag_imp > 15.0 and ind.get("rag_evidence"):
        ev_str = ", ".join(ind["rag_evidence"][:3])
        bot.log(f"🧠 RAG INTERVENCIÓN ({curr_rag_imp:.1f}%): Basado en {ev_str}")

    if bot.global_rag_impact > 10.0:
        bot.risk_multiplier = 0.5

    bot.render_consensus_telemetry(symbol, prob_final, mode, votos)
