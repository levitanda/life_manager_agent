# WhatsApp Bridge (Baileys)

Локальный Node-сервис, который держит активную WhatsApp-сессию через
[Baileys](https://github.com/WhiskeySockets/Baileys) и предоставляет
HTTP-API для Python-агента.

## Setup на сервере

```bash
# Установить Node.js 20 (если ещё нет)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# В директории бота
cd ~/life-agent/whatsapp_bridge
npm install --omit=dev
```

## Первый запуск (авторизация)

```bash
cd ~/life-agent/whatsapp_bridge
node server.js
```

В консоль выводится QR-код. Сканируй его в WhatsApp:
**WhatsApp → Settings → Linked devices → Link a device**

После успешной авторизации появится строка `WhatsApp connected — bridge is ready.`
Сессия сохраняется в `auth_session/` — пары не потребуется при перезапусках.

Останови `Ctrl+C` и поставь как systemd-сервис (см. ниже).

## systemd

`/etc/systemd/system/life-agent-wa-bridge.service`:

```ini
[Unit]
Description=Life Agent WhatsApp Bridge
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/life-agent/whatsapp_bridge
ExecStart=/usr/bin/node server.js
Restart=on-failure
RestartSec=5
Environment=BRIDGE_PORT=3030
Environment=LOG_LEVEL=warn

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now life-agent-wa-bridge
sudo journalctl -u life-agent-wa-bridge -f   # смотри QR при первой авторизации
```

## API

| Метод | Путь | Что |
|---|---|---|
| GET | `/status` | `{ready, has_qr, error}` |
| GET | `/qr` | `{qr}` — raw QR-строка, если ещё не авторизован |
| GET | `/groups` | `{groups: [{id, name, size}]}` |
| POST | `/send` | body `{chatId, text}` |

## Найти ID нужной группы

```bash
curl http://127.0.0.1:3030/groups | jq
```

Скопируй `id` (формат `120363...@g.us`) → добавь в
`~/life-agent/whatsapp_groups.json`:

```json
{
  "семья": "120363...@g.us",
  "покупки": "120363...@g.us"
}
```

После этого бот сможет отправлять по короткому имени.

## Что хранится

- `auth_session/` — приватные ключи сессии. **Не коммить, не делиться.**

Если хочешь разлогиниться — удали папку и перезапусти сервис.
