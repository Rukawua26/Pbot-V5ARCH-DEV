"""
SNIPER AI v118-PRO - CONSOLE UI
=================================
Sin Rich, prints simples con progreso de scan
"""

from datetime import datetime
from config import Config


class UI:
    def __init__(self):
        self.state = {}
        self._render_count = 0
        self._scan_progress = 0

    def start(self):
        print("=" * 60)
        print("  SNIPER AI v118-PRO - CONSOLE MODE")
        print("  Paper: " + ("YES" if Config.PAPER_MODE else "NO"))
        print("=" * 60)

    def stop(self):
        print("[UI] Detenido")

    def update(self, **kwargs):
        self.state.update(kwargs)

    def _print_scanning(self):
        """Muestra el progreso del scan en curso"""
        scanner = self.state.get("scanner", [])
        self._scan_progress += 1

        # Solo mostrar cada 3 ciclos mientras escanea
        if self._scan_progress % 3 != 0:
            return

        print(
            f"\r[{datetime.now().strftime('%H:%M:%S')}] 📡 Escaneando... [{len(scanner)} pares]   ",
            end="",
            flush=True,
        )

    def render(self):
        """Imprime resumen completo"""
        self._render_count += 1

        # Mostrar progreso de scan en cada ciclo
        self._print_scanning()

        # Solo imprimir resumen completo cada 10 ciclos (~30 seg)
        if self._render_count % 10 != 0:
            return

        print()  # Nueva línea después del scan progress
        print()

        st = self.state
        scanner = st.get("scanner", [])
        trades = st.get("trades", [])
        balance = st.get("balance", 0)
        db_stats = st.get("db_stats", {})
        sentiment = st.get("sentiment", ("NEUTRAL", "white"))

        # Contar trades reales vs shadow
        real_active = sum(1 for t in trades if not t.get("is_shadow"))
        shadow_active = sum(1 for t in trades if t.get("is_shadow"))

        # Stats de la DB
        total_real = db_stats.get("total_real_trades", 0) if db_stats else 0
        total_shadow = db_stats.get("total_shadow_trades", 0) if db_stats else 0
        wr = db_stats.get("win_rate", 0) if db_stats else 0

        # Header
        print("=" * 70)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] SNIPER v118-PRO")
        print("=" * 70)

        # Balance y stats
        print(f"\n📊 CUENTA")
        print(f"   Balance: ${balance:.2f}")
        print(f"   Win Rate: {wr:.1f}%")
        print(f"   Real Trades Totales: {total_real} | Shadow Totales: {total_shadow}")

        # Trades activos
        print(f"\n🔴 TRADES REALES ABIERTOS: {real_active}")
        real_trades = [t for t in trades if not t.get("is_shadow")]
        if real_trades:
            for t in real_trades:
                sym = t.get("symbol", "?")
                side = t.get("side", "?")
                entry = t.get("entry", 0) or t.get("entry_price", 0)
                pnl = t.get("pnl", 0)
                print(f"   - {sym} {side}")
                print(f"     Entry: ${entry:.6f} | PnL: {pnl:+.2f}%")
        else:
            print("   (ninguno)")

        print(f"\n🟡 TRADES SHADOW ABIERTOS: {shadow_active}")
        shadow_trades = [t for t in trades if t.get("is_shadow")]
        if shadow_trades:
            for t in shadow_trades:
                sym = t.get("symbol", "?")
                side = t.get("side", "?")
                entry = t.get("entry", 0) or t.get("entry_price", 0)
                pnl = t.get("pnl", 0)
                print(f"   - {sym} {side}")
                print(f"     Entry: ${entry:.6f} | PnL: {pnl:+.2f}%")
        else:
            print("   (ninguno)")

        # Radar - símbolos escaneados
        print(f"\n📡 RADAR ({len(scanner)} pares)")
        print("-" * 70)
        if scanner:
            # Header de la tabla
            print(
                f"{'#':<3} {'SYMBOL':<12} {'SIG':<5} {'PROB':<7} {'RSI':<5} {'TREND':<6} {'TIER':<6} {'RESULT'}"
            )
            print("-" * 70)
            for i, item in enumerate(scanner, 1):
                sym = item.get("symbol", "?")
                signal = item.get("signal", "?") or item.get("side", "?")
                prob_str = item.get("ia_prob", "---")  # Ya viene como string "XX%"
                rsi_val = item.get("rsi_val", 0) or 0
                trend = item.get("trend_val", "N/A") or "N/A"
                tier = item.get("tier", "") or "IRON"
                result = item.get("result", "") or ""
                ia_shadow = item.get("ia_shadow", "")
                ia_real = item.get("ia_real", "")
                ob = item.get("ob", "⚪")

                # Abreviar signal
                sig_map = {
                    "BUY": "BUY",
                    "SELL": "SELL",
                    "NEUTRAL": "NEUT",
                    "WAIT": "WAIT",
                    "HOLD": "HOLD",
                }
                sig = sig_map.get(signal, signal[:4]) if signal else "?"

                # Modo (REAL/SHADOW)
                if ia_real == "✅":
                    mode = "🔥REAL"
                elif ia_shadow == "✅":
                    mode = "🧪SH"
                else:
                    mode = tier[:4] if tier else "IRON"

                rsi_str = f"{rsi_val:.0f}" if rsi_val else "?"
                trend_str = trend[:5] if trend else "N/A"

                print(
                    f"{i:<3} {sym:<12} {sig:<5} {prob_str:<7} {rsi_str:>4}  {trend_str:<6} {mode:<6} {result[:30]}"
                )
        else:
            print("   🔄 Esperando datos del radar...")

        # Sentiment
        sentiment_text = sentiment[0] if isinstance(sentiment, tuple) else sentiment
        print(f"\n🌐 BTC SENTIMENT: {sentiment_text}")

        # ML Metrics (v118-PRO)
        ml = st.get("ml_metrics", {})
        if ml:
            print(f"\n🧠 MACHINE LEARNING")
            perf = ml.get("performance", {})
            if perf:
                print(f"   Score: {perf.get('score', 0):.2f} | Precision: {perf.get('precision', 0):.2f}")
            
            top = ml.get("top_symbols", [])
            if top:
                top_str = ", ".join([f"{s['symbol']}({s['accuracy']:.0f}%)" for s in top[:3]])
                print(f"   Top: {top_str}")

        print("=" * 70)
