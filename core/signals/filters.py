from datetime import datetime

from config import Config


def _apply_entry_filters_and_adjust_prob(
    bot, symbol, symbol_raw, df_main, audit_signal, prob_final, ctx, vol_rel
):
    # === [NUEVO v118] FILTROS DE ENTRADA OPTIMIZADOS ===
    # Aplicar filtros de RSI, ADX y horario antes de evaluar
    rsi_val = ctx.get("rsi", 50)
    adx_val = ctx.get("adx", 20)
    current_time = datetime.now()
    volatility_val = ctx.get("atr_pct", 0)

    # [v118] Determinar prospecto de modo (Shadow/Real) para bypass de filtros
    prob_prospect = prob_final
    is_shadow_prospect = prob_prospect < (Config.REAL_CONFIDENCE_MIN * 100)

    (
        filter_passed,
        filter_reason,
        market_regime,
        adaptive_filters,
    ) = Strategy.check_entry_filters(
        rsi_val,
        adx_val,
        current_time,
        audit_signal,
        volatility_val,
        vol_rel,
        is_shadow=is_shadow_prospect,
    )

    # [SHOCK MAP] Veto por falta de espacio operativo
    # Regla: si la distancia al próximo SHOCK < 1.0%, no se dispara.
    if filter_passed and audit_signal in ["BUY", "SELL"]:
        shock_dist_pct, shock_level = bot._get_shock_distance_pct(df_main, audit_signal)
        if ctx is not None:
            ctx["shock_dist_pct"] = shock_dist_pct
            ctx["shock_level"] = shock_level

        min_shock_dist = float(getattr(Config, "SHOCK_MIN_DIST_PCT", 1.0))
        if shock_dist_pct is not None and shock_dist_pct < min_shock_dist:
            # Breakout Hunter (pasivo): poner en acecho si IA es fuerte.
            if bool(getattr(Config, "BREAKOUT_WATCH_ENABLED", True)):
                added_watch = bot.breakout_agent.add_to_watchlist(
                    symbol=symbol,
                    side=audit_signal,
                    ia_prob=float(prob_final),
                    shock_level=float(shock_level) if shock_level is not None else 0.0,
                    trend=str(ctx.get("trend", "RANGO")),
                    metadata={
                        "source": "SHOCK_VETO",
                        "shock_dist_pct": shock_dist_pct,
                        "regime": bot._get_market_regime(),
                    },
                    min_ia_prob=float(
                        getattr(
                            Config,
                            "BREAKOUT_SHOCK_MIN_IA_PROB",
                            getattr(
                                Config,
                                "BREAKOUT_MIN_IA_PROB",
                                60.0,
                            ),
                        )
                    ),
                )
                if added_watch:
                    bot.log(
                        f"👁️ [ACECHO:SHOCK] {symbol} side={audit_signal} IA={prob_final:.1f}% "
                        f"shock={float(shock_level):.6f} dist={shock_dist_pct:.2f}%"
                    )
            filter_passed = False
            filter_reason = (
                f"SHOCK DEMASIADO CERCA ({shock_dist_pct:.2f}% < {min_shock_dist:.2f}%)"
            )

    # Breakout Hunter (pasivo): evaluar ruptura con el df ya cargado (sin API extra)
    breakout_ready = False
    breakout_info = None
    if bool(getattr(Config, "BREAKOUT_WATCH_ENABLED", True)):
        breakout_ready, breakout_info = bot.breakout_agent.evaluate_breakout(
            symbol, df_main
        )
        if breakout_ready and breakout_info is not None:
            bot.log(
                f"🚀 BREAKOUT_READY {symbol} side={breakout_info['side']} "
                f"close={breakout_info['breakout_close']:.6f} "
                f"shock={breakout_info['shock_level']:.6f} "
                f"vol={breakout_info['volume_now']:.2f}/{breakout_info['volume_avg20']:.2f}"
            )
            ctx["breakout_ready"] = True
            ctx["breakout_info"] = breakout_info

    # [v118] FILTRO BLACKLIST DE SÍMBOLOS (Mejorado)
    blacklist = getattr(Config, "SYMBOL_BLACKLIST", [])
    base_sym = symbol.split("/")[0]
    if symbol in blacklist or base_sym in [b.split("/")[0] for b in blacklist]:
        filter_passed = False
        filter_reason = f"VETO: Símbolo en blacklist ({symbol})"
        bot.log(f"⛔ {symbol} vetado: en blacklist")

    # Aplicar pesos de día/hora a la probabilidad IA
    day_weight = adaptive_filters.get("DAY_WEIGHT", 1.0)
    hour_weight = adaptive_filters.get("HOUR_WEIGHT", 1.0)

    # Info de pesos
    ctx["day_weight"] = day_weight
    ctx["hour_weight"] = hour_weight
    ctx["market_regime"] = market_regime

    # Loguear pesos
    if day_weight > 1.1 or hour_weight > 1.1:
        bot.log(
            f"⚡ {symbol}: Día x{day_weight:.2f}, Hora x{hour_weight:.2f} - MEJOR MOMENTO!"
        )

    # --- CÁLCULO ANTICIPADO DE IA (AUDITORÍA) ---
    prob_ia = 0.0
    adjustment_reason = "N/A"
    # [v118 FIX] prob_ia solo se usa en _calculate_quant_consensus
    # para el radar visual, NO como entrada adicional a decisión.
    # Ghost (G) ya contribuye DENTRO de p_final con su peso ponderado.
    # Usar votos.G aquí de nuevo sería double-counting.
    # Asignamos directamente prob_final escalado para coherencia.
    prob_ia = prob_final / 100.0

    # --- PROTOCOLO CIRUGÍA LÁSER: CONSENSO CUÁNTICO ---
    # Aplicamos el filtro del Senior Quant Strategist sobre la probabilidad bruta
    if prob_ia > 0:
        prob_ia, adjustment_reason = bot._calculate_quant_consensus(prob_ia, ctx)

    # [NUEVO v118] Aplicar pesos de día/hora a la probabilidad IA
    day_weight = ctx.get("day_weight", 1.0)
    hour_weight = ctx.get("hour_weight", 1.0)
    combined_weight = (day_weight + hour_weight) / 2

    # === [NUEVO] PONDERACIÓN POR RÉGIMEN BTC ===
    # Ajustar probabilidad según alineación con régimen de BTC
    btc_regime = bot._get_market_regime()
    regime_weight = 1.0
    regime_reason = "N/A"

    if btc_regime == "BULL_TREND":
        if audit_signal == "BUY":
            regime_weight = 1.15  # Bonus +15% para LONG en bull
            regime_reason = "BULL_ALINGED"
        else:
            regime_weight = 0.85  # Penalty -15% para SHORT en bull
            regime_reason = "BULL_COUNTER"
    elif btc_regime == "BEAR_TREND":
        if audit_signal == "SELL":
            regime_weight = 1.15  # Bonus +15% para SHORT en bear
            regime_reason = "BEAR_ALIGNED"
        else:
            regime_weight = 0.85  # Penalty -15% para LONG en bear
            regime_reason = "BEAR_COUNTER"
    else:
        regime_reason = "RANGE_NEUTRAL"

    final_weight = combined_weight * regime_weight
    if regime_weight != 1.0:
        bot.log(f"📊 {symbol}: BTC={btc_regime} [{regime_reason}] x{regime_weight:.2f}")

    # [v118 paso B] BYPASS TEMPORAL PARA ELITE/GOLD
    # Conservamos la probabilidad original antes de aplicar pesos temporales
    original_prob = prob_final
    tier_current = ctx.get("tier", "IRON")

    if tier_current in ["ELITE", "GOLD"] and original_prob >= 80.0:
        if final_weight < 1.0:
            bot.log(
                f"⚡ [BYPASS] {symbol} ({tier_current}): Ignorando penalización temporal (x{final_weight:.2f})"
            )
            prob_final = original_prob  # Bypass total
    else:
        prob_final = min(original_prob * final_weight, 100)

    if final_weight != 1.0:
        bot.log(
            f"⚖️ {symbol}: Prob {original_prob:.1f} → {prob_final:.1f} (x{final_weight:.2f})"
        )

    return prob_final, filter_passed, filter_reason, ctx


def _plan_execution_mode(
    bot,
    symbol,
    audit_signal,
    prob_final,
    audit_verdict,
    filter_passed,
    filter_reason,
    ctx,
):
    is_shadow_exec = True
    should_execute = False

    REAL_THRESHOLD = Config.REAL_CONFIDENCE_MIN * 100
    SHADOW_MIN_THRESHOLD = float(
        getattr(
            Config,
            "SHADOW_MODE_MIN",
            Config.SHADOW_PROB_MIN * 100,
        )
    )

    breakout_shadow_override = False
    if (
        bool(getattr(Config, "BREAKOUT_SEMI_ACTIVE_SHADOW", True))
        and audit_signal != "NEUTRAL"
        and not filter_passed
        and bool(ctx.get("breakout_ready", False))
        and "SHOCK DEMASIADO CERCA" in str(filter_reason)
        and prob_final
        >= max(
            SHADOW_MIN_THRESHOLD, float(getattr(Config, "BREAKOUT_MIN_IA_PROB", 60.0))
        )
    ):
        breakout_shadow_override = True
        is_shadow_exec = True
        should_execute = True
        bot.breakout_overrides_today += 1
        audit_verdict = f"🧪 BREAKOUT SHADOW READY (IA {prob_final:.1f}%)"
        bot.log(
            f"🧨 BREAKOUT OVERRIDE SHADOW: {symbol} [{audit_signal}] IA={prob_final:.1f}%"
        )

    if bool(getattr(Config, "DIRECTIONAL_COHERENCE_FILTER", True)):
        sentiment_label = str(bot.current_sentiment[0])
        is_bull = "ALCISTA" in sentiment_label
        is_bear = "BAJISTA" in sentiment_label
        extreme_breakout_ok = breakout_shadow_override and prob_final >= float(
            getattr(Config, "BREAKOUT_EXTREME_IA_PROB", 75.0)
        )

        if audit_signal == "SELL" and is_bull and not extreme_breakout_ok:
            should_execute = False
            filter_passed = False
            filter_reason = "COHERENCIA: SELL bloqueado en régimen ALCISTA"
            audit_verdict = f"⛔ VETO: {filter_reason}"
            if bool(getattr(Config, "BREAKOUT_WATCH_ENABLED", True)) and bool(
                getattr(Config, "BREAKOUT_WATCH_COHERENCE_ENABLED", True)
            ):
                shock_level_coh = ctx.get("shock_level") if ctx else None
                if shock_level_coh is not None:
                    added_watch = bot.breakout_agent.add_to_watchlist(
                        symbol=symbol,
                        side=audit_signal,
                        ia_prob=float(prob_final),
                        shock_level=float(shock_level_coh),
                        trend=str(ctx.get("trend", "RANGO")) if ctx else "RANGO",
                        metadata={
                            "source": "COHERENCE_VETO",
                            "shock_dist_pct": ctx.get("shock_dist_pct")
                            if ctx
                            else None,
                            "regime": bot._get_market_regime(),
                            "sentiment": sentiment_label,
                            "reason": filter_reason,
                        },
                        min_ia_prob=float(
                            getattr(
                                Config,
                                "BREAKOUT_COHERENCE_MIN_IA_PROB",
                                getattr(Config, "BREAKOUT_MIN_IA_PROB", 60.0),
                            )
                        ),
                    )
                    if added_watch:
                        bot.log(
                            f"👁️ [ACECHO:COHERENCIA] {symbol} side={audit_signal} IA={prob_final:.1f}% sentiment={sentiment_label}"
                        )
        elif audit_signal == "BUY" and is_bear and not extreme_breakout_ok:
            should_execute = False
            filter_passed = False
            filter_reason = "COHERENCIA: BUY bloqueado en régimen BAJISTA"
            audit_verdict = f"⛔ VETO: {filter_reason}"
            if bool(getattr(Config, "BREAKOUT_WATCH_ENABLED", True)) and bool(
                getattr(Config, "BREAKOUT_WATCH_COHERENCE_ENABLED", True)
            ):
                shock_level_coh = ctx.get("shock_level") if ctx else None
                if shock_level_coh is not None:
                    added_watch = bot.breakout_agent.add_to_watchlist(
                        symbol=symbol,
                        side=audit_signal,
                        ia_prob=float(prob_final),
                        shock_level=float(shock_level_coh),
                        trend=str(ctx.get("trend", "RANGO")) if ctx else "RANGO",
                        metadata={
                            "source": "COHERENCE_VETO",
                            "shock_dist_pct": ctx.get("shock_dist_pct")
                            if ctx
                            else None,
                            "regime": bot._get_market_regime(),
                            "sentiment": sentiment_label,
                            "reason": filter_reason,
                        },
                        min_ia_prob=float(
                            getattr(
                                Config,
                                "BREAKOUT_COHERENCE_MIN_IA_PROB",
                                getattr(Config, "BREAKOUT_MIN_IA_PROB", 60.0),
                            )
                        ),
                    )
                    if added_watch:
                        bot.log(
                            f"👁️ [ACECHO:COHERENCIA] {symbol} side={audit_signal} IA={prob_final:.1f}% sentiment={sentiment_label}"
                        )

    if not breakout_shadow_override and audit_signal != "NEUTRAL" and filter_passed:
        if prob_final >= REAL_THRESHOLD:
            is_shadow_exec = False
            should_execute = True
            bot.log(f"🔥 DISPARO REAL: {symbol} confianza {prob_final:.1f}%")
        elif prob_final >= SHADOW_MIN_THRESHOLD:
            is_shadow_exec = True
            should_execute = True
            bot.log(f"🧪 DISPARO SHADOW: {symbol} confianza {prob_final:.1f}%")

    if (
        not should_execute
        and audit_signal != "NEUTRAL"
        and prob_final >= SHADOW_MIN_THRESHOLD
    ):
        if (
            "SCOUT" in audit_verdict
            or "OK" in audit_verdict
            or "CONCESIÓN" in audit_verdict
        ):
            is_shadow_exec = True
            should_execute = True
            bot.log(f"🔍 DEGRADACION A SHADOW: {symbol} (Veredicto: {audit_verdict})")

    return should_execute, is_shadow_exec, audit_verdict, filter_passed, filter_reason


def _resolve_audit_verdict_and_stats(
    bot,
    symbol,
    audit_signal,
    prob_final,
    ob_status,
    pnl_real_hoy,
    mode,
    ctx,
    filter_passed,
    filter_reason,
    ml_pure_prob,
    signal_stats,
):
    prob_ia_consensus = prob_final / 100.0
    audit_verdict = bot.get_audit_verdict(
        symbol,
        prob_ia_consensus,
        audit_signal,
        ob_status,
        pnl_real_hoy,
        bot.current_target,
        mode,
        ctx,
    )

    if not filter_passed:
        audit_verdict = f"⛔ VETO: {filter_reason}"
        if bool(ctx.get("breakout_ready", False)):
            audit_verdict += " | 👁️ BREAKOUT READY"
        bot.log(f"⛔ {symbol} vetado: {filter_reason}")
    elif prob_final > 95.0:
        bot.log(
            f"🚨 [KILL SWITCH] {symbol}: VETO por sobreconfianza ({prob_final:.1f}%). Posible overfitting."
        )
        audit_verdict = f"⛔ VETO: ML_CONF {prob_final:.1f}%"

    if ml_pure_prob >= 75.0 and "VETO" in audit_verdict:
        conflict_msg = (
            f"[A/B TEST CONFLICT] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {symbol} | "
            f"ML_CONFIDENCE: {ml_pure_prob:.1f}% -> QUERÍA OPERAR ({audit_signal}) PERO FUE VETADO POR: {audit_verdict}\n"
        )
        try:
            with open("conflict_ab.log", "a", encoding="utf-8") as handle:
                handle.write(conflict_msg)
        except Exception as error:
            bot.log(f"⚠️ No se pudo registrar conflicto A/B en {symbol}: {error}")
    elif ml_pure_prob < 50.0 and ("OK" in audit_verdict or "SHADOW" in audit_verdict):
        conflict_msg = (
            f"[A/B TEST CONFLICT] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {symbol} | "
            f"ML_CONFIDENCE: {ml_pure_prob:.1f}% -> QUERÍA ABORTAR PERO REGLAS APROBARON OPERAR ({audit_signal})\n"
        )
        try:
            with open("conflict_ab.log", "a", encoding="utf-8") as handle:
                handle.write(conflict_msg)
        except Exception as error:
            bot.log(f"⚠️ No se pudo registrar conflicto A/B en {symbol}: {error}")

    if symbol in bot.cooldown_pairs and datetime.now() < bot.cooldown_pairs[symbol]:
        remaining = (
            int((bot.cooldown_pairs[symbol] - datetime.now()).total_seconds() / 60) + 1
        )
        audit_verdict = f"❄️ COOLDOWN ({remaining}m)"

    if (
        "VETO" in audit_verdict
        or "BLOQUEADO" in audit_verdict
        or "COOLDOWN" in audit_verdict
        or "RIESGO" in audit_verdict
    ):
        signal_stats["VETO"] += 1
    elif "SHADOW" in audit_verdict or "CONCESIÓN" in audit_verdict:
        signal_stats["SHADOW"] += 1
    elif "OK" in audit_verdict:
        signal_stats["REAL"] += 1

    return audit_verdict
