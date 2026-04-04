"""
SNIPER AI v118 - DASHBOARD
=========================
Panel de control visual para monitoreo del bot en tiempo real.
"""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from datetime import datetime
import threading
import time


class Dashboard:
    def __init__(self, bot=None):
        self.bot = bot
        self.console = Console()
        self.lock = threading.Lock()
        self.stats = {
            "total_trades": 0,
            "win_rate": 0.0,
            "pnl_total": 0.0,
            "active_trades": 0,
            "shadow_trades": 0,
            "real_trades": 0,
        }
        self.running = False

    def update(self):
        """Actualiza las estadísticas del dashboard"""
        if not self.bot:
            return

        with self.lock:
            try:
                self.stats["active_trades"] = len(self.bot.active_trades)
                self.stats["total_trades"] = getattr(self.bot, "total_trades", 0)
                self.stats["balance"] = getattr(self.bot, "balance", 0.0)

                shadow_count = sum(
                    1
                    for t in self.bot.active_trades.values()
                    if t.get("is_shadow", False)
                )
                real_count = self.stats["active_trades"] - shadow_count
                self.stats["shadow_trades"] = shadow_count
                self.stats["real_trades"] = real_count
            except Exception as e:
                print(f"⚠️ Dashboard update error: {e}")

    def render(self):
        """Renderiza el panel de control"""
        table = Table(title="📊 SNIPER AI DASHBOARD", show_header=True)
        table.add_column("Métrica", style="cyan")
        table.add_column("Valor", style="green")

        table.add_row("Balance", f"${self.stats.get('balance', 0):.2f}")
        table.add_row("Trades Activos", str(self.stats.get("active_trades", 0)))
        table.add_row("  └ Real", str(self.stats.get("real_trades", 0)))
        table.add_row("  └ Shadow", str(self.stats.get("shadow_trades", 0)))
        table.add_row("Win Rate", f"{self.stats.get('win_rate', 0):.1f}%")
        table.add_row("PnL Total", f"${self.stats.get('pnl_total', 0):.2f}")

        return table


def start_dashboard(bot):
    """Función de entrada para iniciar el dashboard en hilo separado"""
    dashboard = Dashboard(bot)

    def update_loop():
        while True:
            dashboard.update()
            time.sleep(5)

    thread = threading.Thread(target=update_loop, daemon=True)
    thread.start()
    return dashboard


if __name__ == "__main__":
    dashboard = Dashboard()
    print("Dashboard inicializado")
