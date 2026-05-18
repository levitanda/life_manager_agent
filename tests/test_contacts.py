"""Tests for contacts_client.find_contact (full contact info lookup)."""

from unittest.mock import MagicMock, patch

import pytest

import contacts_client


def _search_response(person: dict | None):
    if person is None:
        return {"results": []}
    return {"results": [{"person": person}]}


def test_find_contact_with_phone(mock_contacts_service):
    mock_contacts_service.people().searchContacts().execute.return_value = _search_response({
        "names": [{"displayName": "Анна Иванова"}],
        "emailAddresses": [{"value": "anna@example.com"}],
        "phoneNumbers": [{"value": "+972 50-123-4567"}],
    })
    result = contacts_client.find_contact("Анна")
    assert result is not None
    assert result["name"] == "Анна Иванова"
    assert result["email"] == "anna@example.com"
    assert result["phone"] == "972501234567"


def test_find_contact_no_phone(mock_contacts_service):
    mock_contacts_service.people().searchContacts().execute.return_value = _search_response({
        "names": [{"displayName": "Email Only"}],
        "emailAddresses": [{"value": "e@x.com"}],
    })
    result = contacts_client.find_contact("Email")
    assert result["phone"] is None
    assert result["email"] == "e@x.com"


def test_find_contact_no_result(mock_contacts_service):
    mock_contacts_service.people().searchContacts().execute.return_value = _search_response(None)
    assert contacts_client.find_contact("несуществующий") is None


def test_find_contact_api_error(mock_contacts_service):
    mock_contacts_service.people().searchContacts().execute.side_effect = Exception("API error")
    assert contacts_client.find_contact("X") is None
