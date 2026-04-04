def _execute_and_update_symbol(
    bot,
    symbol_raw,
    symbol,
    audit_signal,
    prob_final,
    audit_verdict,
    should_execute,
    is_shadow_exec,
    df_main,
    ctx,
    ob_status,
    votos,
    decision,
    elapsed,
):
    final_verdict_for_ui = audit_verdict
    if should_execute:
        if ctx:
            ctx["votos"] = votos
            ctx["prob_final"] = prob_final
            ctx["audit_verdict"] = audit_verdict

        exec_result = bot.execute_order(
            symbol=symbol,
            side=audit_signal,
            price=df_main["close"].iloc[-1],
            atr=ctx.get("atr", 0) if ctx else 0,
            is_shadow=is_shadow_exec,
            context=ctx,
            ob_status=ob_status,
            override_usd_size=0.0,
        )

        if exec_result.startswith("OK"):
            modo_str = "REAL" if not is_shadow_exec else "SHADOW"
            bot.log(f"✅ GATILLO {modo_str}: {symbol} [{audit_signal}] -> {audit_verdict}")
            with bot.lock:
                if symbol in bot.active_trades:
                    final_verdict_for_ui = "⚡ OPEN | 🔒 OPERACIÓN ACTIVA"
                else:
                    final_verdict_for_ui = audit_verdict

            if "DEGRADED" in exec_result:
                deg_msg = exec_result.split(": ")[1] if ": " in exec_result else "PROTECTION"
                audit_verdict = f"🧪 SHADOW (PROT: {deg_msg})"
                for item in bot.scanner_history:
                    if item["symbol"] == symbol:
                        item["result"] = audit_verdict
                        break
        elif exec_result not in ["COOLDOWN", "ALREADY_ACTIVE"]:
            error_msg = exec_result.split(": ")[0] if ": " in exec_result else exec_result
            bot.log(f"❌ FALLO EJECUCIÓN {symbol}: {exec_result}")
            final_verdict_for_ui = f"❌ ERR: {error_msg}"
            for item in bot.scanner_history:
                if item["symbol"] == symbol:
                    item["result"] = f"❌ ERR: {error_msg}"
                    item["ia_real"] = "❌"
                    item["ia_shadow"] = "❌"
                    break
        else:
            final_verdict_for_ui = (
                "❄️ COOLDOWN" if exec_result == "COOLDOWN" else "🔒 OPERACIÓN ACTIVA"
            )

    bot.update_radar(
        symbol_raw,
        decision,
        prob_final / 100.0,
        ob_status,
        final_verdict_for_ui,
        ctx,
        votos,
        response_ms=elapsed,
    )
    time.sleep(0.05)
