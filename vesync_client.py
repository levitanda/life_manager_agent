"""VeSync cloud client — control purifiers, humidifiers, outlets, switches.

Authentication: VESYNC_EMAIL + VESYNC_PASSWORD in env.
Lazy-loaded singleton manager; reconnects if token expires.
"""

import logging
import os
import threading
from typing import Optional

from pyvesync import VeSync

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_manager: Optional[VeSync] = None


def _get_manager() -> Optional[VeSync]:
    """Return a logged-in VeSync manager, or None if credentials missing/login fails."""
    global _manager
    with _lock:
        if _manager is not None:
            return _manager
        email = os.environ.get("VESYNC_EMAIL")
        password = os.environ.get("VESYNC_PASSWORD")
        if not (email and password):
            logger.info("VeSync credentials not configured")
            return None
        try:
            mgr = VeSync(email, password, time_zone=os.environ.get("TIMEZONE", "Asia/Jerusalem"))
            if not mgr.login():
                logger.warning("VeSync login failed")
                return None
            mgr.update()
            _manager = mgr
            return mgr
        except Exception as e:
            logger.warning("VeSync init failed: %s", e)
            return None


def _refresh():
    mgr = _get_manager()
    if mgr:
        try:
            mgr.update()
        except Exception as e:
            logger.warning("VeSync refresh failed: %s", e)


def list_devices() -> list[dict]:
    """Return list of {name, type, state} for all VeSync devices."""
    mgr = _get_manager()
    if not mgr:
        return []
    _refresh()
    devices = []
    for category in (mgr.fans, mgr.outlets, mgr.switches, mgr.bulbs):
        for dev in category:
            devices.append({
                "name": dev.device_name,
                "type": dev.device_type,
                "state": "on" if getattr(dev, "device_status", "") == "on" else "off",
                "backend": "vesync",
            })
    return devices


def _find_device(name: str):
    """Case-insensitive name match across all device categories."""
    mgr = _get_manager()
    if not mgr:
        return None
    needle = name.lower().strip()
    for category in (mgr.fans, mgr.outlets, mgr.switches, mgr.bulbs):
        for dev in category:
            if dev.device_name.lower() == needle:
                return dev
    # Partial match fallback
    for category in (mgr.fans, mgr.outlets, mgr.switches, mgr.bulbs):
        for dev in category:
            if needle in dev.device_name.lower():
                return dev
    return None


def turn_on(device_name: str) -> tuple[bool, str]:
    dev = _find_device(device_name)
    if not dev:
        return False, f"Устройство «{device_name}» не найдено в VeSync."
    try:
        ok = dev.turn_on()
        return bool(ok), f"Включено: {dev.device_name}" if ok else f"Не удалось включить {dev.device_name}"
    except Exception as e:
        logger.error("VeSync turn_on failed: %s", e)
        return False, f"Ошибка: {e}"


def turn_off(device_name: str) -> tuple[bool, str]:
    dev = _find_device(device_name)
    if not dev:
        return False, f"Устройство «{device_name}» не найдено в VeSync."
    try:
        ok = dev.turn_off()
        return bool(ok), f"Выключено: {dev.device_name}" if ok else f"Не удалось выключить {dev.device_name}"
    except Exception as e:
        logger.error("VeSync turn_off failed: %s", e)
        return False, f"Ошибка: {e}"


def set_fan_speed(device_name: str, speed: int) -> tuple[bool, str]:
    """For purifiers/humidifiers/fans. speed: 1-3 typically."""
    dev = _find_device(device_name)
    if not dev:
        return False, f"Устройство «{device_name}» не найдено в VeSync."
    try:
        if hasattr(dev, "change_fan_speed"):
            ok = dev.change_fan_speed(speed)
        elif hasattr(dev, "set_mist_level"):
            ok = dev.set_mist_level(speed)
        else:
            return False, f"У {dev.device_name} нет регулировки скорости."
        return bool(ok), f"Скорость {speed} установлена для {dev.device_name}"
    except Exception as e:
        logger.error("VeSync set_fan_speed failed: %s", e)
        return False, f"Ошибка: {e}"


def set_mode(device_name: str, mode: str) -> tuple[bool, str]:
    """For purifiers: 'auto', 'manual', 'sleep'. For humidifiers similar."""
    dev = _find_device(device_name)
    if not dev:
        return False, f"Устройство «{device_name}» не найдено в VeSync."
    try:
        if hasattr(dev, "auto_mode") and mode == "auto":
            ok = dev.auto_mode()
        elif hasattr(dev, "manual_mode") and mode == "manual":
            ok = dev.manual_mode()
        elif hasattr(dev, "sleep_mode") and mode in ("sleep", "night"):
            ok = dev.sleep_mode()
        elif hasattr(dev, "mode_toggle"):
            ok = dev.mode_toggle(mode)
        else:
            return False, f"Режим '{mode}' не поддерживается для {dev.device_name}."
        return bool(ok), f"Режим '{mode}' установлен для {dev.device_name}"
    except Exception as e:
        logger.error("VeSync set_mode failed: %s", e)
        return False, f"Ошибка: {e}"
