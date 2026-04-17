# -*- coding: utf-8 -*-
"""
Subscription-based Telegram Bot

Responsibilities:
1. Handle user commands: /start /subscribe /help /watchlist /analyze
2. Register users, show plans, manage watchlists
3. Route /analyze requests to the analysis pipeline (Pro only)

Dependencies:
  - python-telegram-bot >= 20.0 (async version)
  - src.bot.db      user database
  - src.bot.tiers   tier definitions
  - src.config      system config (reads SUBSCRIPTION_BOT_TOKEN)

Usage (standalone):
  python -m src.bot.subscription_bot
"""
import asyncio
import logging
import os
from typing import Optional

from telegram import Update, BotCommand
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from src.bot import db
from src.bot.tiers import (
    TIER_FREE,
    can_use_on_demand,
    can_customize_watchlist,
    watchlist_limit,
    format_tier_menu,
    get_tier_config,
)

logger = logging.getLogger(__name__)


def _get_bot_token() -> str:
    # Try env var first
    token = os.environ.get("SUBSCRIPTION_BOT_TOKEN", "")
    if token:
        return token
    # Try secret file
    import json
    secret_paths = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "credentials", "tg-bot.secret.json"),
        "./credentials/tg-bot.secret.json",
    ]
    for path in secret_paths:
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            token = data.get("token", data.get("BOT_TOKEN", ""))
            if token:
                return token
    raise RuntimeError("SUBSCRIPTION_BOT_TOKEN not configured. Set env var or credentials/tg-bot.secret.json")


def _get_default_stock_list() -> list[str]:
    """Read system default stock list (used by Free tier)"""
    raw = os.environ.get("STOCK_LIST", "BTC-USD,ETH-USD,AAPL")
    return [s.strip() for s in raw.split(",") if s.strip()]


# ===========================
# Command Handlers
# ===========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — Register user and show welcome message"""
    user = update.effective_user
    if user is None:
        return

    # Register (idempotent - returns existing user if already exists)
    db_user = db.create_user(telegram_id=user.id, username=user.username)
    tier_cfg = get_tier_config(db_user["tier"])

    welcome = (
        f"👋 Welcome to StockAnalyst, {user.first_name}!\n\n"
        f"Current plan: *{tier_cfg.label}*\n"
        f"{tier_cfg.description}\n\n"
        "Available commands:\n"
        "  /help       — View all commands\n"
        "  /subscribe  — View plan options\n"
        "  /watchlist  — View your watchlist\n"
        "  /analyze `<ticker>` — On-demand analysis (Pro)"
    )
    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — Show available commands"""
    help_text = (
        "📖 *Commands*\n\n"
        "/start      — Register & view current plan\n"
        "/subscribe  — View plan details & upgrade options\n"
        "/watchlist  — View/manage your watchlist\n"
        "/analyze `<ticker>` — Run analysis on a stock\n"
        "              Example: `/analyze AAPL`\n"
        "              _Pro plan only_\n\n"
        "Daily analysis reports are pushed automatically after market close."
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/subscribe — Show plan options"""
    user = update.effective_user
    if user is None:
        return

    db_user = db.get_user(user.id)
    if not db_user:
        db_user = db.create_user(telegram_id=user.id, username=user.username)

    current = get_tier_config(db_user["tier"])
    menu = format_tier_menu()
    msg = f"Current plan: *{current.label}*\n\n{menu}"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/watchlist — Show user's watchlist; Free tier shows default list"""
    user = update.effective_user
    if user is None:
        return

    db_user = db.get_user(user.id)
    if not db_user:
        db_user = db.create_user(telegram_id=user.id, username=user.username)

    tier = db_user["tier"]
    tier_cfg = get_tier_config(tier)

    if tier == TIER_FREE:
        stocks = _get_default_stock_list()
        note = "_Free plan uses the default stock list. Upgrade to Pro to customize._"
    else:
        stocks = db_user.get("watchlist") or []
        if not stocks:
            note = "_Watchlist is empty. Contact admin or use the API to add stocks._"
        else:
            limit = watchlist_limit(tier)
            cap = f"{len(stocks)}/{limit}" if limit else f"{len(stocks)}/∞"
            note = f"_Stocks: {cap}_"

    if stocks:
        stock_lines = "\n".join(f"  • `{s}`" for s in stocks)
        msg = f"📋 *Watchlist*\n{stock_lines}\n\n{note}"
    else:
        msg = f"📋 *Watchlist*\n(None)\n\n{note}"

    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/analyze <ticker> — Run on-demand analysis (Pro only)"""
    user = update.effective_user
    if user is None:
        return

    db_user = db.get_user(user.id)
    if not db_user:
        db_user = db.create_user(telegram_id=user.id, username=user.username)

    tier = db_user["tier"]

    # Permission check
    if not can_use_on_demand(tier):
        tier_cfg = get_tier_config(tier)
        upgrade_msg = (
            f"⚠️ On-demand analysis is a *Pro* feature.\n\n"
            f"Current plan: {tier_cfg.label}\n\n"
            "Use /subscribe to view upgrade options."
        )
        await update.message.reply_text(upgrade_msg, parse_mode=ParseMode.MARKDOWN)
        return

    # Parse ticker argument
    args = context.args
    if not args:
        await update.message.reply_text(
            "Please provide a stock ticker. Example: `/analyze AAPL`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    ticker = args[0].strip().upper()
    if not ticker:
        await update.message.reply_text("Ticker cannot be empty.", parse_mode=ParseMode.MARKDOWN)
        return

    # Acknowledge
    await update.message.reply_text(
        f"🔍 Analyzing `{ticker}`... Please wait.",
        parse_mode=ParseMode.MARKDOWN,
    )

    # Run analysis in background thread
    try:
        result_text = await asyncio.get_event_loop().run_in_executor(
            None, _run_single_stock_analysis, ticker, user.id
        )
        await update.message.reply_text(result_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error("On-demand analysis failed: ticker=%s user=%d error=%s", ticker, user.id, e)
        await update.message.reply_text(
            f"❌ Error analyzing `{ticker}`. Please try again later.",
            parse_mode=ParseMode.MARKDOWN,
        )


# ===========================
# Analysis execution (sync)
# ===========================

def _run_single_stock_analysis(ticker: str, telegram_id: int) -> str:
    """
    Run analysis pipeline synchronously, return formatted report.
    Runs in thread pool to avoid blocking asyncio event loop.
    """
    try:
        from src.core.pipeline import StockAnalysisPipeline
        pipeline = StockAnalysisPipeline()
        results = pipeline.run(stocks=[ticker], notify=False)

        if not results:
            return f"No results found for `{ticker}`. Please check the ticker symbol."

        result = results[0] if isinstance(results, list) else results
        if hasattr(result, "to_markdown"):
            return result.to_markdown()
        return str(result)
    except Exception as e:
        logger.error("Analysis pipeline failed: %s", e)
        raise


# ===========================
# Application builder & startup
# ===========================

def build_application() -> Application:
    """Build and configure the Telegram Application"""
    token = _get_bot_token()
    app = Application.builder().token(token).build()

    # Register command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("analyze", cmd_analyze))

    return app


async def _set_bot_commands(app: Application) -> None:
    """Register command menu with Telegram"""
    commands = [
        BotCommand("start", "Register & view current plan"),
        BotCommand("help", "View all commands"),
        BotCommand("subscribe", "View plan options"),
        BotCommand("watchlist", "View watchlist"),
        BotCommand("analyze", "On-demand stock analysis (Pro)"),
    ]
    await app.bot.set_my_commands(commands)
    logger.info("Telegram command menu updated")


def run_bot() -> None:
    """Start the subscription bot (blocking, suitable for standalone process)"""
    db.init_db()

    app = build_application()

    async def post_init(application: Application) -> None:
        await _set_bot_commands(application)

    app.post_init = post_init

    logger.info("Subscription bot starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    run_bot()