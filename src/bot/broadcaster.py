# -*- coding: utf-8 -*-
"""
Daily broadcast module

Responsibilities:
1. Pull all subscribed users from database
2. Send analysis reports based on user tier
   - Free: system default stock list
   - Pro: user's custom watchlist + market review
3. Backward compatible: falls back to TELEGRAM_CHAT_ID single-channel broadcast

Usage:
  from src.bot.broadcaster import broadcast_daily_report
  broadcast_daily_report(reports)
"""
import json
import logging
import os
import time
from typing import Dict, List, Optional

import requests

from src.bot import db
from src.bot.tiers import (
    TIER_FREE,
    TIER_PRO,
    can_receive_market_review,
    get_tier_config,
)

logger = logging.getLogger(__name__)

# Broadcast interval (seconds) to avoid Telegram rate limits
_BROADCAST_INTERVAL_SECONDS = float(os.environ.get("BROADCAST_INTERVAL_SECONDS", "0.3"))


def _get_bot_token() -> str:
    """Load bot token from env or secret file"""
    token = os.environ.get("SUBSCRIPTION_BOT_TOKEN", "")
    if token:
        return token
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
    return ""


# ===========================
# Core broadcast function
# ===========================

def broadcast_daily_report(
    stock_reports: Dict[str, str],
    market_review: Optional[str] = None,
    default_stock_list: Optional[List[str]] = None,
) -> dict:
    """
    Push daily report to all subscribed users.

    Args:
        stock_reports:       {ticker: report_text} dict
        market_review:      market review text (Pro tier)
        default_stock_list: system default stock list (Free tier);
                            if None, reads from STOCK_LIST env var

    Returns:
        {"sent": int, "failed": int, "skipped": int}
    """
    token = _get_bot_token()
    if not token:
        logger.warning("SUBSCRIPTION_BOT_TOKEN not configured, skipping broadcast")
        return {"sent": 0, "failed": 0, "skipped": 0}

    if default_stock_list is None:
        raw = os.environ.get("STOCK_LIST", "BTC-USD,ETH-USD,AAPL")
        default_stock_list = [s.strip() for s in raw.split(",") if s.strip()]

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
            logger.error("Broadcast failed: telegram_id=%d tier=%s error=%s", tid, tier, e)
            stats["failed"] += 1

        # Avoid Telegram rate limiting
        time.sleep(_BROADCAST_INTERVAL_SECONDS)

    logger.info(
        "Daily broadcast complete: sent=%d failed=%d skipped=%d",
        stats["sent"], stats["failed"], stats["skipped"],
    )
    return stats


def broadcast_legacy_fallback(content: str) -> bool:
    """
    Backward compatible: if subscription mode not enabled, use TELEGRAM_CHAT_ID single broadcast.
    Only called when SUBSCRIPTION_BOT_TOKEN is not configured.
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return False

    return _send_telegram_message(token=bot_token, chat_id=chat_id, text=content)


# ===========================
# Content assembly
# ===========================

def _build_user_content(
    user: dict,
    stock_reports: Dict[str, str],
    market_review: Optional[str],
    default_stock_list: List[str],
) -> Optional[str]:
    """
    Assemble push content based on user tier.

    Returns:
        Push text; None if nothing to send
    """
    tier = user["tier"]

    if tier == TIER_FREE:
        tickers = default_stock_list
    else:
        tickers = user.get("watchlist") or []
        if not tickers:
            # Pro user hasn't set watchlist, fall back to defaults
            tickers = default_stock_list

    # Collect corresponding stock reports
    parts = []
    for ticker in tickers:
        report = stock_reports.get(ticker) or stock_reports.get(ticker.upper())
        if report:
            parts.append(report)

    # Pro users get market review
    if can_receive_market_review(tier) and market_review:
        parts.append(market_review)

    if not parts:
        return None

    tier_cfg = get_tier_config(tier)
    header = f"📊 *Daily Analysis Report* — {tier_cfg.label}\n\n"
    return header + "\n\n---\n\n".join(parts)


# ===========================
# Telegram send layer
# ===========================

def _send_telegram_message(token: str, chat_id, text: str) -> bool:
    """
    Send Telegram message to chat_id (with chunking and retry).

    Telegram single message limit is 4096 chars; auto-chunks if longer.
    """
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    max_length = 4096

    if len(text) <= max_length:
        return _post_telegram(api_url=api_url, chat_id=chat_id, text=text)

    # Chunk long messages
    sections = text.split("\n\n---\n\n")
    current_chunk: List[str] = []
    current_len = 0
    all_ok = True

    for section in sections:
        sec_len = len(section) + 7
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
    """Single HTTP POST with exponential backoff retry"""
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
            logger.error("Telegram request failed (retried %d times): %s", retries, e)
            return False

        if resp.status_code == 200 and resp.json().get("ok"):
            return True

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
            if attempt < retries:
                logger.warning("Telegram rate limited, retrying in %ds (%d/%d)", retry_after, attempt, retries)
                time.sleep(retry_after)
                continue
            return False

        if resp.status_code >= 500 and attempt < retries:
            time.sleep(2 ** attempt)
            continue

        # Markdown parse failure — fall back to plain text
        error_desc = ""
        try:
            error_desc = resp.json().get("description", "")
        except Exception:
            pass
        if "parse" in error_desc.lower() or "markdown" in error_desc.lower():
            plain_payload = dict(payload)
            plain_payload.pop("parse_mode", None)
            try:
                r2 = requests.post(api_url, json=plain_payload, timeout=10)
                return r2.status_code == 200 and r2.json().get("ok", False)
            except Exception:
                pass

        logger.error("Telegram send failed: HTTP %d %s", resp.status_code, resp.text[:200])
        return False

    return False