from datetime import datetime

from config import Config


def sync_wallet(bot):
    try:
        # Usamos fetch_positions para obtener datos precisos y unificados
        # [FIX] Race Condition: Snapshot de active_trades antes de la llamada de red
        with bot.lock:
            active_trades_snapshot = bot.active_trades.copy()

        positions = bot.execution.fetch_positions()
        real_active_on_binance = {}

        # PROTECCIÓN DE INTEGRIDAD: Si Binance devuelve lista vacía pero tenemos trades REALES activos,
        # podría ser un error de API. Verificamos balance para confirmar que no es un error de conexión.
        if not positions and any(
            not trade.get("is_shadow") for trade in active_trades_snapshot.values()
        ):
            if bot.get_current_balance() == 0:
                return  # Si balance es 0 y pos es 0, ok. Si no, sospechoso.

        for pos in positions:
            amount = float(pos.get("contracts") or 0)
            if abs(amount) > 0:
                # Determinación robusta del lado (Long/Short)
                side = "BUY"
                if pos.get("side") == "short":
                    side = "SELL"
                elif pos.get("side") == "long":
                    side = "BUY"
                else:
                    # Fallback a raw info si ccxt no normalizó el side
                    raw_amt = float(pos["info"].get("positionAmt", 0))
                    side = "BUY" if raw_amt > 0 else "SELL"

                # Normalización robusta para evitar purgas erróneas
                raw_symbol = pos["symbol"].split(":")[0]
                # FIX: Usamos slicing negativo ([:-4]) para no romper BTC (3 letras)
                symbol = (
                    raw_symbol
                    if "/" in raw_symbol
                    else (
                        f"{raw_symbol[:-4]}/{raw_symbol[-4:]}"
                        if raw_symbol.endswith("USDT")
                        else raw_symbol
                    )
                )
                if symbol == "WLF I/USDT":
                    symbol = "WLFI/USDT"  # Corrección específica

                real_active_on_binance[symbol] = {
                    "amount": abs(amount),
                    "side": side,
                    "entry": float(pos.get("entryPrice") or 0),
                    "pnl": float(pos.get("unrealizedPnl") or 0),
                }

        # LOG DE DIAGNÓSTICO: Ver qué detecta Binance
        if real_active_on_binance:
            bot.log(
                f"🔍 Wallet Sync: Binance reporta {list(real_active_on_binance.keys())}"
            )

        with bot.lock:
            # Aseguramos actualización de saldo (ATÓMICO v106.0)
            bot.balance = bot.get_current_balance()

            # A. ACTUALIZACIÓN DE PRECIOS REALES (Corrige el PnL)
            for symbol, info in real_active_on_binance.items():
                if symbol in bot.active_trades and not bot.active_trades[symbol].get(
                    "is_shadow"
                ):
                    # Sincronizamos el precio de entrada del bot con el de Binance
                    # Validamos que el precio sea > 0 para evitar errores de API
                    if (
                        info["entry"] > 0
                        and bot.active_trades[symbol]["entry"] != info["entry"]
                    ):
                        bot.log(
                            f"⚖️ Sincronizando precio {symbol}: {bot.active_trades[symbol]['entry']} -> {info['entry']}"
                        )
                        bot.active_trades[symbol]["entry"] = info["entry"]
                        bot.active_trades[symbol]["amount"] = info["amount"]
                        bot.active_trades[symbol]["size_usd"] = (
                            info["entry"] * info["amount"]
                        )

            # B. PURGAR trades huerfanos (No están en Binance pero sí en el bot)
            for symbol in list(bot.active_trades.keys()):
                trade = bot.active_trades[symbol]
                if (
                    not trade.get("is_shadow")
                    and symbol not in real_active_on_binance
                    and not Config.PAPER_MODE
                ):
                    # PROTECCIÓN DE LATENCIA: No purgar si el trade tiene menos de 60 segundos
                    open_time = trade.get("open_time")
                    if isinstance(open_time, str):
                        open_time = datetime.fromisoformat(open_time)
                    if (datetime.now() - open_time).total_seconds() < 120:
                        continue

                    bot.log(f"🧹 Purgando manual: {symbol}")
                    del bot.active_trades[symbol]
                    bot.brain.delete_active_trade_state(symbol)

            # C. ADOPTAR trades nuevos (Si abres algo manual en Binance)
            for symbol, info in real_active_on_binance.items():
                if symbol not in bot.active_trades:
                    bot.log(
                        f"📥 CARTERA: Detectado nuevo trade en Binance: {symbol}. Sincronizando..."
                    )
                    base = symbol.split("/")[0]
                    sector = next(
                        (
                            key
                            for key, values in Config.SECTORS.items()
                            if any(item.lower() in base.lower() for item in values)
                        ),
                        "OTHE",
                    )
                    sl = (
                        info["entry"] * 0.95
                        if info["side"] == "BUY"
                        else info["entry"] * 1.05
                    )

                    bot.active_trades[symbol] = {
                        "symbol": symbol,
                        "side": info["side"],
                        "entry": info["entry"],
                        "amount": info["amount"],
                        "size_usd": info["entry"] * info["amount"],
                        "open_time": datetime.now(),
                        "pnl": 0.0,
                        "is_shadow": False,
                        "simulated_real": False,
                        "sector": sector,
                        "sl": sl,
                        "tp": 0.0,
                        "trailing_active": False,
                        "early_be_armed": False,
                        "mae_price": info["entry"],
                        "mfe_price": info["entry"],
                        "market_snapshot": {
                            "prob_final": 99.0,
                            "votos": {"G": 99.0},
                            "is_adopted": True,
                        },
                    }
    except Exception as error:
        bot.log(f"⚠️ Error Sync: {error}")
