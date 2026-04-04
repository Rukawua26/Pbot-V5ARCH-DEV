from config import Config
from strategy import Strategy


def _analyze_symbol_candidate(bot, symbol_raw, symbol, df_main, df_4h, elapsed):
    try:
        if df_main is None or df_4h is None or df_main.empty:
            bot.update_radar(
                symbol,
                {"signal": "WAIT", "mode": "NONE"},
                0.0,
                "⚪",
                "🚫 SIN DATOS",
                {"tier": "IRON"},
                response_ms=elapsed,
            )
            return None

        if bot.force_chaos_mode:
            df_main["atr"] = df_main["close"] * 0.06

        precio_actual = df_main["close"].iloc[-1]
        rango_promedio = (df_main["high"].tail(14) - df_main["low"].tail(14)).mean()
        atr_pct = (rango_promedio / precio_actual) * 100

        max_allowed_sl_pct = (Config.MAX_RISK_USD / Config.MIN_NOTIONAL_VALUE) * 100
        if (atr_pct * 1.5) > max_allowed_sl_pct:
            bot.update_radar(
                symbol,
                {"signal": "WAIT", "mode": "NONE"},
                0.0,
                "⚪",
                f"⏭️ VOL EXTREMA ({atr_pct:.1f}%)",
                {"atr_pct": atr_pct / 100, "tier": "IRON"},
            )
            return None

        with bot.db_lock:
            dynamic_params = bot.brain.get_dynamic_settings(symbol)

        default_min = Config.SHADOW_MIN_PROBABILITY_RANGE / 10.0
        min_score = dynamic_params.get("min_score", default_min) if dynamic_params else default_min
        if bot.global_rag_impact > 10.0:
            min_score = max(min_score, 8.8)

        with bot.db_lock:
            res = Strategy.analyze(
                df_main,
                df_main,
                bot.brain,
                symbol=symbol,
                order_book=None,
                ghost_model=bot.ghost_model,
                scaler=bot.scaler,
                btc_delta_tf=getattr(bot, "market_btc_change_tf", 0.0),
                min_score=min_score,
                funding_rate=0.0,
                df_4h=df_4h,
            )

        if res[3] >= 50.0:
            try:
                if bot.weight_tracker and bot.weight_tracker.should_block("market"):
                    order_book = None
                else:
                    order_book = bot.execution.fetch_order_book(symbol, limit=20)
                funding_rate = bot._get_cached_funding_rate(symbol)
            except Exception:
                order_book = None
                funding_rate = 0.0

            with bot.db_lock:
                res = Strategy.analyze(
                    df_main,
                    df_main,
                    bot.brain,
                    symbol=symbol,
                    order_book=order_book,
                    ghost_model=bot.ghost_model,
                    scaler=bot.scaler,
                    btc_delta_tf=getattr(bot, "market_btc_change_tf", 0.0),
                    min_score=min_score,
                    funding_rate=funding_rate,
                    df_4h=df_4h,
                )

        return res

    except KeyError as e_key:
        bot.log(f"⚠️ {symbol} descartado: Datos insuficientes para indicador clave ({e_key}).")
        bot.update_radar(
            symbol_raw,
            {"signal": "WAIT", "mode": "NONE"},
            0.0,
            "⚪",
            f"⚠️ KEY_ERR: {e_key}",
            {"tier": "IRON"},
        )
        return None
    except Exception as e_inner:
        bot.log(f"⚠️ Error análisis para {symbol}: {e_inner}")
        bot.update_radar(
            symbol_raw,
            {"signal": "WAIT", "mode": "NONE"},
            0.0,
            "⚪",
            f"❌ ERROR: {str(e_inner)[:15]}",
            {"tier": "IRON"},
        )
        return None
