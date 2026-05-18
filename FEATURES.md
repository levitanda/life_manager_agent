# Life-Agent — Журнал функций

Живой файл всех функций, добавленных в проект. Обновляется после каждой новой фичи.

---

## Май 2026 — Sci-fi roadmap

### Перенос ЛЮБОГО события из календаря по названию
- **Файлы:** `calendar_client.py` (новая функция `find_event_by_title`), `tools.py`, `tests/test_calendar_client.py`, `tests/test_bot_handlers.py`
- **Что починили:** Раньше `reschedule_task` работал только с задачами в task-календарях (нужен был task_number). Если пользователь говорил «перенеси встречу с Леонелем» — бот отвечал «у меня нет доступа к календарю». Теперь tool принимает либо `task_number`, либо `event_title` — fuzzy substring search по primary calendar + task calendars.
- **Поиск:** server-side через `events().list(q=...)` + Python-фильтр по `needle in summary.lower()`. Окно: 1 день назад → 14 дней вперёд.
- **Disambiguation:** если найдено несколько событий по запросу — возвращает список с датами/временем, просит уточнить.
- **Пример:** *«перенеси урок с Леонелем на пятницу в 17:00»* → находит событие, переносит, отвечает «📅 Перенесено: «Урок с Леонелем» → 22.05.2026 в 17:00»
- **Тесты:** 5 новых, 108 всего проходят.

### Дайджест как продолжение разговора
- **Файлы:** `digest.py` (новые параметры + helpers `_format_history`, `_format_summaries`), `bot_handlers.py`, `tests/test_digest.py`
- **Что делает:** Утренний дайджест теперь получает в контекст:
  - Последние 12 сообщений активной сессии (raw history)
  - Последние 5 long-term summaries (через `conversation.get_recent_summaries`)
- **Результат:** дайджест звучит как продолжение вчерашнего разговора, не в вакууме. Если вчера говорила «плохо себя чувствую» — утром Claude спросит как самочувствие. Если обсуждали проект — упомянет его в приоритетах задач.
- **Бонус:** сам сгенерированный дайджест добавляется в `conversation.add(...)` — значит весь дневной чат может ссылаться на «утренний дайджест» как на часть разговора.
- **Промпт обновлён:** Claude явно инструктирован «это не первое сообщение — продолжай разговор», обязан реагировать на эмоциональный контекст.
- **Тесты:** 4 новых, 103 всего проходят.

### Запись внеплановых выполненных задач задним числом
- **Файлы:** `calendar_client.py` (новая функция `record_completed_task`), `tools.py`, `personality.json`, `tests/test_record_completed.py`
- **Что делает:** Если в конце дня говоришь *«сегодня помыла окна, позвонила маме»* — бот не просто сохраняет это в Прогресс, а **создаёт каждую такую задачу в календаре задним числом** и сразу помечает выполненной.
- **Как ищется слот:** через существующий `find_free_slots`, фильтр по слотам которые **уже прошли сегодня**, выбирается самый поздний прошлый. Fallback — 30 мин до текущего времени если свободных прошлых слотов нет.
- **Persistence:** событие создаётся с `extendedProperties.retroactive=true` — потом можно отличить от плановых.
- **Personality обновлена:** Claude теперь сам вызывает `record_completed_task` (по одной задаче на каждое дело) когда пользователь упоминает внеплановые достижения, **параллельно** с `save_progress`.
- **Результат:** внеплановые дела попадают в:
  - `/tasks` history
  - Недельный обзор
  - Будущую статистику «сколько сделано вне плана»
- **Тесты:** 6 новых, 99 всего проходят.

### Отложенные и повторяющиеся действия
- **Файлы:** `scheduled_actions.py`, `tools.py`, `scheduler.py`, `tests/test_scheduled_actions.py`
- **Что делает:** Бот может запланировать **любое действие** на потом или сделать его повторяющимся. Это работает для всех существующих tool'ов (smart home, задачи, дайджесты, погода — что угодно).
- **3 новых инструмента:** `schedule_action`, `list_scheduled_actions`, `cancel_scheduled_action`
- **Способы задания времени:**
  - `delay_minutes=5` — через N минут от текущего момента
  - `at_time="22:00"` — сегодня в это время (если уже прошло — завтра)
  - `at_time="22:00", at_date="2026-05-20"` — конкретный день
  - `at_time="22:00", repeat="daily"` — каждый день
  - `repeat="weekdays"` / `"weekend"` — по будням / выходным
- **Persistence:** действия хранятся в `scheduled_actions.json`, восстанавливаются после рестарта бота. Истёкшие one-shot действия очищаются автоматически.
- **Архитектура:** когда триггер срабатывает — `agent.run_agent(action_text)` выполняет действие, результат улетает в Telegram с префиксом «⏰ Запланированное: ...»
- **Примеры запросов:**
  - «включи лампу в зале через 2 минуты»
  - «выключи бойлер в 23:00»
  - «каждый день в 22:00 включай увлажнитель на ночной режим»
  - «по будням в 7:30 пришли дайджест»
  - «покажи запланированные действия» / «отмени xyz123»
- **Тесты:** 17 новых, 93 всего проходят.

### Smart Home: Tuya + VeSync через cloud API
- **Файлы:** `tuya_client.py`, `vesync_client.py`, `smart_home.py`, `tools.py`, `tests/test_smart_home.py`
- **Что делает:** Бот может управлять всеми устройствами Tuya/Smart Life и VeSync (свет, очистители, увлажнители, выключатели, лампы, кондиционеры) — через те же аккаунты что в мобильных приложениях.
- **В продакшене:** 10 Tuya устройств (лампы, бойлер, IR-пульты для TV/AC, датчики T&H, gateway) + 1 VeSync (Levoit Core 400S Series). Включение лампы и очистителя проверено вживую.
- **Технические детали:** Tuya использует endpoint `/v1.0/iot-01/associated-users/devices` (старый `getdevices` теперь возвращает 1106). VeSync v3 (async) с sync-обёртками — каждый вызов делает свежий login внутри одного `asyncio.run()` чтобы не было утечек event loop.
- **Унифицированный dispatcher:** `smart_home.py` автоматически роутит команду в правильный backend по имени устройства (case-insensitive + partial match).
- **7 новых инструментов:** `smart_home_list`, `smart_home_turn_on`, `smart_home_turn_off`, `smart_home_set_brightness` (Tuya), `smart_home_set_color_temp` (Tuya), `smart_home_set_fan_speed` (VeSync), `smart_home_set_mode` (VeSync auto/manual/sleep).
- **Авторизация:**
  - VeSync: просто `VESYNC_EMAIL` + `VESYNC_PASSWORD` в `.env`
  - Tuya: нужна регистрация в iot.tuya.com → Cloud Project → линковка Smart Life аккаунта по QR → `TUYA_API_KEY`/`SECRET`/`REGION`/`USER_ID` в `.env`
- **Примеры запросов:** «включи свет в спальне», «выключи очиститель», «поставь яркость 30% в гостиной», «включи увлажнитель на ночной режим»
- **Тесты:** 15 новых, 76 всего проходят.

### Anthropic tool-use агент (feature-flagged)
- **Файлы:** `tools.py`, `agent.py`, `personality.json`, `tests/test_agent.py`, `bot_handlers.py`
- **Что делает:** Заменяет JSON-парсер на нативный tool-use loop Claude. Одно сообщение может теперь запускать цепочку инструментов («найди час → запиши туда йогу → отправь приглашение»). Параллельные вызовы поддерживаются.
- **12 инструментов:** `add_task`, `complete_task`, `delete_task`, `reschedule_task`, `find_free_time`, `get_weather`, `send_email`, `show_tasks`, `get_digest`, `get_weekly_digest`, `send_to_alice`, `save_progress`
- **Personality dials:** humor, warmth, terseness, honesty, proactivity (0–100). Меняешь в `personality.json` — Claude мгновенно подстраивает тон.
- **Безопасность:** MAX_ITERATIONS=6, prompt caching на system+tools (экономия ~90% токенов), включается через `USE_AGENT=true` env var, fallback на старый парсер при любом исключении.
- **Тесты:** 16 новых, 61 всего проходят.

---

## Май 2026 — Расписание, дни рождения, новости, тесты

### Управление расписанием
- **Файлы:** `calendar_client.py`, `parser.py`, `bot_handlers.py`
- Перенос задач: `«перенеси задачу 2 на пятницу в 15:00»`
- Поиск свободного времени: `«когда у меня свободный час завтра?»`
- Детекция конфликтов: автоматически при добавлении задачи на занятое время → inline-кнопки «Добавить всё равно / Отменить»
- Недельный обзор: `«план на эту неделю»` → AI-генерация Sonnet
- Новые функции в `calendar_client.py`: `reschedule_task`, `get_all_events_for_date`, `find_free_slots`, `get_conflicts`, `get_week_events`

### Уведомления о днях рождения
- **Файлы:** `birthday_client.py`, `bot_handlers.py`, `digest.py`
- В утренний дайджест автоматически добавляются именинники из Google Contacts (today's birthdays)
- Никаких авто-писем, только напоминание Дарье

### Новостной дайджест
- **Файлы:** `news_client.py`, `digest.py`, `bot_handlers.py`
- RSS-фиды: Ynet (заменил недоступный Кан 11), Walla (заменил Кешет 12), Дождь
- В дайджесте новости группируются по каналу — для каждого 2-3 главные темы + краткий общий фон
- Graceful fallback если фид недоступен

### Тестовая инфраструктура
- **Файлы:** `tests/conftest.py`, `tests/test_*.py`, `requirements.txt`
- pytest + unittest.mock для всех внешних API (Google, Anthropic, RSS, OpenAI)
- Тесты для calendar_client, parser, digest, birthday_client, news_client, bot_handlers
- 45 тестов в стартовой партии

---

## Май 2026 — Голос, контакты, погода, Алиса

### Voice input через Whisper
- **Файлы:** `bot_handlers.py`, `requirements.txt`
- Голосовое сообщение в Telegram → OpenAI Whisper (русский) → обрабатывается как обычный текст через тот же pipeline
- Унифицированная функция `_process_natural(text, update, context)` для текста и голоса

### Multi-action парсинг
- **Файлы:** `parser.py`, `bot_handlers.py`
- Одно сообщение → парсер возвращает `{"actions": [...]}` со списком intents
- `_execute_action` обрабатывает их по очереди
- Пример: `«отметь задачу 1 выполненной и пришли дайджест»` → две операции

### Google Contacts для приглашений
- **Файлы:** `contacts_client.py`, `bot_handlers.py`, `calendar_client.py`
- При создании события с участниками → поиск email в Google Contacts → `sendUpdates="all"` → реальные приглашения по email
- Сообщение `«Кого не нашёл»` если контакт отсутствует

### Погода через Open-Meteo
- **Файлы:** `weather_client.py`, `digest.py`, `bot_handlers.py`
- Бесплатный API без ключа, геокодинг любого города
- В утренний дайджест + по запросу `«погода в Тель-Авиве»`
- Дефолт — Нешер (32.77, 35.05)

### Сообщения Алисе для зачитывания вслух
- **Файлы:** `alice_skill.py`, `bot_handlers.py`, `config.py`
- `«передай Алисе: не забудь воду»` → пишется в `pending_alice_message.txt`
- При следующем открытии навыка Алиса прочитает сообщение и закроет сессию

### Стабильный URL для Алисы
- **Файлы:** systemd-сервис `cloudflared-tunnel.service` на EC2
- Cloudflare Quick Tunnel как systemd-сервис → стабильный URL пока EC2 не перезагружается

---

## Май 2026 — Базовый функционал

### Telegram-бот с управлением задачами
- **Файлы:** `main.py`, `bot_handlers.py`, `calendar_client.py`, `parser.py`
- Краткосрочные и долгосрочные задачи в Google Calendar (отдельные календари)
- Парсинг даты/времени/длительности из естественного языка
- Команды: `/tasks`, `/done`, `/digest`, `/progress`, `/memory`
- Natural-language обработка через Claude Haiku (парсер)

### Утренний дайджест + вечерний check-in
- **Файлы:** `digest.py`, `scheduler.py`, `bot_handlers.py`
- 06:30 — утренний дайджест (Claude Sonnet) с календарём, задачами, погодой, новостями, письмами, днями рождения, мотивацией
- 21:30 — вопрос про прогресс дня
- 23:30 — авто-суммаризация сессии в долгосрочную память

### Долгосрочная память
- **Файлы:** `conversation.py`
- Активная история: последние 20 сообщений в `conversation_history.json`
- Долгосрочные резюме: после 8 часов простоя или вручную — Claude-summary → `session_summaries.jsonl`
- Последние 15 резюме инжектятся в каждый промпт парсера

### Gmail интеграция
- **Файлы:** `gmail_client.py`, `bot_handlers.py`
- Чтение непрочитанных писем за 2 дня (до 15)
- Отправка писем с preview + inline-кнопки подтверждения

### Push-уведомления
- **Файлы:** `pushover_client.py`
- Pushover API для мобильных алертов (помимо Telegram)

---

## Багфиксы и улучшения

- Дайджест разбивается на части если >4000 символов (`_split_message`, `_reply_split`)
- max_tokens поднят с 1024 до 4096 — дайджест больше не обрезается Claude
- Новостные RSS-фиды переписаны на рабочие (Ynet, Walla, tvrain.ru/export/rss/all.xml)
- Open-Meteo больше не падает с 400 (убран конфликт `forecast_days` + `start_date`)
- Новости и дни рождения теперь грузятся для сегодня даже если парсер вернул явную дату
- Multi-day задачи показываются в дайджесте на 2-й, 3-й и т.д. день (look-back 90 дней)
- Сделана отдельная функция `_clean_for_speech` для Алисы — убирает эмодзи и спецсимволы из TTS
- Yandex Dialogs больше не возвращают 400 — в session-ответе оставлены только session_id/message_id/user_id
- GitHub Actions CI/CD исправлен (git pull, stale ref handling)

---

## Запланировано (roadmap)

См. `/home/daria/.claude/plans/robust-floating-hanrahan.md` — полный roadmap.

**Следующие приоритеты:**
1. Smart home мост (Tuya + VeSync) — J.A.R.V.I.S.-момент
2. Realtime voice через Telegram Mini App — Samantha-стиль разговора
3. Persistent user model — структурированный профиль вместо мешка summaries
4. Meeting prep agent — за 30 мин до встречи бриф + контекст по email
5. Proactive interruption engine — watcher с правилами
