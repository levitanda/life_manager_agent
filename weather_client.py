"""Fetch daily weather via Open-Meteo (no API key required)."""

import datetime
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"

_DEFAULT_LAT = 32.77  # Nesher
_DEFAULT_LON = 35.05

def _geocode(city: str) -> tuple[float, float] | None:
    try:
        r = requests.get(_GEO_URL, params={"name": city, "count": 1, "language": "ru"}, timeout=5)
        r.raise_for_status()
        results = r.json().get("results")
        if results:
            return results[0]["latitude"], results[0]["longitude"]
    except Exception as e:
        logger.warning("Geocode failed for %r: %s", city, e)
    return None


_WMO = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "туман с инеем",
    51: "лёгкая морось", 53: "морось", 55: "сильная морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег",
    80: "ливни", 81: "сильные ливни", 82: "очень сильные ливни",
    95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом",
}


def get_weather(target_date: Optional[datetime.date] = None, city: Optional[str] = None) -> Optional[str]:
    """Return a one-line weather summary. city defaults to Nesher."""
    try:
        if city:
            coords = _geocode(city)
            if not coords:
                return f"Не удалось найти город «{city}»"
            lat, lon = coords
        else:
            lat, lon = _DEFAULT_LAT, _DEFAULT_LON
            city = "Нешер"

        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
            "timezone": "auto",
        }
        if target_date:
            params["start_date"] = target_date.isoformat()
            params["end_date"] = target_date.isoformat()
        else:
            params["forecast_days"] = 1

        r = requests.get(_FORECAST_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        daily = data.get("daily", {})
        t_max = daily.get("temperature_2m_max", [None])[0]
        t_min = daily.get("temperature_2m_min", [None])[0]
        precip = daily.get("precipitation_probability_max", [None])[0]
        code = daily.get("weathercode", [None])[0]
        condition = _WMO.get(code, "неизвестно")

        parts = [f"{city}: {condition}"]
        if t_min is not None and t_max is not None:
            parts.append(f"{t_min:.0f}…{t_max:.0f}°C")
        if precip is not None and precip > 20:
            parts.append(f"осадки {precip:.0f}%")

        return ", ".join(parts)
    except Exception as e:
        logger.warning("Weather fetch failed: %s", e)
        return None
