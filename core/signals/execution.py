import time


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
    veto_codes = {
        "SYMBOL_BLOCKED_MATRIX": "SÍMBOLO BLOQUEADO (MATRIX)",
        "INSUFFICIENT_BALANCE_MIN_NOTIONAL": "SALDO INSUFICIENTE (MIN_NOTIONAL)",
        "MAX_REAL_TRADES": "LÍMITE MÁXIMO REAL",
        "MAX_DIRECTIONAL": "LÍMITE DIRECCIONAL",
        "MAX_SHADOW": "LÍMITE MÁXIMO SHADOW",
        "DUPLICATE_REAL_COIN": "POSICIÓN REAL YA EXISTE",
        "BOT_PAUSED": "BOT EN PAUSA",
        "INTEGRITY_LOCK_ACTIVE": "INTEGRITY LOCK ACTIVO",
        "GLOBAL_COOLDOWN": "COOLDOWN GLOBAL ACTIVO",
        "HALT_SYSTEM_ACTIVE": "HALT SYSTEM ACTIVO",
        "TRADING_HALTED_DB_ERROR": "BLOQUEO SEGURIDAD DB",
        "CIRCUIT_BREAKER_PANIC": "CIRCUIT BREAKER",
        "TP_INSUFFICIENT": "TP INSUFICIENTE",
    }

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
            bot.log(
                f"✅ GATILLO {modo_str}: {symbol} [{audit_signal}] -> {audit_verdict}"
            )
            with bot.lock:
                if symbol in bot.active_trades:
                    final_verdict_for_ui = "⚡ OPEN | 🔒 OPERACIÓN ACTIVA"
                else:
                    final_verdict_for_ui = audit_verdict

            if "DEGRADED" in exec_result:
                deg_msg = (
                    exec_result.split(": ")[1] if ": " in exec_result else "PROTECTION"
                )
                audit_verdict = f"🧪 SHADOW (PROT: {deg_msg})"
                for item in bot.scanner_history:
                    if item["symbol"] == symbol:
                        item["result"] = audit_verdict
                        break
        elif exec_result not in ["COOLDOWN", "ALREADY_ACTIVE"]:
            error_msg = (
                exec_result.split(": ")[0] if ": " in exec_result else exec_result
            )
            bot.log(f"❌ FALLO EJECUCIÓN {symbol}: {exec_result}")
            if error_msg in veto_codes:
                final_verdict_for_ui = f"⛔ VETO: {veto_codes[error_msg]}"
            else:
                final_verdict_for_ui = f"❌ ERR: {error_msg}"
            for item in bot.scanner_history:
                if item["symbol"] == symbol:
                    item["result"] = final_verdict_for_ui
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
