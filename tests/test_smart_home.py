"""Tests for smart_home dispatcher + tools."""

from unittest.mock import patch, MagicMock

import pytest

import smart_home
import tools


TUYA_DEVICES = [
    {"name": "Свет в спальне", "type": "light", "state": "off", "id": "tuya123", "backend": "tuya"},
    {"name": "Свет на кухне", "type": "light", "state": "on", "id": "tuya456", "backend": "tuya"},
]

VESYNC_DEVICES = [
    {"name": "Очиститель", "type": "purifier", "state": "off", "backend": "vesync"},
    {"name": "Увлажнитель", "type": "humidifier", "state": "on", "backend": "vesync"},
]


# ─── routing ─────────────────────────────────────────────────────────────────

def test_route_tuya_device():
    with patch("tuya_client.list_devices", return_value=TUYA_DEVICES), \
         patch("vesync_client.list_devices", return_value=VESYNC_DEVICES):
        assert smart_home._route("свет в спальне") == "tuya"


def test_route_vesync_device():
    with patch("tuya_client.list_devices", return_value=TUYA_DEVICES), \
         patch("vesync_client.list_devices", return_value=VESYNC_DEVICES):
        assert smart_home._route("очиститель") == "vesync"


def test_route_unknown():
    with patch("tuya_client.list_devices", return_value=TUYA_DEVICES), \
         patch("vesync_client.list_devices", return_value=VESYNC_DEVICES):
        assert smart_home._route("несуществующее") == "none"


def test_route_partial_match():
    with patch("tuya_client.list_devices", return_value=TUYA_DEVICES), \
         patch("vesync_client.list_devices", return_value=VESYNC_DEVICES):
        # "кухн" should match "Свет на кухне"
        assert smart_home._route("кухн") == "tuya"


# ─── unified turn_on/off ─────────────────────────────────────────────────────

def test_turn_on_routes_to_tuya():
    with patch("tuya_client.list_devices", return_value=TUYA_DEVICES), \
         patch("vesync_client.list_devices", return_value=VESYNC_DEVICES), \
         patch("tuya_client.turn_on", return_value=(True, "Включено: Свет в спальне")) as mock_tuya:
        ok, msg = smart_home.turn_on("свет в спальне")
    assert ok is True
    mock_tuya.assert_called_once()


def test_turn_off_routes_to_vesync():
    with patch("tuya_client.list_devices", return_value=TUYA_DEVICES), \
         patch("vesync_client.list_devices", return_value=VESYNC_DEVICES), \
         patch("vesync_client.turn_off", return_value=(True, "Выключено: Очиститель")) as mock_v:
        ok, msg = smart_home.turn_off("очиститель")
    assert ok is True
    mock_v.assert_called_once()


def test_turn_on_unknown_device():
    with patch("tuya_client.list_devices", return_value=[]), \
         patch("vesync_client.list_devices", return_value=[]):
        ok, msg = smart_home.turn_on("инопланетный гаджет")
    assert ok is False
    assert "не найдено" in msg


# ─── brightness only for tuya ────────────────────────────────────────────────

def test_brightness_rejects_vesync():
    with patch("tuya_client.list_devices", return_value=[]), \
         patch("vesync_client.list_devices", return_value=VESYNC_DEVICES):
        ok, msg = smart_home.set_brightness("очиститель", 50)
    assert ok is False
    assert "Tuya" in msg


def test_brightness_works_for_tuya():
    with patch("tuya_client.list_devices", return_value=TUYA_DEVICES), \
         patch("vesync_client.list_devices", return_value=[]), \
         patch("tuya_client.set_brightness", return_value=(True, "Яркость 50% для Свет в спальне")):
        ok, msg = smart_home.set_brightness("свет в спальне", 50)
    assert ok is True


# ─── fan speed only for vesync ───────────────────────────────────────────────

def test_fan_speed_rejects_tuya():
    with patch("tuya_client.list_devices", return_value=TUYA_DEVICES), \
         patch("vesync_client.list_devices", return_value=[]):
        ok, msg = smart_home.set_fan_speed("свет в спальне", 2)
    assert ok is False
    assert "VeSync" in msg


# ─── tool layer wrappers ─────────────────────────────────────────────────────

def test_tool_smart_home_list():
    with patch("smart_home.list_all_devices", return_value=TUYA_DEVICES + VESYNC_DEVICES):
        result = tools.smart_home_list()
    assert result["status"] == "ok"
    assert "Свет в спальне" in result["summary"]
    assert "Очиститель" in result["summary"]


def test_tool_smart_home_list_empty():
    with patch("smart_home.list_all_devices", return_value=[]):
        result = tools.smart_home_list()
    assert result["status"] == "ok"
    assert "не найдено" in result["summary"].lower()


def test_tool_turn_on_success():
    with patch("smart_home.turn_on", return_value=(True, "Включено: Свет")):
        result = tools.smart_home_turn_on(device_name="свет")
    assert result["status"] == "ok"


def test_tool_turn_on_failure():
    with patch("smart_home.turn_on", return_value=(False, "Не найдено")):
        result = tools.smart_home_turn_on(device_name="фигня")
    assert result["status"] == "error"


# ─── tool schema registered ─────────────────────────────────────────────────

def test_smart_home_tools_in_schemas():
    schema_names = {s["name"] for s in tools.TOOL_SCHEMAS}
    for name in ("smart_home_list", "smart_home_turn_on", "smart_home_turn_off",
                 "smart_home_set_brightness", "smart_home_set_fan_speed",
                 "smart_home_set_mode"):
        assert name in schema_names
        assert name in tools.TOOL_FUNCS
