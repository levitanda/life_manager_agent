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

A2A_PORT = int(os.environ.get("A2A_PORT", "5001"))
A2A_AGENT_NAME = os.environ.get("A2A_AGENT_NAME", "Daria's Life Agent")
A2A_AGENT_URL = os.environ.get("A2A_AGENT_URL", "")  # set after Cloudflare tunnel is up

GOOGLE_CREDENTIALS_FILE = "google_credentials.json"
GOOGLE_TOKEN_FILE = "google_token.json"

DIARY_FILE = os.environ.get("DIARY_FILE", "diary.md")
DIARY_DOC_CACHE = os.environ.get("DIARY_DOC_CACHE", "diary_doc.json")
DIARY_DOC_TITLE = os.environ.get("DIARY_DOC_TITLE", "Личный дневник Дарьи")

SHORT_TASK_CALENDAR = "Задачи краткосрочные"
LONG_TASK_CALENDAR = "Задачи долгосрочные"
PROGRESS_CALENDAR = "Прогресс дня"
