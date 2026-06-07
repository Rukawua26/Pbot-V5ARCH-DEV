import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class CycleContext:
    cycle_start_ts: float
    tickers: Dict[str, Any] = field(default_factory=dict)
    btc_price: float = 0.0
    btc_price_source: str = "unknown"
    btc_change_tf: float = 0.0
    sentiment: Optional[Tuple[str, str]] = None
    market_regime: str = "RANGE"
    hmm_markov_snapshot: Dict[str, Any] = field(default_factory=dict)
    market_breadth: Dict[str, Any] = field(default_factory=dict)
    pnl_real_hoy: float = 0.0
    balance: float = 0.0
    available_balance: float = 0.0
    pairs_to_scan: List[str] = field(default_factory=list)

    @classmethod
    def capture(cls, bot, tickers: Optional[Dict[str, Any]] = None,
                pnl_real_hoy: Optional[float] = None) -> "CycleContext":
        return cls(
            cycle_start_ts=time.time(),
            tickers=tickers if tickers is not None else getattr(bot, "_snapshot_tickers", {}),
            btc_price=float(getattr(bot, "market_btc_price", 0.0) or 0.0),
            btc_price_source=str(getattr(bot, "market_btc_price_source", "unknown") or "unknown"),
            btc_change_tf=float(getattr(bot, "market_btc_change_tf", 0.0) or 0.0),
            sentiment=getattr(bot, "current_sentiment", None),
            market_regime=str(getattr(bot, "market_regime", "RANGE") or "RANGE"),
            hmm_markov_snapshot=dict(getattr(bot, "hmm_markov_snapshot", {}) or {}),
            market_breadth=dict(getattr(bot, "market_breadth", {}) or {}),
            pnl_real_hoy=pnl_real_hoy if pnl_real_hoy is not None
            else float(getattr(bot, "daily_pnl", 0.0) or 0.0),
            balance=float(getattr(bot, "balance", 0.0) or 0.0),
            available_balance=float(getattr(bot, "available_balance", 0.0) or 0.0),
            pairs_to_scan=list(getattr(bot, "pairs_to_scan", []) or []),
        )
