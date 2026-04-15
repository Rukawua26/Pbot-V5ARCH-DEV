from config import Config
from core.execution_runtime_state import persist_execution_runtime_state
from core.bot_cycles import (
    fetch_triage_data_parallel,
    finalize_scan_cycle,
    prepare_top_triage,
    run_cycle_wait_and_api_log,
    run_market_context_cycle,
    run_market_refresh_cycle,
    run_triage_cycle,
)
from core.bot_housekeeping import run_periodic_housekeeping
from core.bot_main_loop import run_main_logic
from core.bot_audit_verdict import get_audit_verdict as resolve_audit_verdict
from core.bot_radar import update_radar as run_update_radar
from core.market_intelligence import acquire_targets, get_active_market_snapshot
from core.bot_risk_cycles import run_btc_panic_cycle, run_crash_predictor_cycle
from core.bot_runtime import run_bot_runtime_loop, run_initial_load
from core.bot_trade_entry import execute_order as run_execute_order
from core.trade_manager import abort_partial_trade as tm_abort_partial_trade
from core.trade_manager import close_trade as tm_close_trade
from core.strategy.shocks import next_shock_distance_pct
from core.bot_signals import run_signal_scan_cycle
from core.signals.analyze import _analyze_symbol_candidate
from core.signals.context import _build_symbol_context, _update_signal_diagnostics
from core.signals.execution import _execute_and_update_symbol
from core.signals.filters import (
    _apply_entry_filters_and_adjust_prob,
    _plan_execution_mode,
    _resolve_audit_verdict_and_stats,
)


class BotFacade:
    def _initial_load(self, dashboard_module=None):
        if dashboard_module is None:
            dashboard_module = getattr(self, "_dashboard_module", None)
        return run_initial_load(self, dashboard_module)

    def run(self):
        return run_bot_runtime_loop(
            self,
            getattr(self, "_dashboard_module", None),
            getattr(self, "_logger", None),
            getattr(self, "_shadow_logger", None),
        )

    def _run_periodic_housekeeping(
        self,
        now,
        last_report_time,
        last_coach_time,
        last_log_check,
    ):
        return run_periodic_housekeeping(
            self,
            now,
            last_report_time,
            last_coach_time,
            last_log_check,
        )

    def acquire_targets(self):
        return acquire_targets(self)

    def _get_active_market_snapshot(self):
        return get_active_market_snapshot(self)

    def _run_market_refresh_cycle(self):
        return run_market_refresh_cycle(self)

    def _run_triage_cycle(self):
        return run_triage_cycle(self)

    def _run_market_context_cycle(self, tickers):
        return run_market_context_cycle(self, tickers)

    def _run_crash_predictor_cycle(self) -> bool:
        return run_crash_predictor_cycle(self)

    def _run_btc_panic_cycle(self):
        return run_btc_panic_cycle(self)

    def _prepare_top_triage(self, triage_snapshot):
        return prepare_top_triage(self, triage_snapshot)

    def _fetch_triage_data_parallel(self, top_triage):
        return fetch_triage_data_parallel(self, top_triage)

    def _analyze_symbol_candidate(self, symbol_raw, symbol, df_main, df_4h, elapsed):
        return _analyze_symbol_candidate(
            self, symbol_raw, symbol, df_main, df_4h, elapsed
        )

    def _build_symbol_context(
        self, symbol_raw, symbol, df_main, price, ind, audit_signal
    ):
        return _build_symbol_context(
            self, symbol_raw, symbol, df_main, price, ind, audit_signal
        )

    def _execute_and_update_symbol(
        self,
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
        return _execute_and_update_symbol(
            self,
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
        )

    def _update_signal_diagnostics(
        self, symbol, audit_signal, prob_final, mode, votos, ind, signal_stats
    ):
        return _update_signal_diagnostics(
            self, symbol, audit_signal, prob_final, mode, votos, ind, signal_stats
        )

    def _apply_entry_filters_and_adjust_prob(
        self, symbol, symbol_raw, df_main, audit_signal, prob_final, ctx, vol_rel
    ):
        return _apply_entry_filters_and_adjust_prob(
            self, symbol, symbol_raw, df_main, audit_signal, prob_final, ctx, vol_rel
        )

    def _plan_execution_mode(
        self,
        symbol,
        audit_signal,
        prob_final,
        audit_verdict,
        filter_passed,
        filter_reason,
        ctx,
    ):
        return _plan_execution_mode(
            self,
            symbol,
            audit_signal,
            prob_final,
            audit_verdict,
            filter_passed,
            filter_reason,
            ctx,
        )

    def _resolve_audit_verdict_and_stats(
        self,
        symbol,
        audit_signal,
        prob_final,
        ob_status,
        pnl_real_hoy,
        mode,
        ctx,
        filter_passed,
        filter_reason,
        ml_pure_prob,
        signal_stats,
    ):
        return _resolve_audit_verdict_and_stats(
            self,
            symbol,
            audit_signal,
            prob_final,
            ob_status,
            pnl_real_hoy,
            mode,
            ctx,
            filter_passed,
            filter_reason,
            ml_pure_prob,
            signal_stats,
        )

    def _run_signal_scan_cycle(self, top_triage, results, signal_stats, pnl_real_hoy):
        return run_signal_scan_cycle(
            self, top_triage, results, signal_stats, pnl_real_hoy
        )

    def _finalize_scan_cycle(self, signal_stats):
        return finalize_scan_cycle(self, signal_stats)

    def _run_cycle_wait_and_api_log(self):
        return run_cycle_wait_and_api_log(self)

    def _main_logic(self):
        return run_main_logic(self)

    def _perform_triage(self):
        """
        [V118-PRO] Alias público de _get_active_market_snapshot().
        Ejecuta el Scouting Masivo + Ranking RVOL y devuelve los pares activos ordenados.
        Úsalo cuando quieras invocar el triaje manualmente desde comandos o tests.
        """
        return self._get_active_market_snapshot()

    def save_cache(self):
        """Guarda el caché de velas del DataService en disco (llamado cada 5 minutos y al apagar)."""
        try:
            if hasattr(self, "data_service") and self.data_service:
                self.data_service.save_cache()
            persist_execution_runtime_state(self)
        except Exception as error:
            self.log(f"⚠️ Error al guardar caché: {error}")

    def get_audit_verdict(
        self,
        symbol,
        prob_ia,
        signal,
        ob_status,
        pnl_hoy,
        meta_actual,
        mode="NONE",
        ctx=None,
    ):
        return resolve_audit_verdict(
            self,
            symbol=symbol,
            prob_ia=prob_ia,
            signal=signal,
            ob_status=ob_status,
            pnl_hoy=pnl_hoy,
            meta_actual=meta_actual,
            mode=mode,
            ctx=ctx,
        )

    def update_radar(
        self,
        symbol,
        decision,
        prob_ia,
        ob_status,
        audit_verdict,
        ctx,
        votos=None,
        response_ms=-1,
    ):
        return run_update_radar(
            self,
            symbol,
            decision,
            prob_ia,
            ob_status,
            audit_verdict,
            ctx,
            votos=votos,
            response_ms=response_ms,
        )

    def execute_order(
        self,
        symbol,
        side,
        price,
        atr,
        is_shadow=False,
        vol=0.0,
        context=None,
        ob_status="⚪",
        override_usd_size=0.0,
    ):
        return run_execute_order(
            self,
            symbol=symbol,
            side=side,
            price=price,
            atr=atr,
            is_shadow=is_shadow,
            vol=vol,
            context=context,
            ob_status=ob_status,
            override_usd_size=override_usd_size,
        )

    def close_trade(
        self,
        symbol,
        reason,
        exit_price,
        exit_confidence=0.0,
        latency_context=None,
    ):
        tm_close_trade(
            self,
            symbol=symbol,
            reason=reason,
            exit_price=exit_price,
            exit_confidence=exit_confidence,
            latency_context=latency_context,
        )

    def abort_partial_trade(self, symbol, reason, exit_price):
        tm_abort_partial_trade(
            self,
            symbol=symbol,
            reason=reason,
            exit_price=exit_price,
        )

    def _safe_div(self, a, b):
        try:
            return float(a) / float(b) if float(b) != 0 else 0.0
        except Exception:
            return 0.0

    def _get_shock_distance_pct(self, df, side):
        """Wrapper del modulo SHOCK compartido (core/strategy/shocks.py)."""
        try:
            return next_shock_distance_pct(
                df=df,
                side=side,
                pivot_window=int(getattr(Config, "SHOCK_PIVOT_WINDOW", 3)),
                lookback_bars=int(getattr(Config, "SHOCK_LOOKBACK_BARS", 240)),
            )
        except Exception:
            return None, None

    def _update_scanner_status(self, symbol, status, qoe="--"):
        """Helper para actualizar estado en radar desde hilos."""
        self.update_radar(
            symbol,
            {"signal": "WAIT", "mode": "NONE"},
            0.0,
            "⚪",
            status,
            {"tier": "IRON"},
            response_ms=-1,
        )
