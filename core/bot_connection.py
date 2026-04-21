import ccxt
import requests

from config import Config


def connect_to_binance(bot):
    try:
        bot.log("Conectando a Binance...")

        # [v118] Soporte para Testnet
        # [V118-PRO] Session pooling para evitar fugas de sockets
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=10, max_retries=3, pool_block=False
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        exchange_config = {
            "apiKey": Config.BINANCE_API_KEY,
            "secret": Config.BINANCE_API_SECRET,
            "options": {
                "defaultType": "future",
                "recvWindow": 60000,
                "fetchCurrencies": False,
                "warnOnFetchOpenOrdersWithoutSymbol": False,
            },
            "enableRateLimit": True,
            "adjustForTimeDifference": True,
            "session": session,  # [V118-PRO] Session pooling
            "timeout": 30000,  # FIX: ccxt espera el timeout en ms (30000 ms = 30s)
        }

        exchange = ccxt.binance(exchange_config)
        if Config.USE_TESTNET:
            bot.log("⚠️ MODO TESTNET ACTIVADO")
            if not hasattr(exchange, "set_sandbox_mode"):
                raise RuntimeError(
                    "La clase de exchange actual no soporta sandbox/testnet de forma nativa."
                )
            try:
                exchange.set_sandbox_mode(True)
            except Exception as error:
                raise RuntimeError(
                    f"No se pudo activar testnet/sandbox en Binance Futures: {error}"
                ) from error

        bot.execution.exchange = exchange
        bot.data_service.exchange = bot.execution.exchange
        bot.execution.load_markets()

        # Verificación explícita de permisos
        try:
            bot.execution.fetch_balance()
            bot.log("✅ Conectado: API Keys válidas y permisos de Futuros activos.")

            # --- DETECCIÓN DE HEDGE MODE (v105.6 FIX) ---
            try:
                # Detectar si la cuenta está en Hedge Mode o One-Way
                # FIX: Usar símbolo válido para evitar error de parámetro
                if hasattr(bot.execution.exchange, "fetch_position_mode"):
                    try:
                        # Intentar primero con símbolo BTC
                        mode = bot.execution.fetch_position_mode(symbol="BTC/USDT:USDT")
                        bot.is_hedge_mode = mode.get("hedged", False)
                    except Exception:
                        # Fallback: intentar sin símbolo
                        mode = bot.execution.fetch_position_mode()
                        bot.is_hedge_mode = mode.get("hedged", False)
                else:
                    # Fallback a endpoint directo
                    mode = bot.execution.get_position_side_dual()
                    bot.is_hedge_mode = mode["dualSidePosition"]
                bot.log(
                    f"ℹ️ Modo de Posición: {'HEDGE' if bot.is_hedge_mode else 'ONE-WAY'}"
                )
            except Exception as error:
                # No es crítico, asumimos One-Way por defecto
                bot.is_hedge_mode = False
                bot.log(
                    f"⚠️ No se pudo detectar modo Hedge/OneWay, asumiendo ONE-WAY: {error}"
                )
        except Exception as error:
            bot.log(
                f"❌ CONEXIÓN RECHAZADA: Error verificando permisos/balance. Revise sus API Keys. {error}"
            )
            raise RuntimeError(
                f"Credenciales/permisos Binance inválidos o insuficientes: {error}"
            ) from error

        bot.sync_wallet()
        bot.log(
            f"🛡️ MODO OPERATIVO: {'📝 PAPER (Simulado)' if Config.PAPER_MODE else '🔥 REAL (Dinero Real)'}"
        )
    except Exception as error:
        bot.log(f"❌ ERROR FATAL: {error}")
        raise RuntimeError(
            f"No se pudo inicializar conexión Binance: {error}"
        ) from error
