import asyncio
import json
import random
import threading
import time

import websockets


class BinanceWebSocket:
    def __init__(self, symbols=["btcusdt"]):
        self.symbols = [s.lower().replace("/", "").split(":")[0] for s in symbols]

        # Build multi-stream URL if multiple symbols
        if len(self.symbols) == 1:
            self.url = f"wss://fstream.binance.com/ws/{self.symbols[0]}@depth5@100ms"
        else:
            streams = "/".join([f"{s}@depth5@100ms" for s in self.symbols])
            self.url = f"wss://fstream.binance.com/stream?streams={streams}"

        self.l2_state = {
            s: {"bid": 0.0, "ask": 0.0, "spread": 0.0} for s in self.symbols
        }
        self.is_running = False

    def update_symbols(self, symbols):
        """Dynamically updates the watched symbols and reconnects."""
        new_symbols = [s.lower().replace("/", "").split(":")[0] for s in symbols]
        if set(self.symbols) != set(new_symbols):
            self.symbols = new_symbols
            if len(self.symbols) == 1:
                self.url = (
                    f"wss://fstream.binance.com/ws/{self.symbols[0]}@depth5@100ms"
                )
            else:
                streams = "/".join([f"{s}@depth5@100ms" for s in self.symbols])
                self.url = f"wss://fstream.binance.com/stream?streams={streams}"

            # Keep existing state for intersecting symbols, initialize new ones
            new_state = {}
            for s in self.symbols:
                new_state[s] = self.l2_state.get(
                    s, {"bid": 0.0, "ask": 0.0, "spread": 0.0}
                )
            self.l2_state = new_state

            if self.is_running:
                # Signal reconnect
                self._reconnect_flag = True

    async def start(self):
        """Starts the WebSocket connection and maintains an infinite listening loop."""
        self._reconnect_flag = False
        reconnect_delay = 2.0
        while self.is_running:
            try:
                # Use \n to avoid mixing with the carriage return updates
                async with websockets.connect(self.url) as ws:
                    self._reconnect_flag = False
                    reconnect_delay = 2.0
                    while self.is_running and not self._reconnect_flag:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=10)
                            data = json.loads(message)
                            self._process_data(data)
                        except (websockets.ConnectionClosed, asyncio.TimeoutError):
                            break
            except Exception as e:
                print(f"⚠️ WS reconnect loop error: {e}")

            if self.is_running:
                wait_s = reconnect_delay + random.uniform(0.0, 0.8)
                await asyncio.sleep(wait_s)
                reconnect_delay = min(reconnect_delay * 1.7, 30.0)

    def _process_data(self, data):
        """Extracts best bid/ask, calculates spread, and updates state."""
        try:
            # Handle combined stream format {"stream": "btcusdt@depth5...", "data": {...}}
            if "stream" in data and "data" in data:
                stream_name = data["stream"]
                symbol = stream_name.split("@")[0]
                payload = data["data"]
            else:
                # Single stream format
                symbol = self.symbols[0]
                payload = data

            # 'b' for bids, 'a' for asks
            bids = payload.get("b", [])
            asks = payload.get("a", [])

            if not bids or not asks:
                return

            # Best Bid is the highest price (first in the array usually)
            best_bid = float(bids[0][0])
            # Best Ask is the lowest price
            best_ask = float(asks[0][0])

            spread_abs = best_ask - best_bid
            spread_pct = (spread_abs / best_ask) * 100 if best_ask > 0 else 0.0

            if symbol in self.l2_state:
                self.l2_state[symbol]["bid"] = best_bid
                self.l2_state[symbol]["ask"] = best_ask
                self.l2_state[symbol]["spread"] = spread_pct

        except (ValueError, IndexError, KeyError) as e:
            print(f"⚠️ WS payload inválido: {e}")

    def start_background(self):
        """Starts the WebSocket loop in a background daemon thread."""
        self.is_running = True
        t = threading.Thread(target=self._run_async_loop, daemon=True)
        t.start()

    def stop(self):
        self.is_running = False

    def _run_async_loop(self):
        asyncio.run(self.start())

    def get_l2_spread(self, symbol):
        """Returns the spread in percentage for a specific symbol."""
        sym = symbol.lower().replace("/", "").split(":")[0]
        if sym in self.l2_state:
            return self.l2_state[sym]["spread"]
        return None

    def get_l2_state(self, symbol=None):
        """Returns full L2 state for a symbol or all symbols."""
        if symbol:
            sym = symbol.lower().replace("/", "").split(":")[0]
            return self.l2_state.get(sym)
        return self.l2_state


if __name__ == "__main__":
    ws = BinanceWebSocket(symbols=["btcusdt", "ethusdt"])
    ws.start_background()
    print("🚀 WebSocket iniciado en segundo plano. Leyendo estado...")
    for i in range(5):
        time.sleep(1)
        print(
            f"Tick {i + 1}: BTC: {ws.get_l2_spread('BTC/USDT')}% | ETH: {ws.get_l2_spread('ETH/USDT')}%"
        )
    ws.stop()
    print("🏁 Test finalizado.")
