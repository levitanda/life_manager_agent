"""Tests for birthday_client.py"""

import datetime
from unittest.mock import MagicMock, patch

import pytest

import birthday_client


def _make_person(name: str, month: int, day: int, email: str | None = None) -> dict:
    person: dict = {
        "names": [{"displayName": name}],
        "birthdays": [{"date": {"month": month, "day": day}}],
    }
    if email:
        person["emailAddresses"] = [{"value": email}]
    return person


def test_get_todays_birthdays_match(mock_birthday_service):
    today = datetime.date.today()
    alice = _make_person("Alice", today.month, today.day, "alice@example.com")
    bob = _make_person("Bob", (today.month % 12) + 1, today.day)  # different month

    mock_birthday_service.people().connections().list().execute.return_value = {
        "connections": [alice, bob]
    }

    results = birthday_client.get_todays_birthdays()
    assert len(results) == 1
    assert results[0]["name"] == "Alice"
    assert results[0]["email"] == "alice@example.com"


def test_get_todays_birthdays_no_match(mock_birthday_service):
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    charlie = _make_person("Charlie", tomorrow.month, tomorrow.day)

    mock_birthday_service.people().connections().list().execute.return_value = {
        "connections": [charlie]
    }

    results = birthday_client.get_todays_birthdays()
    assert results == []


def test_get_todays_birthdays_no_email(mock_birthday_service):
    today = datetime.date.today()
    dave = _make_person("Dave", today.month, today.day)  # no email

    mock_birthday_service.people().connections().list().execute.return_value = {
        "connections": [dave]
    }

    results = birthday_client.get_todays_birthdays()
    assert len(results) == 1
    assert results[0]["email"] is None


def test_get_todays_birthdays_pagination(mock_birthday_service):
    today = datetime.date.today()
    alice = _make_person("Alice", today.month, today.day)
    bob = _make_person("Bob", today.month, today.day)

    execute_mock = mock_birthday_service.people().connections().list().execute
    execute_mock.side_effect = [
        {"connections": [alice], "nextPageToken": "tok1"},
        {"connections": [bob]},
    ]

    results = birthday_client.get_todays_birthdays()
    assert len(results) == 2


def test_get_todays_birthdays_api_error(mock_birthday_service):
    mock_birthday_service.people().connections().list().execute.side_effect = Exception("API error")
    results = birthday_client.get_todays_birthdays()
    assert results == []
