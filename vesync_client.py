"""VeSync cloud client — control purifiers, humidifiers, outlets, switches.

Uses pyvesync v3 (async API) with synchronous wrappers. Each public call
runs a single asyncio.run() that does login + operation + cleanup, so the
aiohttp session never outlives its event loop.

Authentication: VESYNC_EMAIL + VESYNC_PASSWORD in env. Country code is read
from VESYNC_COUNTRY (default IL → uses EU API base).
"""

import asyncio
import logging
import os
from typing import Optional

from pyvesync import VeSync

logger = logging.getLogger(__name__)


async def _with_session(action):
    """Login, run `action(mgr)`, then close. action returns whatever it wants."""
    email = os.environ.get("VESYNC_EMAIL")
    password = os.environ.get("VESYNC_PASSWORD")
    country = os.environ.get("VESYNC_COUNTRY", "IL")
    if not (email and password):
        logger.info("VeSync credentials not configured")
        return None
    try:
        async with VeSync(email, password, country_code=country) as mgr:
            ok = await mgr.login()
            if not ok:
                logger.warning("VeSync login returned False")
                return None
            await mgr.get_devices()
            await mgr.update()
            return await action(mgr)
    except Exception as e:
        logger.warning("VeSync session failed: %s", e)
        return None


def _run(action):
    try:
        return asyncio.run(_with_session(action))
    except Exception as e:
        logger.warning("VeSync run failed: %s", e)
        return None


def _find(mgr, name: str):
    needle = name.lower().strip()
    for dev in mgr.devices:
        if getattr(dev, "device_name", "").lower() == needle:
            return dev
    for dev in mgr.devices:
        if needle in getattr(dev, "device_name", "").lower():
            return dev
    return None


def list_devices() -> list[dict]:
    async def action(mgr):
        result = []
        for dev in mgr.devices:
            state = "on" if getattr(dev, "device_status", None) == "on" else "off"
            result.append({
                "name": getattr(dev, "device_name", "?"),
                "type": getattr(dev, "device_type", "?"),
                "state": state,
                "backend": "vesync",
            })
        return result
    return _run(action) or []


def turn_on(device_name: str) -> tuple[bool, str]:
    async def action(mgr):
        dev = _find(mgr, device_name)
        if not dev:
            return (False, f"Устройство «{device_name}» не найдено в VeSync.")
        try:
            ok = dev.turn_on()
            if asyncio.iscoroutine(ok):
                ok = await ok
            return (bool(ok), f"Включено: {dev.device_name}" if ok else f"Не удалось включить {dev.device_name}")
        except Exception as e:
            return (False, f"Ошибка: {e}")
    return _run(action) or (False, "VeSync недоступен.")


def turn_off(device_name: str) -> tuple[bool, str]:
    async def action(mgr):
        dev = _find(mgr, device_name)
        if not dev:
            return (False, f"Устройство «{device_name}» не найдено в VeSync.")
        try:
            ok = dev.turn_off()
            if asyncio.iscoroutine(ok):
                ok = await ok
            return (bool(ok), f"Выключено: {dev.device_name}" if ok else f"Не удалось выключить {dev.device_name}")
        except Exception as e:
            return (False, f"Ошибка: {e}")
    return _run(action) or (False, "VeSync недоступен.")


def set_fan_speed(device_name: str, speed: int) -> tuple[bool, str]:
    async def action(mgr):
        dev = _find(mgr, device_name)
        if not dev:
            return (False, f"Устройство «{device_name}» не найдено в VeSync.")
        try:
            for method_name in ("change_fan_speed", "set_mist_level", "set_fan_speed"):
                fn = getattr(dev, method_name, None)
                if fn:
                    ok = fn(speed)
                    if asyncio.iscoroutine(ok):
                        ok = await ok
                    if ok:
                        return (True, f"Скорость {speed} установлена для {dev.device_name}")
            return (False, f"У {dev.device_name} нет регулировки скорости.")
        except Exception as e:
            return (False, f"Ошибка: {e}")
    return _run(action) or (False, "VeSync недоступен.")


def set_mode(device_name: str, mode: str) -> tuple[bool, str]:
    async def action(mgr):
        dev = _find(mgr, device_name)
        if not dev:
            return (False, f"Устройство «{device_name}» не найдено в VeSync.")
        try:
            method_map = {
                "auto": "set_auto_mode",
                "manual": "set_manual_mode",
                "sleep": "set_sleep_mode",
                "night": "set_sleep_mode",
            }
            mname = method_map.get(mode)
            fn = getattr(dev, mname, None) if mname else None
            if fn:
                ok = fn()
                if asyncio.iscoroutine(ok):
                    ok = await ok
                return (bool(ok), f"Режим '{mode}' установлен для {dev.device_name}" if ok else "Не удалось")
            fn = getattr(dev, "set_mode", None)
            if fn:
                ok = fn(mode)
                if asyncio.iscoroutine(ok):
                    ok = await ok
                return (bool(ok), f"Режим '{mode}' установлен для {dev.device_name}" if ok else "Не удалось")
            return (False, f"Режим '{mode}' не поддерживается для {dev.device_name}.")
        except Exception as e:
            return (False, f"Ошибка: {e}")
    return _run(action) or (False, "VeSync недоступен.")
