import logging
import os
from dotenv import load_dotenv
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from database import init_db
from handlers import (
    start_command,
    help_command,
    quest_command,
    board_command,
    done_command,
    begin_command,
    drop_command,
    stats_command,
    today_command,
    tag_command,
    clear_command,
    message_handler,
    callback_handler,
)
from scheduler import setup_scheduler

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(app: Application) -> None:
    """Called after the event loop starts — safe to launch APScheduler here."""
    setup_scheduler(app)


def main():
    init_db()
    logger.info("Database initialised.")

    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN environment variable is not set.")

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    # ── Commands ────────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",  start_command))
    app.add_handler(CommandHandler("help",   help_command))
    app.add_handler(CommandHandler("q",      quest_command))
    app.add_handler(CommandHandler("board",  board_command))
    app.add_handler(CommandHandler("done",   done_command))
    app.add_handler(CommandHandler("begin",  begin_command))
    app.add_handler(CommandHandler("drop",   drop_command))
    app.add_handler(CommandHandler("stats",  stats_command))
    app.add_handler(CommandHandler("today",  today_command))
    app.add_handler(CommandHandler("tag",    tag_command))
    app.add_handler(CommandHandler("clear",  clear_command))

    # ── Callbacks & messages ────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("⚔️  MiguQuest Bot starting — polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
