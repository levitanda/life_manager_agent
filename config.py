import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

PUSHOVER_USER_KEY = os.environ.get("PUSHOVER_USER_KEY", "")
PUSHOVER_APP_TOKEN = os.environ.get("PUSHOVER_APP_TOKEN", "")

TIMEZONE = os.environ.get("TIMEZONE", "Europe/Moscow")
MORNING_TIME = os.environ.get("MORNING_TIME", "06:30")
EVENING_TIME = os.environ.get("EVENING_TIME", "21:30")

ALICE_PORT = int(os.environ.get("ALICE_PORT", "5000"))
ALICE_DIGEST_FILE = "last_digest.txt"
ALICE_MESSAGE_FILE = "pending_alice_message.txt"

GOOGLE_CREDENTIALS_FILE = "google_credentials.json"
GOOGLE_TOKEN_FILE = "google_token.json"

SHORT_TASK_CALENDAR = "Задачи краткосрочные"
LONG_TASK_CALENDAR = "Задачи долгосрочные"
PROGRESS_CALENDAR = "Прогресс дня"
