# -*- coding: utf-8 -*-
"""
Subscription-based Telegram Bot

Responsibilities:
1. Handle user commands: /start /subscribe /help /watchlist /add /remove /analyze
2. Register users, show plans, manage watchlists
3. Route /analyze requests to the analysis pipeline (Pro only)
4. Provide inline keyboard buttons for easy navigation

Dependencies:
  - python-telegram-bot >= 20.0 (async version)
  - src.bot.db      user database
  - src.bot.tiers   tier definitions

Usage (standalone):
  python -m src.bot.subscription_bot
"""
import asyncio
import json
import logging
import os
from datetime import timedelta
from typing import Optional

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from src.bot import db
from src.bot.tiers import (
    TIER_FREE,
    TIER_PRO,
    can_use_on_demand,
    can_customize_watchlist,
    watchlist_limit,
    format_tier_menu,
    get_tier_config,
)

logger = logging.getLogger(__name__)


def _get_bot_token() -> str:
    token = os.environ.get("SUBSCRIPTION_BOT_TOKEN", "")
    if token:
        return token
    import json as _json
    secret_paths = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "credentials", "tg-bot.secret.json"),
        "./credentials/tg-bot.secret.json",
    ]
    for path in secret_paths:
        if os.path.exists(path):
            with open(path) as f:
                data = _json.load(f)
            token = data.get("token", data.get("BOT_TOKEN", ""))
            if token:
                return token
    raise RuntimeError("SUBSCRIPTION_BOT_TOKEN not configured. Set env var or credentials/tg-bot.secret.json")


def _get_default_stock_list() -> list[str]:
    """Read system default stock list (used by Free tier)"""
    raw = os.environ.get("STOCK_LIST", "BTC-USD,ETH-USD,AAPL")
    return [s.strip() for s in raw.split(",") if s.strip()]


# ===========================
# Inline Keyboard Builders
# ===========================

def _main_menu_keyboard(tier: str) -> InlineKeyboardMarkup:
    """Main menu buttons shown after /start"""
    keyboard = [
        [
            InlineKeyboardButton("📋 My Plan", callback_data="cmd_subscribe"),
            InlineKeyboardButton("📊 Watchlist", callback_data="cmd_watchlist"),
        ],
    ]
    if tier == TIER_PRO:
        keyboard.append([
            InlineKeyboardButton("➕ Add Stock", callback_data="cmd_add_prompt"),
            InlineKeyboardButton("➖ Remove Stock", callback_data="cmd_remove_prompt"),
        ])
        keyboard.append([
            InlineKeyboardButton("🔍 Analyze", callback_data="cmd_analyze_prompt"),
            InlineKeyboardButton("📊 Dashboard", callback_data="cmd_dashboard"),
        ])
    keyboard.append([
        InlineKeyboardButton("💎 Upgrade to Pro", callback_data="cmd_subscribe") if tier == TIER_FREE else InlineKeyboardButton("📖 Help", callback_data="cmd_help"),
    ])
    return InlineKeyboardMarkup(keyboard)


def _watchlist_keyboard(stocks: list[str], tier: str) -> InlineKeyboardMarkup:
    """Watchlist view with analyze buttons for each stock"""
    keyboard = []
    # Each stock gets an Analyze button (Pro) or just display (Free)
    for stock in stocks[:10]:  # Max 10 buttons per row limit
        if tier == TIER_PRO:
            keyboard.append([InlineKeyboardButton(f"🔍 Analyze {stock}", callback_data=f"analyze_{stock}")])
        else:
            keyboard.append([InlineKeyboardButton(f"📈 {stock}", callback_data=f"noop")])
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="cmd_start")])
    return InlineKeyboardMarkup(keyboard)


def _subscribe_keyboard(current_tier: str) -> InlineKeyboardMarkup:
    """Subscribe page buttons"""
    keyboard = []
    if current_tier == TIER_FREE:
        keyboard.append([InlineKeyboardButton("💎 Upgrade to Pro — $9/mo", callback_data="upgrade_pro")])
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="cmd_start")])
    return InlineKeyboardMarkup(keyboard)


# ===========================
# Command Handlers
# ===========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — Register user and show welcome message"""
    user = update.effective_user
    if user is None:
        return

    db_user = db.create_user(telegram_id=user.id, username=user.username)
    tier_cfg = get_tier_config(db_user["tier"])

    welcome = (
        f"👋 Welcome to StockAnalyst, {user.first_name}!\n\n"
        f"Current plan: *{tier_cfg.label}*\n"
        f"{tier_cfg.description}\n\n"
        "Use the buttons below or type commands:"
    )
    await update.message.reply_text(
        welcome,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_main_menu_keyboard(db_user["tier"]),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — Show available commands"""
    help_text = (
        "📖 *Commands*\n\n"
        "/start — Register & view current plan\n"
        "/subscribe — View plan details & upgrade\n"
        "/watchlist — View your watchlist\n"
        "/add `<ticker>` — Add stock to watchlist\n"
        "              Example: `/add NVDA`\n"
        "/remove `<ticker>` — Remove stock from watchlist\n"
        "              Example: `/remove AAPL`\n"
        "/analyze `<ticker>` — Run analysis on a stock\n"
        "              Example: `/analyze BTC-USD`\n"
        "/dashboard — Decision Dashboard for watchlist\n"
        "              Scores all stocks: Buy/Watch/Sell\n\n"
        "💡 *Pro features:* Unlimited watchlist, on-demand analysis,\n"
        "market review, priority processing — $9/mo"
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
    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_subscribe_keyboard(db_user["tier"]),
    )


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/watchlist — Show user's watchlist with Analyze buttons"""
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
            note = "_Your watchlist is empty. Use /add to add stocks._"
        else:
            limit = watchlist_limit(tier)
            cap = f"{len(stocks)}/∞" if limit is None else f"{len(stocks)}/{limit}"
            note = f"_Stocks: {cap}. Use /add and /remove to manage._"

    if stocks:
        stock_lines = "\n".join(f"  • `{s}`" for s in stocks)
        msg = f"📋 *Watchlist*\n{stock_lines}\n\n{note}"
    else:
        msg = f"📋 *Watchlist*\n(None)\n\n{note}"

    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_watchlist_keyboard(stocks, tier),
    )


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/add <ticker> — Add stock to watchlist (Pro only)"""
    user = update.effective_user
    if user is None:
        return

    db_user = db.get_user(user.id)
    if not db_user:
        db_user = db.create_user(telegram_id=user.id, username=user.username)

    tier = db_user["tier"]

    if tier == TIER_FREE:
        await update.message.reply_text(
            "⚠️ Custom watchlist is a *Pro* feature.\n\n"
            "Current plan: Free\n\n"
            "Use /subscribe to view upgrade options.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_subscribe_keyboard(TIER_FREE),
        )
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Please provide a stock ticker.\nExample: `/add NVDA`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    ticker = args[0].strip().upper()
    if not ticker:
        await update.message.reply_text("Ticker cannot be empty.", parse_mode=ParseMode.MARKDOWN)
        return

    current = db_user.get("watchlist") or []

    if ticker in current:
        await update.message.reply_text(
            f"`{ticker}` is already in your watchlist.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    current.append(ticker)
    db.update_watchlist(user.id, current)

    stock_lines = "\n".join(f"  • `{s}`" for s in current)
    await update.message.reply_text(
        f"✅ Added `{ticker}` to your watchlist.\n\n"
        f"📋 *Your Watchlist*\n{stock_lines}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_watchlist_keyboard(current, tier),
    )


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/remove <ticker> — Remove stock from watchlist (Pro only)"""
    user = update.effective_user
    if user is None:
        return

    db_user = db.get_user(user.id)
    if not db_user:
        db_user = db.create_user(telegram_id=user.id, username=user.username)

    tier = db_user["tier"]

    if tier == TIER_FREE:
        await update.message.reply_text(
            "⚠️ Custom watchlist is a *Pro* feature.\n\n"
            "Use /subscribe to view upgrade options.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_subscribe_keyboard(TIER_FREE),
        )
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Please provide a stock ticker.\nExample: `/remove AAPL`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    ticker = args[0].strip().upper()
    current = db_user.get("watchlist") or []

    if ticker not in current:
        await update.message.reply_text(
            f"`{ticker}` is not in your watchlist.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    current.remove(ticker)
    db.update_watchlist(user.id, current)

    if current:
        stock_lines = "\n".join(f"  • `{s}`" for s in current)
        await update.message.reply_text(
            f"✅ Removed `{ticker}` from your watchlist.\n\n"
            f"📋 *Your Watchlist*\n{stock_lines}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_watchlist_keyboard(current, tier),
        )
    else:
        await update.message.reply_text(
            f"✅ Removed `{ticker}`. Your watchlist is now empty.",
            parse_mode=ParseMode.MARKDOWN,
        )


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/analyze <ticker> — Run on-demand analysis (Pro only)"""
    user = update.effective_user
    if user is None:
        return

    db_user = db.get_user(user.id)
    if not db_user:
        db_user = db.create_user(telegram_id=user.id, username=user.username)

    tier = db_user["tier"]

    if not can_use_on_demand(tier):
        tier_cfg = get_tier_config(tier)
        await update.message.reply_text(
            f"⚠️ On-demand analysis is a *Pro* feature.\n\n"
            f"Current plan: {tier_cfg.label}\n\n"
            "Use /subscribe to view upgrade options.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_subscribe_keyboard(tier),
        )
        return

    args = context.args
    if not args:
        # Show prompt with watchlist buttons for easy selection
        stocks = db_user.get("watchlist") or _get_default_stock_list()
        keyboard = []
        for stock in stocks[:8]:
            keyboard.append([InlineKeyboardButton(f"🔍 {stock}", callback_data=f"analyze_{stock}")])
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="cmd_start")])
        await update.message.reply_text(
            "Select a stock to analyze, or type `/analyze <ticker>`:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    ticker = args[0].strip().upper()
    await _run_and_send_analysis(update, ticker)


async def _run_and_send_analysis(update: Update, ticker: str) -> None:
    """Run analysis and send result. Shared between command and callback."""
    await update.message.reply_text(
        f"🔍 Analyzing `{ticker}`...\n\n"
        f"Fetching price data, technicals, and generating analysis.\n"
        f"This takes 10-30 seconds.",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        result_text = await asyncio.get_event_loop().run_in_executor(
            None, _run_single_stock_analysis, ticker, update.effective_user.id
        )
        await update.message.reply_text(result_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error("On-demand analysis failed: ticker=%s error=%s", ticker, e)
        await update.message.reply_text(
            f"❌ Error analyzing `{ticker}`. Please try again later.",
            parse_mode=ParseMode.MARKDOWN,
        )


async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/dashboard — Decision Dashboard for all watchlist stocks (Pro only)"""
    user = update.effective_user
    if user is None:
        return

    db_user = db.get_user(user.id)
    if not db_user:
        db_user = db.create_user(telegram_id=user.id, username=user.username)

    tier = db_user["tier"]

    if not can_use_on_demand(tier):
        await update.message.reply_text(
            "⚠️ Decision Dashboard is a *Pro* feature.\n\n"
            "Use /subscribe to view upgrade options.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Get watchlist
    tickers = db_user.get("watchlist") or []
    if not tickers:
        await update.message.reply_text(
            "Your watchlist is empty. Use /add to add stocks first.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.message.reply_text(
        f"📊 Generating Decision Dashboard for {len(tickers)} stocks...\n\n"
        f"This takes ~{len(tickers) * 3}-{len(tickers) * 5} seconds.",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        dashboard_text = await asyncio.get_event_loop().run_in_executor(
            None, _generate_dashboard, tickers, user.id
        )
        await update.message.reply_text(dashboard_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error("Dashboard generation failed: user=%d error=%s", user.id, e)
        await update.message.reply_text(
            "❌ Error generating dashboard. Please try again later.",
            parse_mode=ParseMode.MARKDOWN,
        )


# ===========================
# Callback Query Handler (Button presses)
# ===========================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button presses"""
    query = update.callback_query
    await query.answer()

    data = query.data
    user = query.from_user

    if data == "cmd_start":
        db_user = db.get_user(user.id) or db.create_user(telegram_id=user.id, username=user.username)
        tier_cfg = get_tier_config(db_user["tier"])
        welcome = (
            f"👋 Welcome back, {user.first_name}!\n\n"
            f"Current plan: *{tier_cfg.label}*\n"
            f"{tier_cfg.description}"
        )
        await query.edit_message_text(
            welcome,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_main_menu_keyboard(db_user["tier"]),
        )

    elif data == "cmd_subscribe":
        db_user = db.get_user(user.id) or db.create_user(telegram_id=user.id, username=user.username)
        current = get_tier_config(db_user["tier"])
        menu = format_tier_menu()
        msg = f"Current plan: *{current.label}*\n\n{menu}"
        await query.edit_message_text(
            msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_subscribe_keyboard(db_user["tier"]),
        )

    elif data == "cmd_watchlist":
        db_user = db.get_user(user.id) or db.create_user(telegram_id=user.id, username=user.username)
        tier = db_user["tier"]
        if tier == TIER_FREE:
            stocks = _get_default_stock_list()
            note = "_Free plan uses default stocks. Upgrade to Pro to customize._"
        else:
            stocks = db_user.get("watchlist") or []
            if not stocks:
                note = "_Your watchlist is empty. Use /add to add stocks._"
            else:
                note = f"_Stocks: {len(stocks)}/∞_"

        if stocks:
            stock_lines = "\n".join(f"  • `{s}`" for s in stocks)
            msg = f"📋 *Watchlist*\n{stock_lines}\n\n{note}"
        else:
            msg = f"📋 *Watchlist*\n(None)\n\n{note}"

        await query.edit_message_text(
            msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_watchlist_keyboard(stocks, tier),
        )

    elif data == "cmd_help":
        help_text = (
            "📖 *Commands*\n\n"
            "/start — Register & view current plan\n"
            "/subscribe — View plan details & upgrade\n"
            "/watchlist — View your watchlist\n"
            "/add `<ticker>` — Add stock to watchlist\n"
            "/remove `<ticker>` — Remove stock from watchlist\n"
            "/analyze `<ticker>` — Run analysis\n\n"
            "💡 *Pro ($9/mo):* Unlimited watchlist, on-demand analysis, market review"
        )
        await query.edit_message_text(help_text, parse_mode=ParseMode.MARKDOWN)

    elif data == "cmd_add_prompt":
        await query.edit_message_text(
            "➕ *Add Stock to Watchlist*\n\nType:\n`/add TICKER`\n\nExample: `/add NVDA`",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "cmd_remove_prompt":
        db_user = db.get_user(user.id) or db.create_user(telegram_id=user.id, username=user.username)
        stocks = db_user.get("watchlist") or []
        if not stocks:
            await query.edit_message_text("Your watchlist is empty. Nothing to remove.")
            return
        keyboard = []
        for stock in stocks:
            keyboard.append([InlineKeyboardButton(f"➖ {stock}", callback_data=f"remove_{stock}")])
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="cmd_watchlist")])
        await query.edit_message_text(
            "Select a stock to remove:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data.startswith("remove_"):
        ticker = data.replace("remove_", "")
        db_user = db.get_user(user.id) or db.create_user(telegram_id=user.id, username=user.username)
        current = db_user.get("watchlist") or []
        if ticker in current:
            current.remove(ticker)
            db.update_watchlist(user.id, current)
        # Refresh watchlist view
        stocks = current
        note = f"_Stocks: {len(stocks)}/∞_"
        if stocks:
            stock_lines = "\n".join(f"  • `{s}`" for s in stocks)
            msg = f"✅ Removed `{ticker}`.\n\n📋 *Watchlist*\n{stock_lines}\n\n{note}"
        else:
            msg = f"✅ Removed `{ticker}`. Your watchlist is now empty."
        await query.edit_message_text(
            msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_watchlist_keyboard(stocks, db_user["tier"]) if stocks else None,
        )

    elif data.startswith("analyze_"):
        ticker = data.replace("analyze_", "")
        # Check Pro permission
        db_user = db.get_user(user.id) or db.create_user(telegram_id=user.id, username=user.username)
        if not can_use_on_demand(db_user["tier"]):
            await query.edit_message_text(
                "⚠️ On-demand analysis is a *Pro* feature.\n\n"
                "Use /subscribe to upgrade.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await query.edit_message_text(
            f"🔍 Analyzing `{ticker}`...\n\nFetching price data, technicals, and generating analysis.\nThis takes 10-30 seconds.",
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            result_text = await asyncio.get_event_loop().run_in_executor(
                None, _run_single_stock_analysis, ticker, user.id
            )
            await context.bot.send_message(
                chat_id=user.id,
                text=result_text,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error("Callback analysis failed: ticker=%s error=%s", ticker, e)
            await context.bot.send_message(
                chat_id=user.id,
                text=f"❌ Error analyzing `{ticker}`. Please try again later.",
                parse_mode=ParseMode.MARKDOWN,
            )

    elif data == "cmd_analyze_prompt":
        db_user = db.get_user(user.id) or db.create_user(telegram_id=user.id, username=user.username)
        stocks = db_user.get("watchlist") or _get_default_stock_list()
        keyboard = []
        for stock in stocks[:8]:
            keyboard.append([InlineKeyboardButton(f"🔍 {stock}", callback_data=f"analyze_{stock}")])
        keyboard.append([InlineKeyboardButton("📊 Full Dashboard", callback_data="cmd_dashboard")])
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="cmd_start")])
        await query.edit_message_text(
            "Select a stock to analyze:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data == "cmd_dashboard":
        db_user = db.get_user(user.id) or db.create_user(telegram_id=user.id, username=user.username)
        if not can_use_on_demand(db_user["tier"]):
            await query.edit_message_text(
                "⚠️ Decision Dashboard is a *Pro* feature.\n\nUse /subscribe to upgrade.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        tickers = db_user.get("watchlist") or []
        if not tickers:
            await query.edit_message_text("Your watchlist is empty. Use /add to add stocks first.")
            return
        await query.edit_message_text(
            f"📊 Generating Decision Dashboard for {len(tickers)} stocks...\n\nThis takes ~{len(tickers) * 3}-{len(tickers) * 5} seconds.",
        )
        try:
            dashboard_text = await asyncio.get_event_loop().run_in_executor(
                None, _generate_dashboard, tickers, user.id
            )
            await context.bot.send_message(
                chat_id=user.id,
                text=dashboard_text,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error("Callback dashboard failed: user=%d error=%s", user.id, e)
            await context.bot.send_message(
                chat_id=user.id,
                text="❌ Error generating dashboard. Please try again later.",
            )

    elif data == "upgrade_pro":
        await query.edit_message_text(
            "💎 *Upgrade to Pro*\n\n"
            "Pro plan includes:\n"
            "• Unlimited custom watchlist\n"
            "• On-demand stock analysis\n"
            "• Daily market review\n"
            "• Priority processing\n\n"
            "*Price: $9/month*\n\n"
            "To upgrade, contact @admin or use the payment link below.\n\n"
            "_Payment integration coming soon!_",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "noop":
        # Free tier placeholder - no action
        pass


# ===========================
# Analysis execution (sync)
# ===========================

def _run_single_stock_analysis(ticker: str, telegram_id: int) -> str:
    """
    Run analysis pipeline synchronously, return formatted report.
    Tries the full pipeline first, falls back to GPT-4o direct analysis.
    """
    try:
        from src.core.pipeline import StockAnalysisPipeline
        pipeline = StockAnalysisPipeline()
        results = pipeline.run(stocks=[ticker], notify=False)

        if not results:
            return _direct_llm_analysis(ticker)

        result = results[0] if isinstance(results, list) else results
        if hasattr(result, "to_markdown"):
            return result.to_markdown()
        return str(result)
    except Exception as e:
        logger.warning("Full pipeline failed, falling back to direct LLM: %s", e)
        return _direct_llm_analysis(ticker)


def _direct_llm_analysis(ticker: str) -> str:
    """
    Direct LLM analysis using OpenAI GPT-4o-mini.
    Fetches comprehensive price + technical data, generates detailed analysis.
    """
    import requests as http_requests

    # Step 1: Get comprehensive price data
    price_data = _fetch_comprehensive_data(ticker)

    # Step 2: Get LLM API key (try env var, then multiple file paths)
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        # Try CWD-relative first (works in thread pool), then __file__-relative
        cwd = os.getcwd()
        key_paths = [
            os.path.join(cwd, "credentials", "openai.secret.json"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "credentials", "openai.secret.json"),
            "./credentials/openai.secret.json",
        ]
        for path in key_paths:
            try:
                if os.path.exists(path):
                    with open(path) as f:
                        data = json.load(f)
                    api_key = data.get("key", data.get("api_key", ""))
                    if api_key:
                        break
            except Exception:
                continue

    if not api_key:
        return _format_price_only_report(ticker, price_data)

    # Build comprehensive prompt matching the original StockAnalyst format
    price_context = ""
    if price_data:
        price_lines = []
        for key, value in price_data.items():
            if value is not None and value != "":
                price_lines.append(f"{key}: {value}")
        if price_lines:
            price_context = "\nCurrent market data:\n" + "\n".join(price_lines)

    prompt = (
        f"You are StockAnalyst AI, an expert financial analyst producing institutional-grade analysis. "
        f"Analyze {ticker} using the data below. Produce a COMPLETE analysis in this EXACT format:\n"
        f"{price_context}\n\n"
        f"FORMAT (follow this structure exactly):\n\n"
        f"📊 *{ticker} — Decision Score: [0-100]*\n\n"
        f"*Signal:* [🔴 Strong Bearish | 🔴 Bearish | 🟡 Sideways | 🔵 Bullish | 🔵 Strong Bullish]\n"
        f"*Action:* [🔴 Strong Sell | 🔴 Sell | 🟡 Hold/Watch | 🔵 Buy | 🔵 Strong Buy]\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Key Updates*\n"
        f"• Sentiment: [1-2 sentences on market mood]\n"
        f"• Risk Alerts: [2 specific risks]\n"
        f"• Positive Catalyst: [1-2 catalysts]\n\n"
        f"*Core Decision*\n"
        f"• Recommendation: [Buy/Watch/Sell] | Confidence: [1-2 word]\n"
        f"• One-line Decision: [1 decisive sentence]\n"
        f"• Time Sensitivity: [Today / This Week / This Month]\n\n"
        f"*Position | Action*\n"
        f"• No Position: [entry guidance]\n"
        f"• Holding: [hold/sell guidance]\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Market Snapshot*\n"
        f"• Price: [current] | Change: [+/- $ and %] | Volume: [ratio]x avg\n"
        f"• MA5: [value] | MA10: [value] | MA20: [value]\n"
        f"• MA Alignment: [Bullish ✅ | Bearish ❌ | Mixed ⚠️] | Trend Strength: [0-100]\n"
        f"• Bias from MA20: [percentage]\n\n"
        f"*Key Levels*\n"
        f"🟢 Support: [3 specific price levels]\n"
        f"🔴 Resistance: [3 specific price levels]\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*⚔️ Battle Plan*\n"
        f"IMPORTANT: Show ONLY ONE direction line. If bullish use: 🟢 Direction: LONG 📈\n"
        f"If bearish use: 🔴 Direction: SHORT 📉\n"
        f"Never show both. Pick the correct one based on the data.\n"
        f"🎯 Ideal Entry: [specific price]\n"
        f"🎯 Secondary Entry: [specific price]\n"
        f"🛑 Stop Loss: [specific price]\n"
        f"🏆 TP1 (Conservative): [specific price]\n"
        f"🏆 TP2 (Aggressive): [specific price]\n"
        f"📊 Risk/Reward: [ratio like 1:3]\n"
        f"📐 Position Size: [X/10]\n\n"
        f"*✅ Checklist*\n"
        f"1. MA Alignment (MA5 > MA10 > MA20): [Pass ✅ / Fail ❌]\n"
        f"2. Support reasonable (1-5% from price): [Pass / Fail]\n"
        f"3. Volume confirmation: [Pass / Fail]\n"
        f"4. No major negative catalysts: [Pass / Fail]\n"
        f"5. RSI not extreme (20-80): [Pass / Fail]\n"
        f"6. Trend direction clear: [Pass / Fail]\n\n"
        f"Be SPECIFIC with dollar amounts. Use the data provided to calculate real levels. "
        f"If data is insufficient, say so. Use Markdown formatting. Keep under 700 words."
    )

    try:
        resp = http_requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500,
                "temperature": 0.5,
            },
            timeout=45,
        )

        if resp.status_code == 200:
            data = resp.json()
            analysis = data["choices"][0]["message"]["content"]
            return analysis
        else:
            logger.error("OpenAI API error: %d %s", resp.status_code, resp.text[:200])
            return _format_price_only_report(ticker, price_data)
    except Exception as e:
        logger.error("Direct LLM analysis failed: %s", e)
        return _format_price_only_report(ticker, price_data)


def _fetch_comprehensive_data(ticker: str) -> dict:
    """Fetch comprehensive price + technical data from Yahoo Finance"""
    import requests as http_requests

    data = {}
    try:
        # 5-day chart data for price + volume + highs/lows
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1mo&interval=1d&includePrePost=false"
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        resp = http_requests.get(url, headers=headers, timeout=10)

        if resp.status_code != 200:
            return data

        chart = resp.json().get("chart", {}).get("result", [])
        if not chart:
            return data

        meta = chart[0].get("meta", {})
        indicators = chart[0].get("indicators", {})
        quotes = indicators.get("quote", [{}])[0] if indicators.get("quote") else {}

        # Price data
        data["Price"] = f"{meta.get('currency', 'USD')} {meta.get('regularMarketPrice', 'N/A')}"
        prev_close = meta.get("chartPreviousClose", meta.get("previousClose", "N/A"))
        data["Previous Close"] = f"{meta.get('currency', 'USD')} {prev_close}"

        if isinstance(meta.get("regularMarketPrice"), (int, float)) and isinstance(prev_close, (int, float)) and prev_close != 0:
            change = meta["regularMarketPrice"] - prev_close
            change_pct = (change / prev_close) * 100
            sign = "+" if change >= 0 else ""
            data["Change"] = f"{sign}{change:.2f} ({sign}{change_pct:.1f}%)"

        # Volume
        volumes = quotes.get("volume", [])
        closes = quotes.get("close", [])
        if volumes and closes:
            recent_vol = [v for v in volumes[-5:] if v is not None]
            if recent_vol:
                avg_vol = sum(recent_vol) / len(recent_vol)
                current_vol = recent_vol[-1]
                vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0
                data["Volume"] = f"{current_vol:,.0f}"
                data["Avg Volume (5d)"] = f"{avg_vol:,.0f}"
                data["Volume Ratio"] = f"{vol_ratio:.1f}x average"

        # Highs/Lows
        highs = [h for h in quotes.get("high", []) if h is not None]
        lows = [l for l in quotes.get("low", []) if l is not None]
        if highs and lows:
            data["5d High"] = f"{max(highs[-5:]):.2f}" if len(highs) >= 5 else f"{max(highs):.2f}"
            data["5d Low"] = f"{min(lows[-5:]):.2f}" if len(lows) >= 5 else f"{min(lows):.2f}"

        # 50DMA and 200DMA approximation from monthly data
        if closes:
            valid_closes = [c for c in closes if c is not None]
            if len(valid_closes) >= 20:
                data["20DMA"] = f"{sum(valid_closes[-20:]) / 20:.2f}"
            if len(valid_closes) >= 50:
                data["50DMA"] = f"{sum(valid_closes[-50:]) / 50:.2f}"

    except Exception as e:
        logger.warning("Yahoo Finance fetch failed: %s", e)

    # Also try to get RSI/MACD hint
    try:
        url2 = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=3mo&interval=1d"
        resp2 = http_requests.get(url2, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if resp2.status_code == 200:
            chart2 = resp2.json().get("chart", {}).get("result", [])
            if chart2:
                closes2 = [c for c in chart2[0].get("indicators", {}).get("quote", [{}])[0].get("close", []) if c is not None]
                if len(closes2) >= 14:
                    # Simple RSI calculation (14-period)
                    changes = [closes2[i] - closes2[i-1] for i in range(1, len(closes2))]
                    gains = [c for c in changes[-14:] if c > 0]
                    losses = [-c for c in changes[-14:] if c < 0]
                    avg_gain = sum(gains) / 14 if gains else 0
                    avg_loss = sum(losses) / 14 if losses else 0.001
                    rs = avg_gain / avg_loss
                    rsi = 100 - (100 / (1 + rs))
                    data["RSI (14)"] = f"{rsi:.1f}"

                # MACD hint
                if len(closes2) >= 26:
                    ema12 = closes2[-1]  # simplified
                    ema26 = sum(closes2[-26:]) / 26
                    macd_val = ema12 - ema26
                    data["MACD Signal"] = "Bullish" if macd_val > 0 else "Bearish"
    except Exception:
        pass

    return data


def _format_price_only_report(ticker: str, price_data: dict) -> str:
    """Fallback report when no LLM is available"""
    if price_data:
        lines = [f"📊 *{ticker} Market Data*\n"]
        for key, value in price_data.items():
            lines.append(f"• {key}: {value}")
        lines.append("\n_Full analysis temporarily unavailable._")
        return "\n".join(lines)
    return f"❌ Unable to fetch data for `{ticker}`. Please check the ticker symbol and try again."


def _generate_dashboard(tickers: list[str], telegram_id: int) -> str:
    """
    Generate a Decision Dashboard for all watchlist stocks.
    Fetches price data for each, uses LLM to score, categorizes into Buy/Watch/Sell.
    """
    import requests as http_requests

    # Get API key
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        cwd = os.getcwd()
        key_paths = [
            os.path.join(cwd, "credentials", "openai.secret.json"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "credentials", "openai.secret.json"),
            "./credentials/openai.secret.json",
        ]
        for path in key_paths:
            try:
                if os.path.exists(path):
                    with open(path) as f:
                        data = json.load(f)
                    api_key = data.get("key", data.get("api_key", ""))
                    if api_key:
                        break
            except Exception:
                continue

    # Fetch price data for all tickers
    all_price_data = {}
    for ticker in tickers[:15]:  # max 15 stocks
        all_price_data[ticker] = _fetch_comprehensive_data(ticker)

    # Build price summary for LLM
    price_summary_lines = []
    for ticker, pdata in all_price_data.items():
        if pdata:
            line = f"{ticker}: " + ", ".join(f"{k}={v}" for k, v in pdata.items() if v)
        else:
            line = f"{ticker}: No data available"
        price_summary_lines.append(line)
    price_summary = "\n".join(price_summary_lines)

    if not api_key:
        # No LLM - generate simple dashboard from price data only
        return _generate_simple_dashboard(tickers, all_price_data)

    # Ask LLM to score each stock and categorize
    prompt = (
        f"You are StockAnalyst AI. Score each stock below on a 0-100 scale based on the market data provided. "
        f"Categorize each as Buy (score 70+), Watch (score 35-69), or Sell (score below 35). "
        f"Also assign a signal: Strong Bullish, Bullish, Sideways, Bearish, or Strong Bearish.\n\n"
        f"Market data:\n{price_summary}\n\n"
        f"Respond in this EXACT format, one stock per line:\n"
        f"TICKER | Score | Signal | Action\n"
        f"Example: NVDA | 85 | Strong Bullish | Buy\n\n"
        f"List ALL {len(tickers)} stocks. No extra text."
    )

    try:
        resp = http_requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 600,
                "temperature": 0.3,
            },
            timeout=45,
        )

        if resp.status_code != 200:
            logger.error("Dashboard LLM error: %d", resp.status_code)
            return _generate_simple_dashboard(tickers, all_price_data)

        data = resp.json()
        llm_output = data["choices"][0]["message"]["content"]
        return _format_dashboard(tickers, llm_output)

    except Exception as e:
        logger.error("Dashboard generation failed: %s", e)
        return _generate_simple_dashboard(tickers, all_price_data)


def _format_dashboard(tickers: list[str], llm_output: str) -> str:
    """Parse LLM output and format as Decision Dashboard"""
    # Parse LLM output into structured data
    stocks = {"buy": [], "watch": [], "sell": []}

    for line in llm_output.strip().split("\n"):
        line = line.strip()
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue

        ticker = parts[0].strip().replace("*", "")
        try:
            score = int("".join(c for c in parts[1] if c.isdigit()))
        except (ValueError, IndexError):
            score = 50
        signal = parts[2].strip() if len(parts) > 2 else "Sideways"
        action = parts[3].strip() if len(parts) > 3 else "Watch"

        entry = {"ticker": ticker, "score": score, "signal": signal, "action": action}

        action_lower = action.lower()
        if "buy" in action_lower or "strong buy" in action_lower:
            stocks["buy"].append(entry)
        elif "sell" in action_lower or "strong sell" in action_lower:
            stocks["sell"].append(entry)
        else:
            stocks["watch"].append(entry)

    # Sort each category by score descending
    for key in stocks:
        stocks[key].sort(key=lambda x: x["score"], reverse=True)

    # Build dashboard
    total = sum(len(v) for v in stocks.values())
    from datetime import datetime, timezone as tz
    date_str = datetime.now(tz(timedelta(hours=10))).strftime("%Y-%m-%d")

    lines = [
        f"📊 *Decision Dashboard* — {date_str}",
        f"",
        f"Analyzed {total} stocks | Buy: {len(stocks['buy'])} | Watch: {len(stocks['watch'])} | Sell: {len(stocks['sell'])}",
        f"",
    ]

    if stocks["buy"]:
        lines.append("*🟢 Buy*")
        for s in stocks["buy"]:
            lines.append(f"  • `{s['ticker']}` — Score {s['score']} | {s['signal']}")
        lines.append("")

    if stocks["watch"]:
        lines.append("*🟡 Watch*")
        for s in stocks["watch"]:
            lines.append(f"  • `{s['ticker']}` — Score {s['score']} | {s['signal']}")
        lines.append("")

    if stocks["sell"]:
        lines.append("*🔴 Sell*")
        for s in stocks["sell"]:
            lines.append(f"  • `{s['ticker']}` — Score {s['score']} | {s['signal']}")
        lines.append("")

    lines.append("_Use /analyze TICKER for full Battle Plan on any stock._")

    return "\n".join(lines)


def _generate_simple_dashboard(tickers: list[str], all_price_data: dict) -> str:
    """Fallback dashboard without LLM - uses price data + RSI only"""
    from datetime import datetime, timezone as tz
    from datetime import timedelta
    date_str = datetime.now(tz(timedelta(hours=10))).strftime("%Y-%m-%d")

    stocks = {"buy": [], "watch": [], "sell": []}

    for ticker in tickers:
        pdata = all_price_data.get(ticker, {})
        score = 50  # default
        signal = "Sideways"

        # Simple scoring based on available data
        change_str = pdata.get("Change", "")
        rsi_str = pdata.get("RSI (14)", "")
        macd = pdata.get("MACD Signal", "")

        # Parse change %
        change_pct = 0
        if change_str:
            try:
                pct = change_str.split("(")[-1].replace("%)", "").replace("+", "").replace("%", "")
                change_pct = float(pct)
            except (ValueError, IndexError):
                pass

        # Parse RSI
        rsi = 50
        if rsi_str:
            try:
                rsi = float(rsi_str.replace("~", ""))
            except (ValueError, IndexError):
                pass

        # Score calculation
        score = 50
        if change_pct > 2:
            score += 20
        elif change_pct > 0:
            score += 10
        elif change_pct < -2:
            score -= 20
        elif change_pct < 0:
            score -= 10

        if rsi > 60:
            score += 10
        elif rsi < 40:
            score -= 10

        if "Bullish" in macd:
            score += 10
        elif "Bearish" in macd:
            score -= 10

        score = max(0, min(100, score))

        # Signal
        if score >= 70:
            signal = "Strong Bullish" if score >= 80 else "Bullish"
        elif score >= 40:
            signal = "Sideways"
        else:
            signal = "Strong Bearish" if score < 25 else "Bearish"

        # Action
        if score >= 70:
            action = "Buy"
        elif score >= 35:
            action = "Watch"
        else:
            action = "Sell"

        entry = {"ticker": ticker, "score": score, "signal": signal, "action": action}
        stocks[action.lower()].append(entry)

    # Sort
    for key in stocks:
        stocks[key].sort(key=lambda x: x["score"], reverse=True)

    total = sum(len(v) for v in stocks.values())

    lines = [
        f"📊 *Decision Dashboard* — {date_str}",
        f"",
        f"Analyzed {total} stocks | Buy: {len(stocks['buy'])} | Watch: {len(stocks['watch'])} | Sell: {len(stocks['sell'])}",
        f"",
    ]

    if stocks["buy"]:
        lines.append("*🟢 Buy*")
        for s in stocks["buy"]:
            lines.append(f"  • `{s['ticker']}` — Score {s['score']} | {s['signal']}")
        lines.append("")

    if stocks["watch"]:
        lines.append("*🟡 Watch*")
        for s in stocks["watch"]:
            lines.append(f"  • `{s['ticker']}` — Score {s['score']} | {s['signal']}")
        lines.append("")

    if stocks["sell"]:
        lines.append("*🔴 Sell*")
        for s in stocks["sell"]:
            lines.append(f"  • `{s['ticker']}` — Score {s['score']} | {s['signal']}")
        lines.append("")

    lines.append("_Use /analyze TICKER for full Battle Plan._")

    return "\n".join(lines)


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
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))

    # Register callback query handler (button presses)
    app.add_handler(CallbackQueryHandler(button_callback))

    return app


async def _set_bot_commands(app: Application) -> None:
    """Register command menu with Telegram"""
    commands = [
        BotCommand("start", "Register & view current plan"),
        BotCommand("help", "View all commands"),
        BotCommand("subscribe", "View plan options"),
        BotCommand("watchlist", "View your watchlist"),
        BotCommand("add", "Add stock to watchlist (Pro)"),
        BotCommand("remove", "Remove stock from watchlist (Pro)"),
        BotCommand("analyze", "On-demand stock analysis (Pro)"),
        BotCommand("dashboard", "Decision Dashboard for watchlist (Pro)"),
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