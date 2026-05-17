# Life Agent — Setup Guide

## Step 1: Install system dependency

```bash
sudo apt install python3.14-venv
```

## Step 2: Create virtual environment and install packages

```bash
cd ~/life-agent
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## Step 3: Create a Telegram bot

1. Open Telegram → search `@BotFather` → `/newbot`
2. Follow prompts, get your **bot token**
3. Start a chat with your new bot, then visit:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Send any message to the bot, refresh — find `"id"` inside `"chat"` — that's your **chat ID**

## Step 4: Create Pushover account

1. Register at https://pushover.net
2. Get your **User Key** from the dashboard
3. Create an application → get the **App Token**
4. Install Pushover app on your phone

## Step 5: Set up Google Calendar API

1. Go to https://console.cloud.google.com
2. Create a new project → enable **Google Calendar API**
3. Create **OAuth 2.0 credentials** (Desktop app type)
4. Download the JSON file → save as `~/life-agent/google_credentials.json`

## Step 6: Get Anthropic API key

1. Go to https://console.anthropic.com
2. Create an API key → copy it

## Step 7: Configure environment

```bash
cp .env.example .env
nano .env   # fill in all values
```

## Step 8: Authorize Google Calendar (one-time)

```bash
cd ~/life-agent
./venv/bin/python3 -c "import calendar_client; calendar_client._get_service(); print('OK')"
```
A browser window will open → log in → authorize. This creates `google_token.json`.

## Step 9: Run

```bash
./venv/bin/python3 main.py
```

Test by sending `/start` to your bot.

## Step 10: Auto-start on boot (optional)

```bash
sudo cp life-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable life-agent
sudo systemctl start life-agent
```

Check status: `sudo systemctl status life-agent`
View logs: `journalctl -u life-agent -f`

---

## Bot commands

| Command | Description |
|---|---|
| `/add short <задача>` | Add a short-term task (1-3 days) |
| `/add long <задача>` | Add a long-term task (weeks/months) |
| `/tasks` | View all active tasks |
| `/done <N>` | Mark task #N as complete |
| `/digest` | Get morning digest right now |
| `/progress` | Record today's progress |
