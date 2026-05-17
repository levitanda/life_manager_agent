"""Unified smart-home dispatcher across Tuya and VeSync backends.

The agent calls these functions by device name. We look up the device in both
backends and route the command. If a name matches in both, Tuya wins (arbitrary)
— in practice device names should be unique across apps.
"""

import logging

import tuya_client
import vesync_client

logger = logging.getLogger(__name__)


def list_all_devices() -> list[dict]:
    """Combined inventory from both backends."""
    return tuya_client.list_devices() + vesync_client.list_devices()


def _route(device_name: str) -> str:
    """Return 'tuya', 'vesync', or 'none' based on which backend has the device."""
    needle = device_name.lower().strip()
    for d in tuya_client.list_devices():
        if needle in d.get("name", "").lower():
            return "tuya"
    for d in vesync_client.list_devices():
        if needle in d.get("name", "").lower():
            return "vesync"
    return "none"


def turn_on(device_name: str) -> tuple[bool, str]:
    backend = _route(device_name)
    if backend == "tuya":
        return tuya_client.turn_on(device_name)
    if backend == "vesync":
        return vesync_client.turn_on(device_name)
    return False, f"Устройство «{device_name}» не найдено ни в Tuya, ни в VeSync."


def turn_off(device_name: str) -> tuple[bool, str]:
    backend = _route(device_name)
    if backend == "tuya":
        return tuya_client.turn_off(device_name)
    if backend == "vesync":
        return vesync_client.turn_off(device_name)
    return False, f"Устройство «{device_name}» не найдено ни в Tuya, ни в VeSync."


def set_brightness(device_name: str, percent: int) -> tuple[bool, str]:
    backend = _route(device_name)
    if backend == "tuya":
        return tuya_client.set_brightness(device_name, percent)
    return False, f"Яркость поддерживается только для Tuya-устройств."


def set_color_temp(device_name: str, percent: int) -> tuple[bool, str]:
    backend = _route(device_name)
    if backend == "tuya":
        return tuya_client.set_color_temp(device_name, percent)
    return False, f"Температура света поддерживается только для Tuya-устройств."


def set_fan_speed(device_name: str, speed: int) -> tuple[bool, str]:
    backend = _route(device_name)
    if backend == "vesync":
        return vesync_client.set_fan_speed(device_name, speed)
    return False, f"Скорость вентилятора — только для VeSync-устройств."


def set_mode(device_name: str, mode: str) -> tuple[bool, str]:
    backend = _route(device_name)
    if backend == "vesync":
        return vesync_client.set_mode(device_name, mode)
    return False, f"Режимы — только для VeSync-устройств."
