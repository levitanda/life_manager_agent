"""Send push notifications via Pushover."""

import requests
import config


def send_push(message: str, title: str = "Дайджест дня") -> bool:
    if not config.PUSHOVER_USER_KEY or not config.PUSHOVER_APP_TOKEN:
        return False

    resp = requests.post(
        "https://api.pushover.net/1/messages.json",
        data={
            "token": config.PUSHOVER_APP_TOKEN,
            "user": config.PUSHOVER_USER_KEY,
            "title": title,
            "message": message[:1024],
            "priority": 0,
        },
        timeout=10,
    )
    return resp.status_code == 200
