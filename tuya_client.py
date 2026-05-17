"""Tuya Cloud client — control Tuya/Smart Life devices via Tuya IoT Platform.

Setup (one-time):
  1. Register at iot.tuya.com (free)
  2. Create a Cloud Project (data center matching your region — for Israel use EU/Central Europe)
  3. Subscribe to "IoT Core" and "Authorization" services in Service API
  4. Link your Smart Life account: Devices → Link App Account → scan QR with Smart Life app
  5. Copy Access ID, Access Secret, and your Tuya UID
  6. Add to .env: TUYA_API_KEY, TUYA_API_SECRET, TUYA_API_REGION (eu/us/cn/in), TUYA_USER_ID
"""

import logging
import os
import threading
from typing import Optional

import tinytuya

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cloud: Optional[tinytuya.Cloud] = None
_devices_cache: Optional[list] = None


def _get_cloud() -> Optional[tinytuya.Cloud]:
    global _cloud
    with _lock:
        if _cloud is not None:
            return _cloud
        api_key = os.environ.get("TUYA_API_KEY")
        api_secret = os.environ.get("TUYA_API_SECRET")
        region = os.environ.get("TUYA_API_REGION", "eu")
        user_id = os.environ.get("TUYA_USER_ID")
        if not (api_key and api_secret and user_id):
            logger.info("Tuya credentials not configured")
            return None
        try:
            cloud = tinytuya.Cloud(
                apiRegion=region,
                apiKey=api_key,
                apiSecret=api_secret,
                apiDeviceID=user_id,
            )
            _cloud = cloud
            return cloud
        except Exception as e:
            logger.warning("Tuya init failed: %s", e)
            return None


def _fetch_devices() -> list[dict]:
    global _devices_cache
    if _devices_cache is not None:
        return _devices_cache
    cloud = _get_cloud()
    if not cloud:
        return []
    try:
        # New Smart Home Basic Service endpoint (the old /v1.0/devices/{uid} is deprecated)
        resp = cloud.cloudrequest("/v1.0/iot-01/associated-users/devices")
        if not isinstance(resp, dict) or not resp.get("success"):
            logger.warning("Tuya cloudrequest failed: %s", resp)
            return []
        result = resp.get("result")
        # Endpoint can return either a list or a dict with 'devices'
        if isinstance(result, dict):
            devs = result.get("devices", result.get("list", []))
        elif isinstance(result, list):
            devs = result
        else:
            devs = []
        _devices_cache = devs
        return _devices_cache
    except Exception as e:
        logger.warning("Tuya fetch failed: %s", e)
        return []


def invalidate_cache():
    global _devices_cache
    _devices_cache = None


def list_devices() -> list[dict]:
    """Return list of {name, type, state, id} for all Tuya devices."""
    devs = _fetch_devices()
    result = []
    for d in devs:
        name = d.get("name", "")
        dev_id = d.get("id", "")
        category = d.get("category", "")
        online = d.get("online", False)
        state = "?"
        # Status is embedded in the device record from the new endpoint
        for s in d.get("status", []):
            if s.get("code") in ("switch_led", "switch_1", "switch"):
                state = "on" if s.get("value") else "off"
                break
        if not online:
            state = "offline"
        result.append({
            "name": name,
            "type": category,
            "state": state,
            "id": dev_id,
            "backend": "tuya",
        })
    return result


def _find_device(name: str) -> Optional[dict]:
    devs = _fetch_devices()
    needle = name.lower().strip()
    for d in devs:
        if d.get("name", "").lower() == needle:
            return d
    for d in devs:
        if needle in d.get("name", "").lower():
            return d
    return None


def _send_command(device_id: str, code: str, value) -> tuple[bool, str]:
    cloud = _get_cloud()
    if not cloud:
        return False, "Tuya не настроен."
    try:
        result = cloud.sendcommand(device_id, {"commands": [{"code": code, "value": value}]})
        if isinstance(result, dict) and result.get("success"):
            return True, "ok"
        return False, f"Tuya отклонил команду: {result}"
    except Exception as e:
        logger.error("Tuya sendcommand failed: %s", e)
        return False, str(e)


def turn_on(device_name: str) -> tuple[bool, str]:
    dev = _find_device(device_name)
    if not dev:
        return False, f"Устройство «{device_name}» не найдено в Tuya."
    # Try common switch codes — different device categories use different codes
    for code in ("switch_led", "switch_1", "switch"):
        ok, msg = _send_command(dev["id"], code, True)
        if ok:
            return True, f"Включено: {dev['name']}"
    return False, f"Не удалось включить {dev['name']}: {msg}"


def turn_off(device_name: str) -> tuple[bool, str]:
    dev = _find_device(device_name)
    if not dev:
        return False, f"Устройство «{device_name}» не найдено в Tuya."
    for code in ("switch_led", "switch_1", "switch"):
        ok, msg = _send_command(dev["id"], code, False)
        if ok:
            return True, f"Выключено: {dev['name']}"
    return False, f"Не удалось выключить {dev['name']}: {msg}"


def set_brightness(device_name: str, percent: int) -> tuple[bool, str]:
    """Set bulb brightness. percent: 1-100. Tuya range is typically 10-1000."""
    dev = _find_device(device_name)
    if not dev:
        return False, f"Устройство «{device_name}» не найдено в Tuya."
    value = max(10, min(1000, int(percent * 10)))
    for code in ("bright_value_v2", "bright_value", "brightness"):
        ok, msg = _send_command(dev["id"], code, value)
        if ok:
            return True, f"Яркость {percent}% установлена для {dev['name']}"
    return False, f"Не удалось установить яркость: {msg}"


def set_color_temp(device_name: str, percent: int) -> tuple[bool, str]:
    """Set color temperature. percent: 0 (warm) to 100 (cool)."""
    dev = _find_device(device_name)
    if not dev:
        return False, f"Устройство «{device_name}» не найдено в Tuya."
    value = max(0, min(1000, int(percent * 10)))
    for code in ("temp_value_v2", "temp_value"):
        ok, msg = _send_command(dev["id"], code, value)
        if ok:
            return True, f"Температура света {percent}% для {dev['name']}"
    return False, f"Не удалось: {msg}"
