import time

from config import Config
from core.cooldown_state import cleanup_expired_cooldowns
from core.time_utils import parse_datetime_utc, utc_now


def _is_not_expired_until(value, now_utc):
    try:
        return parse_datetime_utc(value) > now_utc
    except Exception:
        return False


def acquire_targets(bot):
    """Fase 2: Selección Dinámica de Líderes con Prioridad Inteligente (v110.3)"""
    bot.log("🎯 Buscando pares líderes...")
    try:
        now = utc_now()
        # Limpieza de blacklists expiradas
        bot.blacklist = {
            s: e for s, e in bot.blacklist.items() if _is_not_expired_until(e, now)
        }
        cleanup_expired_cooldowns(bot)

        tickers = bot.execution.fetch_tickers(params={"type": "future"})
        # Incluir todos los futuros USDT devueltos por Binance.
        all_future_tickers = [t for s, t in tickers.items() if "/USDT" in s]

        if not all_future_tickers:
            bot.log(
                f"⚠️ Alerta: fetch_tickers devolvió {len(tickers)} items. Reintentando..."
            )
            return {}
        else:
            # 1. Filtramos por volumen para tener un pool robusto (Real + Shadow)
            top_pool = sorted(
                all_future_tickers,
                key=lambda x: x.get("quoteVolume", 0),
                reverse=True,
            )[: Config.MAX_REAL_PAIRS + Config.MAX_SHADOW_PAIRS]

            # 2. Filtro de Volumen y Madurez v118.5
            valid_pool = []
            for t in top_pool:
                symbol = t.get("symbol")
                if not symbol:
                    continue

                # Filtrar por volumen mínimo primero (más rápido)
                if t.get("quoteVolume", 0) < Config.TRIAGE_MIN_VOL_24H:
                    continue

                # Auditoría de madurez del símbolo antes de escanear.
                if not bot.data_service.audit_symbol_maturity(symbol):
                    # Si fue rechazado, lo removemos de cualquier lista activa
                    if symbol in bot.pairs_to_scan:
                        bot.pairs_to_scan.remove(symbol)
                    continue

                valid_pool.append(t)

            # --- PUMP & DUMP PROTECTION (Anti-Burbuja) ---
            valid_pool = [
                t for t in valid_pool if abs(float(t.get("percentage", 0) or 0)) < 40.0
            ]

            # 3. PRIORIZACIÓN INTELIGENTE (v110.3)
            # Categoría A: Precio < $3 Y Alto Volumen
            # Categoría B: Precio < $3 Y Bajo Volumen
            # Categoría C: Precio >= $3
            cat_a = []  # Alta prioridad: precio bajo, alto volumen
            cat_b = []  # Media prioridad: precio bajo, bajo volumen
            cat_c = []  # Baja prioridad: precio alto

            for t in valid_pool:
                symbol = t["symbol"]
                precio = t.get("last", 0)
                volumen = t.get("quoteVolume", 0)

                if precio < Config.PRICE_PRIORITY_LIMIT:
                    if volumen >= 10_000_000:  # $10M+
                        cat_a.append(symbol)
                    else:
                        cat_b.append(symbol)
                else:
                    cat_c.append(symbol)

            # 4. Obtener WR histórico de cada símbolo y reordenar
            def get_symbol_score(sym):
                """Puntaje basado en WR histórico y volumen"""
                try:
                    perf = bot.brain.get_symbol_performance(sym)
                    wr = perf.get("wr", 50)  # 0-100
                    trades = perf.get("trades", 0)

                    # Si tiene trades recientes, usar su WR; si no, usar 50 como neutral
                    if trades >= 5:
                        return wr * 0.7 + (
                            min(trades, 50) * 0.3
                        )  # Ponderar WR y experiencia
                    return 50
                except Exception:
                    return 50

            # Ordenar cada categoría por WR histórico
            cat_a.sort(key=get_symbol_score, reverse=True)
            cat_b.sort(key=get_symbol_score, reverse=True)
            cat_c.sort(key=get_symbol_score, reverse=True)

            # Combinar: 50% cat_a, 30% cat_b, 20% cat_c
            half_a = int(len(cat_a) * Config.RADAR_PRIORITY_HIGH_VOL_LOW_PRICE)
            half_b = int(len(cat_b) * Config.RADAR_PRIORITY_HIGH_WR)

            new_list = (
                cat_a[:half_a]
                + cat_b[:half_b]
                + cat_a[half_a:]
                + cat_c[: int(len(cat_c) * Config.RADAR_PRIORITY_OTHERS)]
                + cat_b[half_b:]
            )

            # 5. Filtrado por Sector Blacklist
            if bot.restricted_sectors:
                new_list = [
                    p
                    for p in new_list
                    if next(
                        (
                            k
                            for k, v in Config.SECTORS.items()
                            if any(s.lower() in p.split("/")[0].lower() for s in v)
                        ),
                        "OTHE",
                    )
                    not in bot.restricted_sectors
                ]

            # Filtrado por blacklist persistida en el brain.
            if hasattr(bot.brain, "get_symbol_blacklist"):
                symbol_blacklist = bot.brain.get_symbol_blacklist()
                # Normalizar blacklist para comparación (quitar /USDT si existe)
                clean_blacklist = [s.split("/")[0] for s in symbol_blacklist]
                if clean_blacklist:
                    new_list = [
                        p for p in new_list if p.split("/")[0] not in clean_blacklist
                    ]
                    bot.log(f"   - 🚫 Símbolos vetados: {clean_blacklist}")

            controls = bot._load_runtime_symbol_controls()
            blocked = controls.get("blocked", set())
            preferred = controls.get("preferred", set())
            if blocked:
                before = len(new_list)
                new_list = [p for p in new_list if p.split("/")[0] not in blocked]
                removed = before - len(new_list)
                if removed > 0:
                    bot.log(
                        f"   - 🧱 Matriz de decisión: {removed} símbolos bloqueados"
                    )

            if preferred:
                preferred_pairs = [p for p in new_list if p.split("/")[0] in preferred]
                others = [p for p in new_list if p.split("/")[0] not in preferred]
                new_list = preferred_pairs + others
                if preferred_pairs:
                    bot.log(
                        f"   - ⭐ Priorización táctica: {len(preferred_pairs)} símbolos MANTENER al frente"
                    )

            bot.pairs_to_scan = new_list

        # La lista se construye desde el mercado activo, no desde Config.PAIRS.
        if len(bot.pairs_to_scan) < Config.TOP_TRIAGE_COUNT:
            bot.log(
                f"⚠️ Solo {len(bot.pairs_to_scan)} pares filtrados (lista dinámica del mercado)."
            )

        # --- FASE 2: FILTRO ANTI-PUMP & DUMP (Volumen Irracional) ---
        # Compara volumen de últimos 15m vs promedio 24h (aprox).
        # Si el volumen reciente es > 500% del promedio, se descarta por riesgo de manipulación.
        safe_list = []
        for p in bot.pairs_to_scan:
            # Blacklist dinámica anti-revenge.
            is_safe, ar_reason = bot.risk_engine.check_anti_revenge_blacklist(p)
            if not is_safe:
                bot.log(
                    f"🚫 [v118] ANTI-REVENGE: {p} bloqueado temporalmente: {ar_reason}"
                )
                continue

            try:
                t = tickers.get(
                    p.replace("/", "") if ":" not in p else p.split(":")[0]
                ) or tickers.get(p)
                if not t:
                    safe_list.append(p)
                    continue

                avg_15m_vol = (
                    float(t["quoteVolume"]) / 96
                    if t.get("quoteVolume") and float(t["quoteVolume"]) > 0
                    else 0.0
                )  # 96 periodos de 15m en 24h
                # Nota: Para ser precisos requeriría fetch_ohlcv, pero por velocidad usamos heurística
                # Si el cambio de precio es > 15% y no es una corrección, sospechamos.
                if (
                    abs(float(t["percentage"])) > 15.0
                    and float(t["quoteVolume"]) < Config.MIN_VOLUME_24H * 2
                ):
                    bot.log(f"⚠️ Anti-Pump: {p} descartado (Volátil/Bajo Liq).")
                    continue
                safe_list.append(p)
            except Exception:
                safe_list.append(p)
        bot.pairs_to_scan = safe_list

        # Inicializar radar con todos los objetivos como PENDING.
        with bot.lock:
            existing_syms = {i["symbol"] for i in bot.scanner_history}
            for p in bot.pairs_to_scan:
                if p not in existing_syms:
                    base = p.split("/")[0]
                    sector = next(
                        (
                            k
                            for k, v in Config.SECTORS.items()
                            if any(s.lower() in base.lower() for s in v)
                        ),
                        "OTHE",
                    )
                    vol_24h = 0.0
                    if tickers:
                        clean_p = p.split(":")[0]
                        if clean_p in tickers:
                            vol_24h = float(tickers[clean_p].get("quoteVolume", 0) or 0)
                        else:
                            for key, val in tickers.items():
                                if key.split("/")[0] == base:
                                    vol_24h = float(val.get("quoteVolume", 0) or 0)
                                    break
                    bot.scanner_history.append(
                        {
                            "symbol": p,
                            "sector": sector,
                            "tech_checklist": "⏳ PENDING",
                            "ob": "⚪",
                            "ia_prob": "---",
                            "ia_shadow": "⏳",
                            "ia_real": "⏳",
                            "result": "EN COLA...",
                            "signal": "WAIT",
                            "rsi_val": 0,
                            "adx_val": 0,
                            "z_score": 0.0,
                            "vol_24h": vol_24h,
                            "trend_val": "N/A",
                            "funding_rate": 0.0,
                            "votos": {},
                        }
                    )

        # Auto-recuperación del precio BTC si no vino en el batch.
        if "BTC/USDT" in tickers or "BTC/USDT:USDT" in tickers:
            btc_ticker = tickers.get("BTC/USDT:USDT", tickers.get("BTC/USDT"))
            bot.market_btc_price = float(btc_ticker["last"])
        elif bot.market_btc_price == 0:
            # Intento forzado si no vino en el paquete
            try:
                btc_t = bot.execution.fetch_ticker("BTC/USDT")
                bot.market_btc_price = float(btc_t["last"])
            except Exception as error:
                bot.log(f"⚠️ No se pudo rescatar BTC ticker en acquire_targets: {error}")

        bot.log(
            f"✅ Radar {Config.VERSION}: {len(bot.pairs_to_scan)} monedas en mira. BTC: ${bot.market_btc_price}"
        )
        bot.log(f"📋 Objetivos: {', '.join(bot.pairs_to_scan)}")
        return tickers

    except Exception as e:
        bot.log(f"⚠️ Error en acquire_targets: {e}")
        # Fallback resiliente: reutilizar snapshot dinámico si está disponible.
        try:
            ranked = bot._get_active_market_snapshot()
            if ranked:
                bot.pairs_to_scan = [
                    r["symbol"]
                    for r in ranked[: int(getattr(Config, "TOP_TRIAGE_COUNT", 50))]
                ]
                bot.log(
                    f"♻️ Fallback acquire_targets: {len(bot.pairs_to_scan)} pares desde snapshot dinámico."
                )
                return {item["symbol"]: item.get("ticker", {}) for item in ranked}
        except Exception as error:
            bot.log(f"⚠️ Fallback snapshot dinámico falló en acquire_targets: {error}")
        # Último intento de rescate de BTC si todo lo demás falla.
        try:
            btc_t = bot.execution.fetch_ticker("BTC/USDT")
            bot.market_btc_price = float(btc_t["last"])
        except Exception as error:
            bot.log(f"⚠️ No se pudo rescatar BTC ticker en fallback final: {error}")
        # No vaciar radar si ya hay lista previa válida.
        if not bot.pairs_to_scan:
            bot.pairs_to_scan = []
        return {}


def get_active_market_snapshot(bot):
    """
    [DINÁMICO] Top liquidez por Config.TOP_TRIAGE_COUNT (default 30).
    
    Lógica:
      - Stateless: Ya no mantiene pares fijos por RVOL.
      - Refresh mercado cada 5 min (peso 40) para armar pool de liquidez diaria.
      - En cada ciclo (peso 1), evalúa spreads reales.
      - Ordena todos los futuros activos por quoteVolume (24h liquidez real).
      - Toma los top Config.TOP_TRIAGE_COUNT pares que pasen el filtro de spread.
    
    Returns:
        List[Dict]: Pares activos ordenados por volumen bruto desc.
    """
    try:
        # Inicializar cachés de mercado si no existen
        if not hasattr(bot, "_market_cache"):
            bot._market_cache = {}
        if not hasattr(bot, "_market_cache_ts"):
            bot._market_cache_ts = 0

        MAX_PAIRS = max(1, int(getattr(Config, "TOP_TRIAGE_COUNT", 25) or 25))
        MIN_VOL = float(getattr(Config, "TRIAGE_MIN_VOL_24H", 15_000_000))

        # [BEAR_TREND] Reducir universo de pares en régimen bajista
        try:
            btc_regime = getattr(bot, "market_regime", "UNKNOWN")
            if btc_regime == "BEAR_TREND":
                MAX_PAIRS = min(
                    MAX_PAIRS,
                    max(1, int(getattr(Config, "BEAR_TREND_MAX_PAIRS", 15) or 15)),
                )
                MIN_VOL = float(getattr(Config, "BEAR_TREND_MIN_VOL", 50_000_000))
        except Exception:
            bot.log("⚠️ BEAR_TREND pair reduction omitido, usando defaults")
            
        MAX_SPREAD = float(getattr(Config, "TRIAGE_SPREAD_MAX", 0.0005))
        MARKET_REFRESH = 300  # 5 min

        # --- CAPA 0: BookTicker para spreads reales (peso ~1) ---
        bid_ask_map = {}
        try:
            book_tickers = bot.execution.fetch_book_tickers()
            for bt in book_tickers:
                raw_sym = bt.get("symbol", "")
                bid_price = float(bt.get("bidPrice", 0) or 0)
                ask_price = float(bt.get("askPrice", 0) or 0)
                if raw_sym and bid_price > 0 and ask_price > 0:
                    bid_ask_map[raw_sym] = {"bid": bid_price, "ask": ask_price}
        except Exception as e:
            bot.log(f"⚠️ [TRIAJE] BookTicker falló: {e}")

        # --- CAPA 1: Refresh del mercado cada 5 min (peso 40) ---
        now = time.time()
        if now - bot._market_cache_ts > MARKET_REFRESH or not bot._market_cache:
            bot.log("📡 [TRIAJE ELITE] Refresh mercado completo (cada 5 min)...")
            try:
                if not bot.execution.has_markets_loaded():
                    bot.execution.load_markets()

                if hasattr(bot, "weight_tracker") and bot.weight_tracker and bot.weight_tracker.should_block("market"):
                    bot.log("🛑 [TRIAJE] Saltando refresh mercado por presión de API Weight")
                else:
                    raw_tickers = bot.execution.fetch_tickers(params={"type": "future"})
                    
                    # Construir pool de candidatos inicial
                    all_candidates = []
                    for symbol, ticker in raw_tickers.items():
                        if not (symbol.endswith("/USDT") or symbol.endswith("/USDT:USDT")):
                            continue
                        if any(x in symbol for x in ["DOWN", "UP", "BEAR", "BULL", "_", "BUSD", "USDC"]):
                            continue
                        clean_sym = Config.sanitize_symbol(symbol)
                        if clean_sym and clean_sym.endswith("/USDT"):
                            # Filtro inicial de min_vol
                            vol_24h = float(ticker.get("quoteVolume", 0) or 0)
                            last = float(ticker.get("last", 0) or 0)
                            if vol_24h < MIN_VOL:
                                base_vol = float(ticker.get("baseVolume", 0) or 0)
                                vol_24h = base_vol * last
                                
                            if vol_24h >= MIN_VOL:
                                all_candidates.append({
                                    "symbol": clean_sym,
                                    "ticker": ticker,
                                    "vol_24h": vol_24h,
                                    "last": last
                                })

                    bot._market_cache = {
                        "candidates": all_candidates,
                    }
                    bot._market_cache_ts = now
                    bot.log(f"✅ [TRIAJE] {len(all_candidates)} candidatos liquidez cacheados")
            except Exception as e_tickers:
                bot.log(f"⚠️ [TRIAJE] fetch_tickers falló: {e_tickers}")
                if getattr(bot, "_market_cache", None) is None:
                    bot._market_cache = {"candidates": []}

        all_candidates = bot._market_cache.get("candidates", [])

        # --- PASO 2: Ordenar estrictamente por liquidez (quoteVolume) ---
        # Garantiza que evaluamos los megacaps primero
        all_candidates.sort(key=lambda x: x["vol_24h"], reverse=True)

        ranked = []
        for cand in all_candidates:
            sym = cand["symbol"]
            ticker = cand["ticker"]
            last = cand["last"]
            vol_24h = cand["vol_24h"]

            # [FIX] Respetar MIN_VOL dinámico si mercado cambia tras el cache
            if vol_24h < MIN_VOL:
                continue

            # Spread check
            raw_key = sym.replace("/", "").replace(":USDT", "")
            book_data = bid_ask_map.get(raw_key)
            if book_data:
                spread = (book_data["ask"] - book_data["bid"]) / book_data["ask"]
            else:
                ask = float(ticker.get("ask", 0) or 0)
                bid = float(ticker.get("bid", 0) or 0)
                spread = (ask - bid) / last if (last > 0 and ask > bid) else None

            if spread is None or spread > MAX_SPREAD:
                continue

            # Agregar a los Top
            ranked.append({
                "symbol": sym,
                "symbol_raw": sym,
                "rvol": 1.0,  # Legacy alias fallback
                "vol_24h": vol_24h,
                "status": "ACTIVE",
                "ticker": ticker,
            })
            
            if len(ranked) >= MAX_PAIRS:
                break

        # [Opcional] Limpiar viejas variables stateful de memoria para ahorrar estado
        if hasattr(bot, "_dynamic_pair_list"): del bot._dynamic_pair_list
        if hasattr(bot, "_vol_ema"): del bot._vol_ema
        if hasattr(bot, "_market_scan_offset"): del bot._market_scan_offset

        top_symbols = [f"{item['symbol']} (${item['vol_24h']/1_000_000:.0f}M)" for item in ranked[:5]]
        bot.log(
            f"🎯 ELITE TRIAJE: {len(ranked)}/{MAX_PAIRS} pares activos (Pura Liquidez) | "
            f"Top 5: {', '.join(top_symbols)}"
        )

        return ranked

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        bot.log(f"⚠️ Error en get_active_market_snapshot: {e}")
        bot.log(f"TRACEBACK: {tb}")
        return []
