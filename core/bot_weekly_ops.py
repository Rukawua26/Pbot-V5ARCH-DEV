from datetime import datetime

from notifier import send_telegram_msg


def check_weekly_schedule(bot, module_available_fn):
    """Envía el reporte de evolución los domingos a las 20:00."""
    now_local = datetime.now()
    if now_local.weekday() == 6 and now_local.hour == 20 and now_local.minute == 0:
        if not bot._weekly_sent:
            try:
                if module_available_fn("evolution_logger"):
                    from evolution_logger import get_evolution_report

                    report = get_evolution_report()
                    send_telegram_msg(
                        f"📊 *RESUMEN DE CRECIMIENTO SEMANAL*\n\n{report}"
                    )
                else:
                    bot.log(
                        "ℹ️ Resumen semanal omitido: evolution_logger no disponible."
                    )
            except Exception as error:
                bot.log(f"⚠️ Error en reporte semanal: {error}")
            bot._weekly_sent = True
    elif now_local.hour != 20:
        bot._weekly_sent = False


def check_weekly_maintenance_utc(bot):
    """Domingo 00:00 UTC: purga shadow >30d y VACUUM en sniper_brain.db."""
    now_utc = datetime.utcnow()
    maintenance_key = f"{now_utc.isocalendar().year}-W{now_utc.isocalendar().week}"

    if now_utc.weekday() == 6 and now_utc.hour == 0 and now_utc.minute < 5:
        if bot._last_weekly_maintenance_utc != maintenance_key:
            bot.log("🧹 Mantenimiento semanal DB (UTC): iniciando purge+VACUUM...")
            result = bot.brain.weekly_maintenance(shadow_days_to_keep=30)
            if result.get("error"):
                bot.log(f"⚠️ Mantenimiento DB falló: {result['error']}")
            else:
                bot.log(
                    f"✅ Mantenimiento DB OK: shadow_deleted={result.get('shadow_deleted', 0)} cutoff={result.get('cutoff')} vacuum={result.get('vacuum_ok', False)}"
                )
            bot._last_weekly_maintenance_utc = maintenance_key
