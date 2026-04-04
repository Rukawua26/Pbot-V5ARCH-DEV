from core.commands.audit import _handle_audit_commands
from core.commands.history import _handle_history_commands
from core.commands.intelligence import _handle_intelligence_commands
from core.commands.ops import _handle_misc_commands, _handle_training_and_maintenance_commands, _help_message


def handle_basic_command(bot, text: str) -> bool:
    if _handle_audit_commands(bot, text):
        return True

    if _handle_intelligence_commands(bot, text):
        return True

    if _handle_history_commands(bot, text):
        return True

    if _handle_misc_commands(bot, text):
        return True

    if _handle_training_and_maintenance_commands(bot, text):
        return True

    if text == "/help":
        from notifier import send_telegram_msg
        send_telegram_msg(_help_message())
        return True

    if text in ["/on", "/resume"]:
        from notifier import send_telegram_msg
        if bot.mandatory_train_pending:
            send_telegram_msg(
                "🛡️ *MODO DEFENSIVO ACTIVO*: No se puede reanudar sin re-entrenamiento. Use /force_train."
            )
        else:
            bot.is_paused = False
            send_telegram_msg("🟢 *SISTEMA ACTIVO*")
        return True

    if text in ["/off", "/pause"]:
        from notifier import send_telegram_msg
        bot.is_paused = True
        send_telegram_msg("🟡 *SISTEMA EN PAUSA*")
        return True

    if text in ["/panic", "/closeall"]:
        from notifier import send_telegram_msg
        bot.is_paused = True
        bot._close_all_positions_emergency()
        send_telegram_msg("🔴 *EMERGENCIA*: Todo cerrado en Binance.")
        return True

    if text == "/reset":
        from notifier import send_telegram_msg
        msg = bot.handle_reset_pnl()
        send_telegram_msg(msg)
        return True

    if text == "/test":
        from notifier import send_telegram_msg
        send_telegram_msg(
            "🔔 *PRUEBA DE CONEXIÓN*\nSi estás leyendo esto, las notificaciones de Sniper AI funcionan correctamente."
        )
        return True

    return False
