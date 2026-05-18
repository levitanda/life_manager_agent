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


def find_contact(name: str) -> dict | None:
    """Return {name, email, phone} for the first matching contact, or None.
    Phone is normalized to digits only (international format without '+').
    """
    try:
        result = (
            _get_service()
            .people()
            .searchContacts(query=name, readMask="names,emailAddresses,phoneNumbers")
            .execute()
        )
        results = result.get("results", [])
        if not results:
            return None
        person = results[0].get("person", {})
        names = person.get("names", [])
        display_name = names[0].get("displayName") if names else name
        emails = person.get("emailAddresses", [])
        phones = person.get("phoneNumbers", [])
        return {
            "name": display_name,
            "email": emails[0]["value"] if emails else None,
            "phone": "".join(c for c in phones[0]["value"] if c.isdigit()) if phones else None,
        }
    except Exception as e:
        logger.warning("Full contact search failed for %r: %s", name, e)
        return None
