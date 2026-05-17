"""Entry point: starts the Telegram bot with scheduled jobs."""

import asyncio
import logging
import threading

from telegram.ext import Application

import alice_skill
import bot_handlers
import config
import scheduler as scheduler_module

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    threading.Thread(
        target=alice_skill.run, daemon=True,
        name="alice-webhook"
    ).start()
    logger.info("Alice webhook listening on port %s", config.ALICE_PORT)

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    bot_handlers.register_handlers(app)

    sched = scheduler_module.setup_scheduler(app)
    sched.start()
    logger.info("Scheduler started.")

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=False)

    logger.info("Bot is running. Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()
    finally:
        sched.shutdown(wait=False)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
