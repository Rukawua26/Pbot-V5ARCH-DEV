from datetime import datetime

from strategy import Strategy


def _build_symbol_context(bot, symbol_raw, symbol, df_main, price, ind, audit_signal):
    decision = {"signal": audit_signal, "mode": ind.get("mode", "NONE")}

    ema_ref = df_main["ema"].iloc[-1] if "ema" in df_main.columns else price
    trend_label = "RANGO"
    current_adx = float(
        ind.get(
            "adx",
            (
                df_main["adx_raw"].iloc[-1]
                if "adx_raw" in df_main.columns
                else (df_main["adx"].iloc[-1] if "adx" in df_main.columns else 0.0)
            ),
        )
    )
    current_rsi = float(
        df_main["rsi_raw"].iloc[-1]
        if "rsi_raw" in df_main.columns
        else (df_main["rsi"].iloc[-1] if "rsi" in df_main.columns else 50.0)
    )

    if current_adx > 25:
        trend_label = "UP" if price > ema_ref else "DOWN"

    vol_rel = (
        (df_main["volume"].iloc[-1] / df_main["volume_ma"].iloc[-1])
        if "volume_ma" in df_main.columns and df_main["volume_ma"].iloc[-1] > 0
        else 0.0
    )

    ctx = {
        "rsi": current_rsi,
        "adx": current_adx,
        "close": price,
        "df_1h": df_main,
        "atr": df_main["atr"].iloc[-1] if "atr" in df_main.columns else 0.0,
        "atr_pct": (df_main["atr"].iloc[-1] / price)
        if ("atr" in df_main.columns and price > 0)
        else 0,
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
    }

    ob_status = Strategy.detect_order_block(df_main, symbol)
    ctx["ob_status"] = ob_status
    ctx["btc_delta_tf"] = getattr(bot, "market_btc_change_tf", 0.0)
    ctx["funding_rate"] = (
        bot._get_cached_funding_rate(symbol) if audit_signal in ["BUY", "SELL"] else 0.0
    )
    ctx["market_hour"] = datetime.now().hour

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
