import ccxt
import requests

from config import Config


def _build_exchange(session):
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
        "session": session,
        "timeout": 30000,
    }
    return ccxt.binance(exchange_config)


def _is_public_sandbox_limitation(error) -> bool:
    return "does not have a testnet/sandbox URL for public endpoints" in str(error)


def connect_to_binance(bot):
    try:
        bot.log("Conectando a Binance...")

        # [v118] Soporte para Testnet
        # [V118-PRO] Session pooling para evitar fugas de sockets
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=100, pool_maxsize=100, max_retries=3, pool_block=False
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        exchange = _build_exchange(session)
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
        try:
            bot.execution.load_markets()
        except Exception as error:
            if Config.PAPER_MODE and Config.USE_TESTNET and _is_public_sandbox_limitation(error):
                bot.log(
                    "⚠️ Sandbox/testnet no soporta endpoints públicos en este backend. "
                    "Continuando en PAPER con mercado público real."
                )
                exchange = _build_exchange(session)
                bot.execution.exchange = exchange
                bot.data_service.exchange = bot.execution.exchange
                bot.execution.load_markets()
            else:
                raise

        if Config.PAPER_MODE:
            if not float(getattr(bot, "balance", 0.0) or 0.0):
                bot.balance = float(getattr(Config, "PAPER_INITIAL_BALANCE", 1000.0))
            if not float(getattr(bot, "available_balance", 0.0) or 0.0):
                bot.available_balance = float(
                    getattr(Config, "PAPER_INITIAL_BALANCE", 1000.0)
                )
            if not float(getattr(bot, "daily_initial_balance", 0.0) or 0.0):
                bot.daily_initial_balance = float(
                    getattr(Config, "PAPER_INITIAL_BALANCE", 1000.0)
                )
            if Config.BINANCE_API_KEY and Config.BINANCE_API_SECRET:
                try:
                    bot.execution.fetch_balance()
                    bot.log("✅ Conectado: API Keys válidas y permisos de Futuros activos.")
                except Exception as error:
                    bot.log(
                        "⚠️ PAPER_MODE: credenciales Binance no válidas o no operativas. "
                        f"Se continúa solo con endpoints públicos: {error}"
                    )
            else:
                bot.log("ℹ️ PAPER_MODE: sin API keys, usando solo endpoints públicos.")
            bot.is_hedge_mode = False
            bot.log(
                f"🧾 PAPER capital virtual inicializado en ${float(getattr(Config, 'PAPER_INITIAL_BALANCE', 1000.0)):.2f}"
            )
        else:
            # Verificación explícita de permisos
            try:
                bot.execution.fetch_balance()
                bot.log("✅ Conectado: API Keys válidas y permisos de Futuros activos.")

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

        if not Config.PAPER_MODE:
            bot.sync_wallet()
        bot.log(
            f"🛡️ MODO OPERATIVO: {'📝 PAPER (Simulado)' if Config.PAPER_MODE else '🔥 REAL (Dinero Real)'}"
        )
    except Exception as error:
        bot.log(f"❌ ERROR FATAL: {error}")
        raise RuntimeError(
            f"No se pudo inicializar conexión Binance: {error}"
        ) from error
