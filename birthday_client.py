"""Google People API — find contacts whose birthday is today."""

import datetime
import logging

from googleapiclient.discovery import build

import google_auth

logger = logging.getLogger(__name__)


def get_todays_birthdays() -> list[dict]:
    """Return contacts with a birthday matching today's month and day."""
    today = datetime.date.today()
    try:
        svc = build("people", "v1", credentials=google_auth.get_credentials())
        next_page_token = None
        birthdays = []
        while True:
            kwargs = {
                "resourceName": "people/me",
                "pageSize": 1000,
                "personFields": "names,birthdays,emailAddresses",
            }
            if next_page_token:
                kwargs["pageToken"] = next_page_token
            result = svc.people().connections().list(**kwargs).execute()
            for person in result.get("connections", []):
                for bday in person.get("birthdays", []):
                    date = bday.get("date", {})
                    if date.get("month") == today.month and date.get("day") == today.day:
                        names = person.get("names", [])
                        name = names[0].get("displayName", "Неизвестно") if names else "Неизвестно"
                        emails = person.get("emailAddresses", [])
                        email = emails[0].get("value") if emails else None
                        birthdays.append({"name": name, "email": email})
                        break
            next_page_token = result.get("nextPageToken")
            if not next_page_token:
                break
        return birthdays
    except Exception as e:
        logger.warning("Birthday fetch failed: %s", e)
        return []
