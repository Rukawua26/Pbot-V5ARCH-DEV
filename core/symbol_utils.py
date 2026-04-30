def normalize_position_symbol(pos_symbol: str) -> str:
    """Normalize exchange position symbols to the internal SYMBOL/USDT format."""
    raw = str(pos_symbol or "").split(":")[0]
    if "/" in raw:
        return raw
    if raw.endswith("USDT") and len(raw) > 4:
        return f"{raw[:-4]}/{raw[-4:]}"
    return raw
