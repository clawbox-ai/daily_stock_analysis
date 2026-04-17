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
    JobQueue,
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
        InlineKeyboardButton("📧 Email", callback_data="cmd_email_menu"),
        InlineKeyboardButton("💎 Upgrade" if tier == TIER_FREE else "📖 Help", callback_data="cmd_subscribe" if tier == TIER_FREE else "cmd_help"),
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
        "/email `<email>` — Set email for daily delivery (Pro)\n"
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


async def cmd_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/email — Set up email delivery for Pro daily analysis (one email per day)"""
    user = update.effective_user
    if user is None:
        return

    db_user = db.get_user(user.id)
    if not db_user:
        db_user = db.create_user(telegram_id=user.id, username=user.username)

    tier = db_user["tier"]

    if tier == TIER_FREE:
        await update.message.reply_text(
            "⚠️ Email delivery is a *Pro* feature.\n\n"
            "Upgrade to Pro to receive your full watchlist analysis by email every day.\n"
            "Use /subscribe to upgrade.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_subscribe_keyboard(tier),
        )
        return

    args = context.args

    # No args: show current settings or setup guide
    if not args:
        schedule = db.get_email_schedule(user.id)
        if schedule["email"]:
            from src.bot.city_timezones import resolve_timezone, get_utc_offset_display
            tz_display = get_utc_offset_display(schedule["timezone"]) if schedule["timezone"] else "Not set"
            city = schedule["timezone"] or "Not set"
            keyboard = [
                [InlineKeyboardButton("📧 Change Email", callback_data="email_set_prompt")],
                [InlineKeyboardButton("📍 Change City", callback_data="email_city_prompt")],
                [InlineKeyboardButton("🕐 Change Time", callback_data="email_time_prompt")],
                [InlineKeyboardButton("🔴 Turn Off", callback_data="email_off")],
                [InlineKeyboardButton("⬅️ Back", callback_data="cmd_start")],
            ]
            await update.message.reply_text(
                f"📧 *Email Delivery*\n\n"
                f"• Email: `{schedule['email']}`\n"
                f"• City: {city} ({tz_display})\n"
                f"• Delivery: {schedule['delivery_time']} local time\n"
                f"• Frequency: Once daily\n\n"
                f"_⚠️ Email delivery coming soon — settings are saved and ready!_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            # Button-based setup flow
            keyboard = [
                [InlineKeyboardButton("📧 Set Email", callback_data="email_set_prompt")],
                [InlineKeyboardButton("📍 Set City", callback_data="email_city_prompt")],
                [InlineKeyboardButton("🕐 Set Time", callback_data="email_time_prompt")],
                [InlineKeyboardButton("⬅️ Back", callback_data="cmd_start")],
            ]
            await update.message.reply_text(
                "📧 *Email Delivery Setup*\n\n"
                "Get your full watchlist analysis delivered by email once daily.\n\n"
                "Tap a button below to configure:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        return

    action = args[0].strip().lower()

    # Turn off
    if action in ("off", "remove", "delete", "none"):
        db.set_email_schedule(user.id, None, None, "08:00")
        await update.message.reply_text(
            "📧 Email delivery turned off. You'll still get Telegram broadcasts.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Set city/timezone
    if action == "city" and len(args) > 1:
        from src.bot.city_timezones import resolve_timezone, get_utc_offset_display
        city_input = " ".join(args[1:]).strip()
        timezone = resolve_timezone(city_input)
        if not timezone:
            from src.bot.city_timezones import get_popular_cities
            popular = get_popular_cities()
            city_list = " | ".join(popular[:15])
            await update.message.reply_text(
                f"❌ Couldn't find timezone for *{city_input}*.\n\n"
                f"Try one of these cities:\n{city_list}\n\n"
                f"Or use a timezone directly: `/email city Australia/Brisbane`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        schedule = db.get_email_schedule(user.id)
        if not schedule["email"]:
            await update.message.reply_text(
                "⚠️ Set your email first: `/email your@email.com`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        db.set_email_schedule(user.id, schedule["email"], timezone, schedule["delivery_time"])
        offset = get_utc_offset_display(timezone)
        await update.message.reply_text(
            f"✅ City set to *{city_input}* ({offset})\n\n"
            f"📧 Delivery at {schedule['delivery_time']} your local time.\n"
            f"Change time: `/email time 07:30`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Set delivery time
    if action == "time" and len(args) > 1:
        time_input = args[1].strip()
        # Validate HH:MM format
        import re
        if not re.match(r"^\d{1,2}:\d{2}$", time_input):
            await update.message.reply_text(
                "❌ Use 24-hour format: `/email time 08:00` or `/email time 17:30`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        hour, minute = time_input.split(":")
        if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            await update.message.reply_text(
                "❌ Invalid time. Use 24-hour format: `/email time 08:00`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        schedule = db.get_email_schedule(user.id)
        if not schedule["email"]:
            await update.message.reply_text(
                "⚠️ Set your email first: `/email your@email.com`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        db.set_email_schedule(user.id, schedule["email"], schedule["timezone"], time_input)
        tz_display = schedule["timezone"] or "UTC"
        await update.message.reply_text(
            f"✅ Delivery time set to *{time_input}* ({tz_display})\n\n"
            f"📧 Your full analysis will arrive at {time_input} your local time.\n"
            f"Change city: `/email city Sydney`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Set email address
    if "@" in action and "." in action.split("@")[-1]:
        schedule = db.get_email_schedule(user.id)
        db.set_email_schedule(user.id, action, schedule["timezone"], schedule["delivery_time"])
        if schedule["timezone"]:
            await update.message.reply_text(
                f"✅ Email set to `{action}`\n\n"
                f"📧 Delivery at {schedule['delivery_time']} your local time.\n"
                f"Change city: `/email city London`\n"
                f"Change time: `/email time 09:00`",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(
                f"✅ Email set to `{action}`\n\n"
                f"📍 Next step: Set your city for delivery time\n"
                f"Example: `/email city Tokyo` or `/email city New York`\n\n"
                f"_We calculate the timezone from your city — no UTC math needed!_",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    # Unknown input
    await update.message.reply_text(
        "📧 *Email Delivery Commands*\n\n"
        "• `/email your@email.com` — Set email\n"
        "• `/email city Brisbane` — Set your city\n"
        "• `/email time 08:00` — Set delivery time\n"
        "• `/email off` — Turn off delivery\n"
        "• `/email` — View current settings",
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

    if tier == TIER_FREE:
        await update.message.reply_text(
            "⚠️ On-demand analysis is a *Pro* feature.\n\n"
            "🆓 Free plan includes a *daily random stock Battle Plan* broadcast — no requests needed!\n\n"
            "💎 Upgrade to Pro for unlimited analyses + custom watchlist + email delivery.\n"
            "Use /subscribe to upgrade.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_subscribe_keyboard(tier),
        )
        return

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
    await _run_and_send_analysis(update, ticker, context)


async def _run_and_send_analysis(update: Update, ticker: str, context: ContextTypes.DEFAULT_TYPE = None) -> None:
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

    # Free users get dashboard for default stocks only
    if tier == TIER_FREE:
        tickers = _get_default_stock_list()
        note = "_🆓 Free plan: showing default stocks. Upgrade to Pro for custom watchlist dashboard._"
    else:
        tickers = db_user.get("watchlist") or []
        note = None

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

    elif data == "cmd_email_menu":
        db_user = db.get_user(user.id) or db.create_user(telegram_id=user.id, username=user.username)
        if db_user["tier"] == TIER_FREE:
            await query.edit_message_text(
                "📧 *Email Delivery*\n\n"
                "⚠️ This feature is for *Pro* members only.\n\n"
                "💎 Upgrade to Pro for:\n"
                "• Daily email with full watchlist analysis\n"
                "• Custom delivery time\n"
                "• City-based timezone — no UTC math\n\n"
                "Use /subscribe to upgrade!",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        # Pro user — show email submenu
        schedule = db.get_email_schedule(user.id)
        if schedule["email"]:
            from src.bot.city_timezones import get_utc_offset_display
            tz_display = get_utc_offset_display(schedule["timezone"]) if schedule["timezone"] else "Not set"
            city = schedule["timezone"] or "Not set"
            keyboard = [
                [InlineKeyboardButton("📧 Change Email", callback_data="email_set_prompt")],
                [InlineKeyboardButton("📍 Change City", callback_data="email_city_prompt")],
                [InlineKeyboardButton("🕐 Change Time", callback_data="email_time_prompt")],
                [InlineKeyboardButton("🔴 Turn Off Email", callback_data="email_off")],
                [InlineKeyboardButton("⬅️ Back", callback_data="cmd_start")],
            ]
            await query.edit_message_text(
                f"📧 *Email Delivery*\n\n"
                f"• Email: `{schedule['email']}`\n"
                f"• City: {city} ({tz_display})\n"
                f"• Delivery: {schedule['delivery_time']} local time\n"
                f"• Frequency: Once daily\n\n"
                f"_Email delivery coming soon — settings saved!_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            keyboard = [
                [InlineKeyboardButton("📧 Set Email", callback_data="email_set_prompt")],
                [InlineKeyboardButton("📍 Set City", callback_data="email_city_prompt")],
                [InlineKeyboardButton("🕐 Set Time", callback_data="email_time_prompt")],
                [InlineKeyboardButton("⬅️ Back", callback_data="cmd_start")],
            ]
            await query.edit_message_text(
                "📧 *Email Delivery Setup*\n\n"
                "Get your full watchlist analysis delivered by email once daily.\n\n"
                "Tap a button to configure:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    elif data == "email_set_prompt":
        await query.edit_message_text(
            "📧 *Set Your Email*\n\n"
            "Type: `/email your@email.com`\n\n"
            "Example: `/email john@gmail.com`",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "email_city_prompt":
        from src.bot.city_timezones import get_popular_cities
        cities = get_popular_cities()
        # Show popular cities as buttons
        keyboard = []
        row = []
        for i, city in enumerate(cities[:12]):
            row.append(InlineKeyboardButton(city, callback_data=f"email_city_{city.lower().replace(' ','_')}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="cmd_email_view")])
        await query.edit_message_text(
            "📍 *Set Your City*\n\n"
            "Tap a city below or type: `/email city YourCityName`\n\n"
            "_We calculate the timezone from your city!_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data.startswith("email_city_"):
        city_name = data.replace("email_city_", "").replace("_", " ")
        from src.bot.city_timezones import resolve_timezone, get_utc_offset_display
        timezone = resolve_timezone(city_name)
        if timezone:
            schedule = db.get_email_schedule(user.id)
            email = schedule.get("email")
            if email:
                db.set_email_schedule(user.id, email, timezone, schedule.get("delivery_time", "08:00"))
                offset = get_utc_offset_display(timezone)
                keyboard = [
                    [InlineKeyboardButton("🕐 Change Time", callback_data="email_time_prompt")],
                    [InlineKeyboardButton("📧 Email Settings", callback_data="cmd_email_view")],
                ]
                await query.edit_message_text(
                    f"✅ City set to *{city_name.title()}* ({offset})\n\n"
                    f"📧 Delivery at {schedule.get('delivery_time', '08:00')} your local time.\n\n"
                    f"_⚠️ Email delivery coming soon — settings saved!_",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            else:
                await query.edit_message_text(
                    f"✅ City: *{city_name.title()}*\n\n"
                    f"⚠️ Set your email first!\n"
                    f"Type: `/email your@email.com`",
                    parse_mode=ParseMode.MARKDOWN,
                )
        else:
            await query.edit_message_text(
                f"❌ Couldn't find timezone for *{city_name}*.\n\n"
                f"Try typing: `/email city YourCityName`",
                parse_mode=ParseMode.MARKDOWN,
            )

    elif data == "email_time_prompt":
        keyboard = [
            [
                InlineKeyboardButton("06:00", callback_data="email_time_06:00"),
                InlineKeyboardButton("07:00", callback_data="email_time_07:00"),
                InlineKeyboardButton("08:00", callback_data="email_time_08:00"),
            ],
            [
                InlineKeyboardButton("09:00", callback_data="email_time_09:00"),
                InlineKeyboardButton("17:00", callback_data="email_time_17:00"),
                InlineKeyboardButton("18:00", callback_data="email_time_18:00"),
            ],
            [
                InlineKeyboardButton("21:00", callback_data="email_time_21:00"),
                InlineKeyboardButton("22:00", callback_data="email_time_22:00"),
                InlineKeyboardButton("23:00", callback_data="email_time_23:00"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="cmd_email_view")],
        ]
        await query.edit_message_text(
            "🕐 *Set Delivery Time*\n\n"
            "Tap a time below or type: `/email time HH:MM`\n\n"
            "_Time is in your local timezone based on your city._",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data.startswith("email_time_"):
        time_input = data.replace("email_time_", "")
        schedule = db.get_email_schedule(user.id)
        if schedule.get("email"):
            db.set_email_schedule(user.id, schedule["email"], schedule.get("timezone"), time_input)
            tz_display = schedule.get("timezone") or "UTC"
            keyboard = [
                [InlineKeyboardButton("📧 Email Settings", callback_data="cmd_email_view")],
            ]
            await query.edit_message_text(
                f"✅ Delivery time set to *{time_input}*\n\n"
                f"📧 Your analysis arrives at {time_input} ({tz_display}) local time.\n\n"
                f"_⚠️ Email delivery coming soon — settings saved!_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await query.edit_message_text(
                f"⚠️ Set your email first!\n\n"
                f"Type: `/email your@email.com`",
                parse_mode=ParseMode.MARKDOWN,
            )

    elif data == "email_off":
        db.set_email_schedule(user.id, None, None, "08:00")
        await query.edit_message_text(
            "📧 Email delivery turned off.\n\n"
            "You'll still get Telegram broadcasts.\n"
            "Re-enable anytime with `/email your@email.com`",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "cmd_email_view":
        # Re-show /email settings
        db_user = db.get_user(user.id) or db.create_user(telegram_id=user.id, username=user.username)
        if db_user["tier"] == TIER_FREE:
            await query.edit_message_text("⚠️ Email delivery is a Pro feature.")
            return
        schedule = db.get_email_schedule(user.id)
        if schedule["email"]:
            from src.bot.city_timezones import resolve_timezone, get_utc_offset_display
            tz_display = get_utc_offset_display(schedule["timezone"]) if schedule["timezone"] else "Not set"
            city = schedule["timezone"] or "Not set"
            keyboard = [
                [InlineKeyboardButton("📧 Change Email", callback_data="email_set_prompt")],
                [InlineKeyboardButton("📍 Change City", callback_data="email_city_prompt")],
                [InlineKeyboardButton("🕐 Change Time", callback_data="email_time_prompt")],
                [InlineKeyboardButton("🔴 Turn Off", callback_data="email_off")],
                [InlineKeyboardButton("⬅️ Back", callback_data="cmd_start")],
            ]
            await query.edit_message_text(
                f"📧 *Email Delivery*\n\n"
                f"• Email: `{schedule['email']}`\n"
                f"• City: {city} ({tz_display})\n"
                f"• Delivery: {schedule['delivery_time']} local time\n"
                f"• Frequency: Once daily\n\n"
                f"_⚠️ Email delivery coming soon — settings are saved and ready!_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            keyboard = [
                [InlineKeyboardButton("📧 Set Email", callback_data="email_set_prompt")],
                [InlineKeyboardButton("📍 Set City", callback_data="email_city_prompt")],
                [InlineKeyboardButton("🕐 Set Time", callback_data="email_time_prompt")],
                [InlineKeyboardButton("⬅️ Back", callback_data="cmd_start")],
            ]
            await query.edit_message_text(
                "📧 *Email Delivery Setup*\n\n"
                "Get your full watchlist analysis delivered by email once daily.\n\n"
                "Tap a button below to configure:",
                parse_mode=ParseMode.MARKDOWN,
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


def _calculate_deterministic_score(price_data: dict) -> dict:
    """
    Calculate a deterministic decision score from price data.
    Score is 0-100 based on RSI, MA alignment, MACD, price change, and volume.
    Same input = same output every time.
    """
    score = 50  # neutral baseline

    # Parse change %
    change_pct = 0
    change_str = price_data.get("Change", "")
    if change_str:
        try:
            pct = change_str.split("(")[-1].replace("%)", "").replace("+", "")
            change_pct = float(pct)
        except (ValueError, IndexError):
            pass

    # Parse RSI
    rsi = 50
    rsi_str = price_data.get("RSI (14)", "")
    if rsi_str:
        try:
            rsi = float(rsi_str.replace("~", ""))
        except (ValueError, IndexError):
            pass

    # Parse MACD
    macd = price_data.get("MACD Signal", "")
    macd_bullish = "Bullish" in macd if macd else False
    macd_bearish = "Bearish" in macd if macd else False

    # Parse price vs 20DMA
    price = None
    dma20 = None
    price_str = price_data.get("Price", "")
    dma20_str = price_data.get("20DMA", "")
    if price_str:
        try:
            price = float(price_str.split()[-1].replace(",", ""))
        except (ValueError, IndexError):
            pass
    if dma20_str:
        try:
            dma20 = float(dma20_str.replace(",", ""))
        except (ValueError, IndexError):
            pass

    # === SCORING COMPONENTS ===

    # 1. Price Change (±15 points)
    if change_pct > 3:
        score += 15
    elif change_pct > 1:
        score += 10
    elif change_pct > 0:
        score += 5
    elif change_pct < -3:
        score -= 15
    elif change_pct < -1:
        score -= 10
    elif change_pct < 0:
        score -= 5

    # 2. RSI (±15 points)
    if rsi > 70:
        score -= 10  # overbought = risk
    elif rsi > 55:
        score += 12  # bullish momentum
    elif rsi > 45:
        score += 0   # neutral
    elif rsi > 30:
        score -= 8   # weak
    else:
        score += 5   # oversold = potential bounce

    # 3. MACD (±10 points)
    if macd_bullish:
        score += 10
    elif macd_bearish:
        score -= 10

    # 4. Price vs 20DMA (±15 points)
    if price is not None and dma20 is not None and dma20 > 0:
        pct_from_dma = ((price - dma20) / dma20) * 100
        if pct_from_dma > 5:
            score += 15  # strong above MA
        elif pct_from_dma > 2:
            score += 10
        elif pct_from_dma > 0:
            score += 5
        elif pct_from_dma > -2:
            score -= 5
        elif pct_from_dma > -5:
            score -= 10
        else:
            score -= 15

    # 5. Volume confirmation (±5 points)
    vol_str = price_data.get("Volume Ratio", "")
    if vol_str:
        try:
            vol_ratio = float(vol_str.replace("x average", "").strip())
            if vol_ratio > 1.5:
                score += 5  # high volume confirms move
            elif vol_ratio > 1.0:
                score += 2
            elif vol_ratio < 0.5:
                score -= 3  # low volume = weak conviction
        except (ValueError, IndexError):
            pass

    # Clamp 0-100
    score = max(0, min(100, score))

    # === DETERMINE SIGNAL, ACTION, DIRECTION ===

    if score >= 80:
        signal = "Strong Bullish"
        action = "Strong Buy"
        direction = "LONG"
        direction_color = "🔵"
    elif score >= 65:
        signal = "Bullish"
        action = "Buy"
        direction = "LONG"
        direction_color = "🔵"
    elif score >= 45:
        signal = "Sideways"
        action = "Hold"
        direction = "NEUTRAL"
        direction_color = "🟡"
    elif score >= 25:
        signal = "Bearish"
        action = "Sell"
        direction = "SHORT"
        direction_color = "🔴"
    else:
        signal = "Strong Bearish"
        action = "Strong Sell"
        direction = "SHORT"
        direction_color = "🔴"

    # MA Alignment
    ma_alignment = "Mixed ⚠️"
    if price is not None and dma20 is not None:
        if price > dma20:
            ma_alignment = "Bullish ✅"
        else:
            ma_alignment = "Bearish ❌"

    # Trend Strength (0-100)
    trend_strength = 50
    if price is not None and dma20 is not None and dma20 > 0:
        pct = abs((price - dma20) / dma20) * 100
        trend_strength = min(100, int(50 + pct * 5))
    if macd_bullish:
        trend_strength = min(100, trend_strength + 10)
    elif macd_bearish:
        trend_strength = max(0, trend_strength - 10)

    return {
        "score": score,
        "signal": signal,
        "action": action,
        "direction": direction,
        "direction_color": direction_color,
        "ma_alignment": ma_alignment,
        "trend_strength": trend_strength,
        "rsi": rsi,
        "change_pct": change_pct,
        "macd_bullish": macd_bullish,
        "price_above_dma20": price is not None and dma20 is not None and price > dma20,
    }


def _direct_llm_analysis(ticker: str) -> str:
    """
    Direct LLM analysis using OpenAI GPT-4o-mini.
    Score/Signal/Action/Direction are calculated deterministically from math.
    LLM only writes the narrative — it cannot change the numbers.
    """
    import requests as http_requests

    # Step 1: Get comprehensive price data
    price_data = _fetch_comprehensive_data(ticker)

    # Step 1b: Calculate deterministic score from math (same input = same output)
    ds = _calculate_deterministic_score(price_data)

    # Step 2: Get LLM API key
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

    if not api_key:
        return _format_deterministic_report(ticker, price_data, ds)

    # Build price context
    price_context = ""
    if price_data:
        price_lines = []
        for key, value in price_data.items():
            if value is not None and value != "":
                price_lines.append(f"{key}: {value}")
        if price_lines:
            price_context = "\nCurrent market data:\n" + "\n".join(price_lines)

    # Determine direction emoji
    if ds["direction"] == "LONG":
        dir_line = "🟢 Direction: LONG 📈"
    elif ds["direction"] == "SHORT":
        dir_line = "🔴 Direction: SHORT 📉"
    else:
        dir_line = "🟡 Direction: NEUTRAL ➡️"

    # Determine signal/action emoji
    sig_emoji = "🔵" if "Bullish" in ds["signal"] else ("🔴" if "Bearish" in ds["signal"] else "🟡")
    act_emoji = "🔵" if "Buy" in ds["action"] else ("🔴" if "Sell" in ds["action"] else "🟡")

    prompt = (
        f"You are StockAnalyst AI. Analyze {ticker} using the data below. "
        f"IMPORTANT: The score, signal, action, and direction are PRE-CALCULATED and FIXED. "
        f"You MUST use these exact values — do NOT change them. Your job is to write the narrative sections.\n"
        f"{price_context}\n\n"
        f"PRE-CALCULATED VALUES (use exactly, do not change):\n"
        f"- Decision Score: {ds['score']}/100\n"
        f"- Signal: {sig_emoji} {ds['signal']}\n"
        f"- Action: {act_emoji} {ds['action']}\n"
        f"- Direction: {dir_line}\n"
        f"- MA Alignment: {ds['ma_alignment']} | Trend Strength: {ds['trend_strength']}/100\n"
        f"- RSI: {ds['rsi']:.1f}\n"
        f"- Price Change: {ds['change_pct']:+.1f}%\n"
        f"- Price above 20DMA: {'Yes' if ds['price_above_dma20'] else 'No'}\n\n"
        f"Produce the COMPLETE analysis in this EXACT format:\n\n"
        f"📊 *{ticker} — Decision Score: {ds['score']}*\n\n"
        f"*Signal:* {sig_emoji} *{ds['signal']}*\n"
        f"*Action:* {act_emoji} *{ds['action']}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Key Updates*\n"
        f"• Sentiment: [1-2 sentences based on the data]\n"
        f"• Risk Alerts: [2 specific risks]\n"
        f"• Positive Catalyst: [1-2 catalysts]\n\n"
        f"*Core Decision*\n"
        f"• Recommendation: {ds['action']} | Confidence: [based on score strength]\n"
        f"• One-line Decision: [1 decisive sentence]\n"
        f"• Time Sensitivity: [Today / This Week / This Month]\n\n"
        f"*Position | Action*\n"
        f"• No Position: [entry guidance]\n"
        f"• Holding: [hold/sell guidance]\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Market Snapshot*\n"
        f"• Price: [from data] | Change: [from data] | Volume: [from data]\n"
        f"• MA Alignment: {ds['ma_alignment']} | Trend Strength: {ds['trend_strength']}/100\n"
        f"• Bias from MA20: [calculate from data]\n\n"
        f"*Key Levels*\n"
        f"🟢 Support: [3 specific price levels from data]\n"
        f"🔴 Resistance: [3 specific price levels from data]\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*⚔️ Battle Plan*\n"
        f"{dir_line}\n"
        f"🎯 Ideal Entry: [specific price]\n"
        f"🎯 Secondary Entry: [specific price]\n"
        f"🛑 Stop Loss: [specific price]\n"
        f"🏆 TP1 (Conservative): [specific price]\n"
        f"🏆 TP2 (Aggressive): [specific price]\n"
        f"📊 Risk/Reward: [ratio]\n"
        f"📐 Position Size: [1-10 based on score confidence]\n\n"
        f"*✅ Checklist*\n"
        f"1. MA Alignment: {'Pass ✅' if 'Bullish' in ds['ma_alignment'] else 'Fail ❌'}\n"
        f"2. Support reasonable (1-5%): [Pass / Fail]\n"
        f"3. Volume confirmation: [Pass / Fail]\n"
        f"4. No major negative catalysts: [Pass / Fail]\n"
        f"5. RSI not extreme (20-80): [Pass / Fail]\n"
        f"6. Trend direction clear: [Pass / Fail]\n\n"
        f"Be SPECIFIC with dollar amounts. Use the data to calculate real levels. "
        f"Keep under 700 words. Use Markdown."
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
                "temperature": 0.3,
            },
            timeout=45,
        )

        if resp.status_code == 200:
            data = resp.json()
            analysis = data["choices"][0]["message"]["content"]
            # Force the score header to be deterministic (LLM might hallucinate a different one)
            expected_header = f"📊 *{ticker} — Decision Score: {ds['score']}*"
            if expected_header not in analysis:
                # Replace whatever header the LLM put with the correct one
                import re
                analysis = re.sub(
                    r"📊 \*.*?— Decision Score:.*?\*",
                    expected_header,
                    analysis
                )
            return analysis
        else:
            logger.error("OpenAI API error: %d %s", resp.status_code, resp.text[:200])
            return _format_deterministic_report(ticker, price_data, ds)
    except Exception as e:
        logger.error("Direct LLM analysis failed: %s", e)
        return _format_deterministic_report(ticker, price_data, ds)


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


def _format_deterministic_report(ticker: str, price_data: dict, ds: dict) -> str:
    """Full deterministic report when no LLM is available — score from math, not guessing"""

    # Determine direction line
    if ds["direction"] == "LONG":
        dir_line = "🟢 Direction: LONG 📈"
    elif ds["direction"] == "SHORT":
        dir_line = "🔴 Direction: SHORT 📉"
    else:
        dir_line = "🟡 Direction: NEUTRAL ➡️"

    sig_emoji = "🔵" if "Bullish" in ds["signal"] else ("🔴" if "Bearish" in ds["signal"] else "🟡")
    act_emoji = "🔵" if "Buy" in ds["action"] else ("🔴" if "Sell" in ds["action"] else "🟡")

    lines = [
        f"📊 *{ticker} — Decision Score: {ds['score']}*",
        f"",
        f"*Signal:* {sig_emoji} *{ds['signal']}*",
        f"*Action:* {act_emoji} *{ds['action']}*",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"*Market Snapshot*",
    ]

    for key, value in price_data.items():
        if value is not None and value != "":
            lines.append(f"• {key}: {value}")

    lines.extend([
        f"",
        f"• MA Alignment: {ds['ma_alignment']} | Trend Strength: {ds['trend_strength']}/100",
        f"",
        f"*⚔️ Battle Plan*",
        dir_line,
        f"",
        f"_Full narrative analysis requires Pro API key._",
    ])

    return "\n".join(lines)


def _format_price_only_report(ticker: str, price_data: dict) -> str:
    """Legacy fallback — no score data available"""
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
    Uses deterministic scoring from math — same data = same scores every time.
    """
    from datetime import datetime, timezone as tz

    # Fetch price data and calculate scores for all tickers
    stock_entries = []
    for ticker in tickers[:15]:  # max 15 stocks
        price_data = _fetch_comprehensive_data(ticker)
        ds = _calculate_deterministic_score(price_data)
        stock_entries.append({
            "ticker": ticker,
            "score": ds["score"],
            "signal": ds["signal"],
            "action": ds["action"],
        })

    # Categorize
    buy = [s for s in stock_entries if "Buy" in s["action"]]
    watch = [s for s in stock_entries if "Hold" in s["action"] or "Watch" in s["action"]]
    sell = [s for s in stock_entries if "Sell" in s["action"]]

    # Sort each by score descending
    buy.sort(key=lambda x: x["score"], reverse=True)
    watch.sort(key=lambda x: x["score"], reverse=True)
    sell.sort(key=lambda x: x["score"], reverse=True)

    date_str = datetime.now(tz(timedelta(hours=10))).strftime("%Y-%m-%d")

    lines = [
        f"📊 *Decision Dashboard* — {date_str}",
        f"",
        f"Analyzed {len(stock_entries)} stocks | Buy: {len(buy)} | Watch: {len(watch)} | Sell: {len(sell)}",
        f"",
    ]

    if buy:
        lines.append("*🟢 Buy*")
        for s in buy:
            lines.append(f"  • `{s['ticker']}` — Score {s['score']} | {s['signal']}")
        lines.append("")

    if watch:
        lines.append("*🟡 Watch*")
        for s in watch:
            lines.append(f"  • `{s['ticker']}` — Score {s['score']} | {s['signal']}")
        lines.append("")

    if sell:
        lines.append("*🔴 Sell*")
        for s in sell:
            lines.append(f"  • `{s['ticker']}` — Score {s['score']} | {s['signal']}")
        lines.append("")

    lines.append("_Use /analyze TICKER for full Battle Plan on any stock._")

    return "\n".join(lines)



# ===========================
# Application builder & startup
# ===========================

def build_application() -> Application:
    """Build and configure the Telegram Application"""
    token = _get_bot_token()
    app = Application.builder().token(token).job_queue(JobQueue()).build()

    # Register command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("email", cmd_email))
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
        BotCommand("email", "Set email for daily analysis delivery (Pro)"),
        BotCommand("dashboard", "Decision Dashboard for watchlist"),
    ]
    await app.bot.set_my_commands(commands)
    logger.info("Telegram command menu updated")


async def _email_delivery_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job queue callback: check and send daily emails to Pro users"""
    try:
        from src.bot.email_sender import send_daily_emails
        stats = send_daily_emails()
        if stats["sent"] > 0 or stats["failed"] > 0:
            logger.info("Email delivery job: sent=%d failed=%d skipped=%d", stats["sent"], stats["failed"], stats["skipped"])
    except Exception as e:
        logger.error("Email delivery job failed: %s", e)


def run_bot() -> None:
    """Start the subscription bot (blocking, suitable for standalone process)"""
    db.init_db()

    app = build_application()

    async def post_init(application: Application) -> None:
        await _set_bot_commands(application)
        # Schedule email delivery check every 30 minutes
        application.job_queue.run_repeating(
            _email_delivery_job,
            interval=1800,  # 30 minutes
            first=60,  # Start checking after 1 minute
        )

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