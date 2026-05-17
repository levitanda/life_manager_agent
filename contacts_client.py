"""Google People API — search contacts by name."""

import logging

from googleapiclient.discovery import build

import google_auth

logger = logging.getLogger(__name__)


def _get_service():
    return build("people", "v1", credentials=google_auth.get_credentials())


def find_contact_email(name: str) -> str | None:
    """Return the first email address for a contact matching name, or None."""
    try:
        result = (
            _get_service()
            .people()
            .searchContacts(query=name, readMask="names,emailAddresses")
            .execute()
        )
        for item in result.get("results", []):
            emails = item.get("person", {}).get("emailAddresses", [])
            if emails:
                return emails[0]["value"]
    except Exception as e:
        logger.warning("Contact search failed for %r: %s", name, e)
    return None
