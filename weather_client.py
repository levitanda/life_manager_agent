"""Fetch daily weather for Nesher via Open-Meteo (no API key required)."""

import datetime
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_URL = "https://api.open-meteo.com/v1/forecast"
_PARAMS = {
    "latitude": 32.77,
    "longitude": 35.05,
    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
    "current_weather": True,
    "timezone": "Asia/Jerusalem",
    "forecast_days": 1,
}

_WMO = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "туман с инеем",
    51: "лёгкая морось", 53: "морось", 55: "сильная морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег",
    80: "ливни", 81: "сильные ливни", 82: "очень сильные ливни",
    95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом",
}


def get_weather(target_date: Optional[datetime.date] = None) -> Optional[str]:
    """Return a one-line weather summary for Nesher on target_date (default: today)."""
    try:
        params = dict(_PARAMS)
        if target_date:
            params["start_date"] = target_date.isoformat()
            params["end_date"] = target_date.isoformat()
            params.pop("current_weather", None)

        r = requests.get(_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        daily = data.get("daily", {})
        t_max = daily.get("temperature_2m_max", [None])[0]
        t_min = daily.get("temperature_2m_min", [None])[0]
        precip = daily.get("precipitation_probability_max", [None])[0]
        code = daily.get("weathercode", [None])[0]
        condition = _WMO.get(code, "неизвестно")

        parts = [f"{condition.capitalize()}"]
        if t_min is not None and t_max is not None:
            parts.append(f"{t_min:.0f}…{t_max:.0f}°C")
        if precip is not None and precip > 20:
            parts.append(f"осадки {precip:.0f}%")

        return ", ".join(parts)
    except Exception as e:
        logger.warning("Weather fetch failed: %s", e)
        return None
