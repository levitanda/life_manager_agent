"""Shared pytest fixtures."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub env vars before any project module is imported
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "123456")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")
os.environ.setdefault("TIMEZONE", "Asia/Jerusalem")


@pytest.fixture
def mock_credentials():
    with patch("google_auth.get_credentials") as mock:
        mock.return_value = MagicMock()
        yield mock


@pytest.fixture
def mock_google_build(mock_credentials):
    """Patch googleapiclient.discovery.build everywhere."""
    mock_svc = MagicMock()
    with patch("googleapiclient.discovery.build", return_value=mock_svc):
        yield mock_svc


@pytest.fixture
def mock_calendar_service():
    """Patch calendar_client._get_service directly."""
    mock_svc = MagicMock()
    with patch("calendar_client._get_service", return_value=mock_svc):
        yield mock_svc


@pytest.fixture
def mock_birthday_service():
    """Patch birthday_client.build directly."""
    mock_svc = MagicMock()
    with patch("birthday_client.build", return_value=mock_svc):
        yield mock_svc


@pytest.fixture
def mock_contacts_service():
    """Patch contacts_client._get_service directly."""
    mock_svc = MagicMock()
    with patch("contacts_client._get_service", return_value=mock_svc):
        yield mock_svc
