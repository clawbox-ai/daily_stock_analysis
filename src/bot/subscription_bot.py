# -*- coding: utf-8 -*-
"""
订阅制 Telegram 机器人

职责：
1. 响应用户指令：/start /subscribe /help /watchlist /analyze
2. 注册用户、展示套餐、管理自选股
3. 将 /analyze 请求路由到分析流水线（Pro/Elite 专属）

依赖：
  - python-telegram-bot >= 20.0（异步版）
  - src.bot.db      用户数据库
  - src.bot.tiers   套餐定义
  - src.config      系统配置（读取 SUBSCRIPTION_BOT_TOKEN）

启动方式（独立进程）：
  python -m src.bot.subscription_bot

或在 main.py 中通过 --bot 参数集成启动。
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

# ===========================
# 环境变量读取
# ===========================

def _get_bot_token() -> str:
    token = os.environ.get("SUBSCRIPTION_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("SUBSCRIPTION_BOT_TOKEN 未配置，无法启动订阅机器人")
    return token


def _get_default_stock_list() -> list[str]:
    """读取系统默认股票列表（免费用户使用）"""
    raw = os.environ.get("STOCK_LIST", "600519,000001,300750")
    return [s.strip() for s in raw.split(",") if s.strip()]


# ===========================
# 指令处理器
# ===========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — 注册用户并展示欢迎信息"""
    user = update.effective_user
    if user is None:
        return

    # 注册（幂等操作，已存在则直接返回）
    db_user = db.create_user(telegram_id=user.id, username=user.username)
    tier_cfg = get_tier_config(db_user["tier"])

    welcome = (
        f"👋 欢迎使用股票智能分析机器人，{user.first_name}！\n\n"
        f"当前套餐：*{tier_cfg.label}*\n"
        f"{tier_cfg.description}\n\n"
        "可用指令：\n"
        "  /help       — 查看全部指令\n"
        "  /subscribe  — 查看套餐选项\n"
        "  /watchlist  — 查看自选股列表\n"
        "  /analyze `<代码>` — 按需分析（Pro/Elite）"
    )
    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — 展示可用指令列表"""
    help_text = (
        "📖 *指令说明*\n\n"
        "/start      — 注册账号，查看当前套餐\n"
        "/subscribe  — 查看套餐详情及升级方式\n"
        "/watchlist  — 查看/管理自选股列表\n"
        "/analyze `<代码>` — 对指定股票发起按需分析\n"
        "              示例：`/analyze 600519`\n"
        "              _仅 Pro / Elite 套餐可用_\n\n"
        "每日分析报告将在交易日收盘后自动推送。"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/subscribe — 展示套餐选项"""
    user = update.effective_user
    if user is None:
        return

    # 确保用户已注册
    db_user = db.get_user(user.id)
    if not db_user:
        db_user = db.create_user(telegram_id=user.id, username=user.username)

    current = get_tier_config(db_user["tier"])
    menu = format_tier_menu()
    msg = f"当前套餐：*{current.label}*\n\n{menu}"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/watchlist — 展示用户自选股；Free 用户显示系统默认列表"""
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
        note = "_免费套餐使用系统默认股票列表，升级 Pro 可自定义。_"
    else:
        stocks = db_user.get("watchlist") or []
        if not stocks:
            note = "_自选股列表为空。请联系管理员或通过 API 添加股票。_"
        else:
            limit = watchlist_limit(tier)
            cap = f"{len(stocks)}/{limit}" if limit else f"{len(stocks)}/无限"
            note = f"_自选股数量：{cap}_"

    if stocks:
        stock_lines = "\n".join(f"  • `{s}`" for s in stocks)
        msg = f"📋 *自选股列表*\n{stock_lines}\n\n{note}"
    else:
        msg = f"📋 *自选股列表*\n（暂无）\n\n{note}"

    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/analyze <ticker> — 发起按需分析（Pro/Elite 专属）"""
    user = update.effective_user
    if user is None:
        return

    db_user = db.get_user(user.id)
    if not db_user:
        db_user = db.create_user(telegram_id=user.id, username=user.username)

    tier = db_user["tier"]

    # 权限校验
    if not can_use_on_demand(tier):
        tier_cfg = get_tier_config(tier)
        upgrade_msg = (
            f"⚠️ 按需分析是 *Pro / Elite* 专属功能。\n\n"
            f"当前套餐：{tier_cfg.label}\n\n"
            "使用 /subscribe 查看升级选项。"
        )
        await update.message.reply_text(upgrade_msg, parse_mode=ParseMode.MARKDOWN)
        return

    # 解析股票代码参数
    args = context.args
    if not args:
        await update.message.reply_text(
            "请提供股票代码，例如：`/analyze 600519`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    ticker = args[0].strip().upper()
    if not ticker:
        await update.message.reply_text("股票代码不能为空。", parse_mode=ParseMode.MARKDOWN)
        return

    # 提示正在分析
    await update.message.reply_text(
        f"🔍 正在分析 `{ticker}`，请稍候……",
        parse_mode=ParseMode.MARKDOWN,
    )

    # 调用分析流水线（在后台线程执行，避免阻塞事件循环）
    try:
        result_text = await asyncio.get_event_loop().run_in_executor(
            None, _run_single_stock_analysis, ticker, user.id
        )
        await update.message.reply_text(result_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error("按需分析失败: ticker=%s user=%d error=%s", ticker, user.id, e)
        await update.message.reply_text(
            f"❌ 分析 `{ticker}` 时出错，请稍后重试。",
            parse_mode=ParseMode.MARKDOWN,
        )


# ===========================
# 分析任务执行（同步）
# ===========================

def _run_single_stock_analysis(ticker: str, telegram_id: int) -> str:
    """
    同步调用分析流水线，返回格式化报告文本。
    运行于线程池，不阻塞 asyncio 事件循环。
    """
    try:
        from src.core.pipeline import StockAnalysisPipeline
        pipeline = StockAnalysisPipeline()
        results = pipeline.run(stocks=[ticker], notify=False)

        if not results:
            return f"未获取到 `{ticker}` 的分析结果，请确认代码是否正确。"

        # 取第一条结果格式化输出
        result = results[0] if isinstance(results, list) else results
        if hasattr(result, "to_markdown"):
            return result.to_markdown()
        return str(result)
    except Exception as e:
        logger.error("分析流水线执行失败: %s", e)
        raise


# ===========================
# 应用构建与启动
# ===========================

def build_application() -> Application:
    """构建并配置 Telegram Application 实例"""
    token = _get_bot_token()
    app = Application.builder().token(token).build()

    # 注册指令处理器
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("analyze", cmd_analyze))

    return app


async def _set_bot_commands(app: Application) -> None:
    """向 Telegram 注册指令菜单（可选，提升用户体验）"""
    commands = [
        BotCommand("start", "注册账号，查看当前套餐"),
        BotCommand("help", "查看全部指令说明"),
        BotCommand("subscribe", "查看套餐选项"),
        BotCommand("watchlist", "查看自选股列表"),
        BotCommand("analyze", "按需分析指定股票（Pro/Elite）"),
    ]
    await app.bot.set_my_commands(commands)
    logger.info("Telegram 指令菜单已更新")


def run_bot() -> None:
    """启动订阅机器人（阻塞运行，适合独立进程）"""
    # 初始化数据库
    db.init_db()

    app = build_application()

    # 在 post_init 中注册指令菜单
    async def post_init(application: Application) -> None:
        await _set_bot_commands(application)

    app.post_init = post_init

    logger.info("订阅机器人启动，开始 polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


# ===========================
# 直接运行入口
# ===========================

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
