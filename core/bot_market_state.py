def detect_market_regime(bot) -> str:
    try:
        if not hasattr(bot, "market_btc_price") or bot.market_btc_price == 0:
            return "RANGE"

        btc_data = bot.data_service.fetch_and_update_data("BTC/USDT", "1h")
        if btc_data is None or len(btc_data) < 200:
            return "RANGE"

        close = btc_data["close"]
        ema_200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
        adx_values = btc_data.get("adx")
        if adx_values is None or len(adx_values) < 14:
            from pandas_ta import adx

            btc_data = adx(
                btc_data["high"], btc_data["low"], btc_data["close"], length=14
            )
            adx_values = btc_data.get("ADX_14")

        if adx_values is None or len(adx_values) < 14:
            return "RANGE"

        adx = adx_values.iloc[-1]
        btc_price = bot.market_btc_price

        if adx < 20:
            return "RANGE"
        if btc_price > ema_200:
            return "BULL_TREND"
        return "BEAR_TREND"
    except Exception as error:
        bot.log(f"⚠️ Error detecting market regime: {error}")
        return "RANGE"
