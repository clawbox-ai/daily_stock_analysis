# -*- coding: utf-8 -*-
"""
每日广播模块

职责：
1. 从数据库拉取各套餐用户
2. 按用户套餐发送对应的分析报告
   - Free：使用系统默认股票列表
   - Pro：使用用户自定义自选股
   - Elite：使用用户自定义自选股 + 大盘复盘
3. 保持向后兼容：若未启用订阅模式，沿用原有 TELEGRAM_CHAT_ID 单播行为

调用方式：
  from src.bot.broadcaster import broadcast_daily_report
  broadcast_daily_report(reports)

其中 reports 为 {ticker: report_text} 字典或 NotificationService 已生成的内容。
"""
import logging
import os
import time
from typing import Dict, List, Optional

import requests

from src.bot import db
from src.bot.tiers import (
    TIER_FREE,
    TIER_PRO,
    TIER_ELITE,
    can_receive_market_review,
    get_tier_config,
)

logger = logging.getLogger(__name__)

# 广播间隔（秒），避免触发 Telegram 频率限制
_BROADCAST_INTERVAL_SECONDS = float(os.environ.get("BROADCAST_INTERVAL_SECONDS", "0.3"))


# ===========================
# 核心广播函数
# ===========================

def broadcast_daily_report(
    stock_reports: Dict[str, str],
    market_review: Optional[str] = None,
    default_stock_list: Optional[List[str]] = None,
) -> dict:
    """
    向所有订阅用户推送每日报告。

    Args:
        stock_reports:       {ticker: report_text} 字典，已生成的个股报告
        market_review:       大盘复盘文本（Elite 套餐使用）
        default_stock_list:  系统默认股票列表（Free 套餐使用）；
                             若为 None，从 STOCK_LIST 环境变量读取

    Returns:
        {
            "sent": int,       成功发送数
            "failed": int,     发送失败数
            "skipped": int,    跳过（无报告可发）数
        }
    """
    token = os.environ.get("SUBSCRIPTION_BOT_TOKEN", "")
    if not token:
        logger.warning("SUBSCRIPTION_BOT_TOKEN 未配置，跳过订阅广播")
        return {"sent": 0, "failed": 0, "skipped": 0}

    if default_stock_list is None:
        raw = os.environ.get("STOCK_LIST", "600519,000001,300750")
        default_stock_list = [s.strip() for s in raw.split(",") if s.strip()]

    # 拉取所有用户（按套餐分批处理）
    all_users = db.get_all_users()
    stats = {"sent": 0, "failed": 0, "skipped": 0}

    for user in all_users:
        tid = user["telegram_id"]
        tier = user["tier"]

        try:
            content = _build_user_content(
                user=user,
                stock_reports=stock_reports,
                market_review=market_review,
                default_stock_list=default_stock_list,
            )
            if not content:
                stats["skipped"] += 1
                continue

            ok = _send_telegram_message(token=token, chat_id=tid, text=content)
            if ok:
                stats["sent"] += 1
            else:
                stats["failed"] += 1
        except Exception as e:
            logger.error("广播失败: telegram_id=%d tier=%s error=%s", tid, tier, e)
            stats["failed"] += 1

        # 避免 Telegram 限流
        time.sleep(_BROADCAST_INTERVAL_SECONDS)

    logger.info(
        "每日广播完成: sent=%d failed=%d skipped=%d",
        stats["sent"], stats["failed"], stats["skipped"],
    )
    return stats


def broadcast_legacy_fallback(content: str) -> bool:
    """
    向后兼容：若未启用订阅模式，使用原有 TELEGRAM_CHAT_ID 单播。
    仅当 SUBSCRIPTION_BOT_TOKEN 未配置时，此函数才会被调用。

    Returns:
        是否成功发送
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return False

    return _send_telegram_message(token=bot_token, chat_id=chat_id, text=content)


# ===========================
# 内容组装
# ===========================

def _build_user_content(
    user: dict,
    stock_reports: Dict[str, str],
    market_review: Optional[str],
    default_stock_list: List[str],
) -> Optional[str]:
    """
    根据用户套餐组装推送内容。

    Returns:
        推送文本；若无内容可推送，返回 None
    """
    tier = user["tier"]

    # 确定该用户需要接收哪些股票报告
    if tier == TIER_FREE:
        tickers = default_stock_list
    else:
        tickers = user.get("watchlist") or []
        if not tickers:
            # Pro/Elite 用户未设置自选股，降级为默认列表
            tickers = default_stock_list

    # 收集对应个股报告
    parts = []
    for ticker in tickers:
        report = stock_reports.get(ticker) or stock_reports.get(ticker.upper())
        if report:
            parts.append(report)

    # Elite 用户追加大盘复盘
    if can_receive_market_review(tier) and market_review:
        parts.append(market_review)

    if not parts:
        return None

    tier_cfg = get_tier_config(tier)
    header = f"📊 *每日分析报告* — {tier_cfg.label}\n\n"
    return header + "\n\n---\n\n".join(parts)


# ===========================
# Telegram 发送底层
# ===========================

def _send_telegram_message(token: str, chat_id, text: str) -> bool:
    """
    向指定 chat_id 发送 Telegram 消息（含分段和重试）。

    Telegram 单条消息限制 4096 字符，超长自动分段。
    """
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    max_length = 4096

    if len(text) <= max_length:
        return _post_telegram(api_url=api_url, chat_id=chat_id, text=text)

    # 超长分段发送
    sections = text.split("\n\n---\n\n")
    current_chunk: List[str] = []
    current_len = 0
    all_ok = True

    for section in sections:
        sec_len = len(section) + 7  # +7 for "\n\n---\n\n"
        if current_len + sec_len > max_length:
            if current_chunk:
                chunk_text = "\n\n---\n\n".join(current_chunk)
                if not _post_telegram(api_url=api_url, chat_id=chat_id, text=chunk_text):
                    all_ok = False
                time.sleep(_BROADCAST_INTERVAL_SECONDS)
            current_chunk = [section]
            current_len = sec_len
        else:
            current_chunk.append(section)
            current_len += sec_len

    if current_chunk:
        chunk_text = "\n\n---\n\n".join(current_chunk)
        if not _post_telegram(api_url=api_url, chat_id=chat_id, text=chunk_text):
            all_ok = False

    return all_ok


def _post_telegram(api_url: str, chat_id, text: str, retries: int = 3) -> bool:
    """单次 HTTP POST，含指数退避重试"""
    payload = {
        "chat_id": str(chat_id),
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(api_url, json=payload, timeout=10)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            logger.error("Telegram 请求异常（已重试 %d 次）: %s", retries, e)
            return False

        if resp.status_code == 200 and resp.json().get("ok"):
            return True

        if resp.status_code == 429:
            # 触发频率限制
            retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
            if attempt < retries:
                logger.warning("Telegram 频率限制，%ds 后重试（%d/%d）", retry_after, attempt, retries)
                time.sleep(retry_after)
                continue
            return False

        if resp.status_code >= 500 and attempt < retries:
            time.sleep(2 ** attempt)
            continue

        # Markdown 解析失败降级为纯文本
        error_desc = resp.json().get("description", "") if resp.headers.get("content-type", "").startswith("application/json") else ""
        if "parse" in error_desc.lower() or "markdown" in error_desc.lower():
            plain_payload = dict(payload)
            plain_payload.pop("parse_mode", None)
            try:
                r2 = requests.post(api_url, json=plain_payload, timeout=10)
                return r2.status_code == 200 and r2.json().get("ok", False)
            except Exception:
                pass

        logger.error("Telegram 发送失败: HTTP %d %s", resp.status_code, resp.text[:200])
        return False

    return False
