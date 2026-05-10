import os
import time
import json
import threading
import pandas as pd
import logging
import ccxt
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, List
from config import Config

logger = logging.getLogger("SniperAI")

# [v118] Directorio base del módulo para rutas absolutas de caché
_BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)  # raíz del proyecto

try:
    import pyarrow

    HAS_PARQUET = True
except ImportError:
    HAS_PARQUET = False


class DataService:
    def __init__(self, exchange):
        self.exchange = exchange
        self.weight_tracker = None
        self.data_cache = {}
        self.last_ohlcv_fetch = {}
        self.maturity_cache = {}
        self._cache_save_lock = threading.Lock()
        self._cache_save_future = None
        self._cache_save_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="cache-save"
        )
        # [v118] Rutas absolutas ancladas al directorio raíz del proyecto
        self.maturity_file = os.path.join(
            _BASE_DIR, "data_storage", "maturity_cache.json"
        )
        self.cache_dir = os.path.join(_BASE_DIR, "data_storage", "candles")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.load_maturity_cache()

    def set_weight_tracker(self, tracker):
        self.weight_tracker = tracker

    def _track_api_weight(self, endpoint: str, weight: int, category: str):
        if self.weight_tracker:
            self.weight_tracker.track(endpoint, weight, category)

    def _clean_df(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        # Eliminar duplicados por columna 'time'
        df = df.drop_duplicates(subset=["time"]).copy()
        df = df.sort_values("time")
        return df

    def load_cache(self):
        """Carga el caché de velas desde disco (Parquet/Pickle)"""
        if not os.path.exists(self.cache_dir):
            return

        files = [
            f
            for f in os.listdir(self.cache_dir)
            if f.endswith(".parquet") or f.endswith(".pkl")
        ]
        count = 0
        for f in files:
            try:
                path = os.path.join(self.cache_dir, f)
                symbol_tf = f.replace(".parquet", "").replace(".pkl", "")

                if HAS_PARQUET and f.endswith(".parquet"):
                    df = pd.read_parquet(path)
                else:
                    df = pd.read_pickle(path)

                if not df.empty:
                    self.data_cache[symbol_tf] = df
                    count += 1
            except Exception as e:
                logger.warning(f"⚠️ Error cargando cache {f}: {e}")

        if count > 0:
            logger.info(f"💾 Caché de datos cargado: {count} archivos.")

    def _snapshot_cache_for_save(self):
        snapshot = {}
        for key, df in list(self.data_cache.items()):
            if df is None or df.empty:
                continue
            try:
                # Guardar solo las últimas N velas para ahorrar espacio
                snapshot[key] = df.tail(Config.CANDLE_FETCH_LIMIT).copy()
            except Exception as e:
                logger.warning(f"⚠️ Error preparando cache {key}: {e}")
        return snapshot

    def _write_cache_snapshot(self, snapshot):
        for key, df_to_save in snapshot.items():
            try:

                # [v118] Sanitizar nombre de archivo (reemplazar / para evitar error de subdirectorio)
                safe_key = key.replace("/", "_").replace(":", "_")
                if HAS_PARQUET:
                    path = os.path.join(self.cache_dir, f"{safe_key}.parquet")
                    df_to_save.to_parquet(path, engine="pyarrow", compression="snappy")
                else:
                    path = os.path.join(self.cache_dir, f"{safe_key}.pkl")
                    df_to_save.to_pickle(path)
            except Exception as e:
                logger.warning(f"⚠️ Error guardando cache {key}: {e}")

    def save_cache(self):
        """Guarda el caché de datos de forma segura y síncrona."""
        self._write_cache_snapshot(self._snapshot_cache_for_save())

    def save_cache_async(self) -> bool:
        """Agenda un guardado de velas sin bloquear el ciclo principal.

        Returns:
            True si se agendó un nuevo guardado; False si ya había uno en curso.
        """
        with self._cache_save_lock:
            if self._cache_save_future and not self._cache_save_future.done():
                return False
            snapshot = self._snapshot_cache_for_save()
            if not snapshot:
                self._cache_save_future = None
                return False
            self._cache_save_future = self._cache_save_executor.submit(
                self._write_cache_snapshot, snapshot
            )
            return True

    def load_maturity_cache(self):
        if os.path.exists(self.maturity_file):
            try:
                with open(self.maturity_file, "r") as f:
                    self.maturity_cache = json.load(f)
            except Exception:
                self.maturity_cache = {}

    def _candle_file_path(self, symbol: str, timeframe: str) -> str:
        safe_symbol = symbol.replace("/", "_").replace(":", "_")
        safe_tf = timeframe.replace("/", "_").replace(":", "_")
        return os.path.join(self.cache_dir, f"{safe_symbol}_{safe_tf}.parquet")

    def download_historical_data(
        self,
        symbol: str,
        timeframe: str,
        days: int,
        limit_per_call: int = 1500,
    ) -> pd.DataFrame:
        """Descarga velas históricas paginadas y guarda en parquet.

        Usa fetch_ohlcv paginado con `since` para cubrir el rango completo solicitado.
        """
        if not self.exchange:
            raise RuntimeError("Exchange no inicializado en DataService")

        now_ms = int(time.time() * 1000)
        tf_seconds = int(self.exchange.parse_timeframe(timeframe))
        tf_ms = tf_seconds * 1000
        since_ms = now_ms - (int(days) * 24 * 60 * 60 * 1000)

        all_rows: List[List[float]] = []
        cursor = since_ms
        safety_counter = 0
        max_iterations = 200

        logger.info(
            f"📥 Descargando histórico {symbol} {timeframe} ({days} días) desde Binance..."
        )

        while cursor < now_ms and safety_counter < max_iterations:
            batch = self.exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, since=cursor, limit=limit_per_call
            )
            self._track_api_weight("fetch_ohlcv", 1, "market")
            if not batch:
                break

            all_rows.extend(batch)
            last_ts = int(batch[-1][0])
            next_cursor = last_ts + tf_ms

            if next_cursor <= cursor:
                break

            cursor = next_cursor
            safety_counter += 1

            if last_ts >= (now_ms - tf_ms):
                break

            time.sleep(0.05)

        if not all_rows:
            raise RuntimeError(
                f"No se pudieron descargar velas para {symbol} {timeframe}"
            )

        df = pd.DataFrame(
            all_rows, columns=["time", "open", "high", "low", "close", "volume"]
        )
        df = self._clean_df(df)
        df = df[(df["time"] >= since_ms) & (df["time"] <= now_ms)].copy()

        path = self._candle_file_path(symbol, timeframe)
        df.to_parquet(path, engine="pyarrow", compression="snappy", index=False)

        cache_key = f"{symbol}_{timeframe}"
        self.data_cache[cache_key] = df
        self.last_ohlcv_fetch[cache_key] = time.time()

        logger.info(
            f"✅ Histórico guardado en {path} | velas={len(df)} | "
            f"desde={pd.to_datetime(df['time'].min(), unit='ms', utc=True)} "
            f"hasta={pd.to_datetime(df['time'].max(), unit='ms', utc=True)}"
        )
        return df

    def download_multiscale_historical_data(
        self,
        symbol: str,
        days: int = 60,
        timeframes: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Descarga histórico multiescala y lo guarda en parquet.

        Por defecto descarga 15m y 1h para setup swing.
        """
        if timeframes is None:
            timeframes = ["15m", "1h"]

        out: Dict[str, pd.DataFrame] = {}
        for tf in timeframes:
            out[tf] = self.download_historical_data(
                symbol=symbol, timeframe=tf, days=days
            )
        return out

    def save_maturity_cache(self):
        os.makedirs(os.path.dirname(self.maturity_file), exist_ok=True)
        try:
            with open(self.maturity_file, "w") as f:
                json.dump(self.maturity_cache, f)
        except (IOError, OSError) as e:
            logger.warning(f"⚠️ Error guardando maturity cache: {e}")

    def audit_symbol_maturity(self, symbol: str) -> bool:
        """Verifica si el activo tiene historial suficiente para ser operado."""
        if symbol in self.maturity_cache:
            return self.maturity_cache[symbol]

        try:
            # Para la auditoría usamos 4h para ver historial largo
            ohlcv = self.exchange.fetch_ohlcv(symbol, "4h", limit=200)
            self._track_api_weight("fetch_ohlcv", 1, "market")
            is_mature = len(ohlcv) >= 200
            self.maturity_cache[symbol] = is_mature
            return is_mature
        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            logger.warning(f"⚠️ Error auditando madurez {symbol}: {e}")
            return False

    def fetch_and_update_data(
        self,
        symbol: str,
        timeframe: str,
        pairs_to_scan: Optional[List[str]] = None,
        fast_mode: bool = False,
    ) -> Optional[pd.DataFrame]:
        cache_key = f"{symbol}_{timeframe}"
        since, limit = None, Config.CANDLE_FETCH_LIMIT
        expected_cols = ["time", "open", "high", "low", "close", "volume"]

        _CACHE_TTL = {"15m": 90, "1h": 180, "4h": 600}
        cache_ttl = _CACHE_TTL.get(timeframe, 30)
        ahora_ts = time.time()

        prev_df = self.data_cache.get(cache_key)
        cache_valid = (
            cache_key in self.last_ohlcv_fetch
            and ahora_ts - self.last_ohlcv_fetch[cache_key] < cache_ttl
        )

        if cache_valid and prev_df is not None and not prev_df.empty:
            prev_df = self._clean_df(prev_df)
            if len(prev_df) >= Config.MIN_CANDLE_HISTORY:
                return prev_df

        if prev_df is not None and not prev_df.empty:
            prev_df = self._clean_df(prev_df)
            if len(prev_df) >= Config.MIN_CANDLE_HISTORY:
                since = int(prev_df["time"].iloc[-1])
                limit = None

        ohlcv = []
        retries = 1 if fast_mode else 2
        for i in range(retries):
            try:
                ohlcv = self.exchange.fetch_ohlcv(
                    symbol, timeframe, since=since, limit=limit
                )
                self._track_api_weight("fetch_ohlcv", 1, "market")
                break
            except (ccxt.NetworkError, ccxt.ExchangeError) as e:
                if i == retries - 1:
                    logger.error(
                        f"🚫 Error fatal fetch {symbol} ({timeframe}): {e}"
                    )
                    return prev_df
                time.sleep(0.15 if fast_mode else (i + 1) ** 2)

        if not ohlcv:
            return prev_df

        new_df = pd.DataFrame(ohlcv, columns=expected_cols)
        if new_df[expected_cols].isnull().any().any():
            return prev_df

        for col in ["open", "high", "low", "close"]:
            if (new_df[col] <= 0).any() or (new_df[col] > 1e9).any():
                return prev_df

        combined = (
            pd.concat([prev_df, new_df], ignore_index=True)
            if prev_df is not None
            else new_df
        )
        updated = self._clean_df(combined).tail(Config.CANDLE_FETCH_LIMIT)
        updated = updated[expected_cols].copy()

        self.data_cache[cache_key] = updated
        self.last_ohlcv_fetch[cache_key] = time.time()

        # Gestión de memoria del caché
        if len(self.data_cache) > 50 and pairs_to_scan:
            symbols_to_keep = set(pairs_to_scan[:30])
            keys_to_delete = [
                k for k in self.data_cache if k.split("_")[0] not in symbols_to_keep
            ]
            for k in keys_to_delete[:20]:
                del self.data_cache[k]

        return updated

    def sanitize_context(self, context: Optional[Dict]) -> Dict:
        """Elimina objetos no serializables (DataFrames) del contexto para guardar en DB."""
        if not context:
            return {}
        clean = context.copy()
        keys_to_remove = [k for k, v in clean.items() if isinstance(v, pd.DataFrame)]
        for k in keys_to_remove:
            del clean[k]
        return clean
