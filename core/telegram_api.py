from config import Config


def telegram_api_url(method: str) -> str:
    token = str(getattr(Config, "TELEGRAM_TOKEN", "") or "")
    clean_method = str(method or "").strip("/")
    return f"https://api.telegram.org/bot{token}/{clean_method}"
