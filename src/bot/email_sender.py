# -*- coding: utf-8 -*-
"""
Email delivery engine for StockAnalyst Pro.

Sends full watchlist analysis to Pro users at their scheduled time.
Uses Resend API (100 emails/day free).
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests

from src.bot import db
from src.bot.tiers import TIER_PRO

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
FROM_EMAIL = "StockAnalyst <stockanalyst@clawboxai.org>"


def _get_resend_key() -> Optional[str]:
    """Get Resend API key from env or credentials file"""
    key = os.environ.get("RESEND_API_KEY", "")
    if key:
        return key

    cwd = os.getcwd()
    key_paths = [
        os.path.join(cwd, "credentials", "resend.secret.json"),
        "./credentials/resend.secret.json",
    ]
    for path in key_paths:
        try:
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                return data.get("api_key", "")
        except Exception:
            continue
    return None


def send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Send an email via Resend API"""
    api_key = _get_resend_key()
    if not api_key:
        logger.error("Resend API key not configured. Set RESEND_API_KEY env var or credentials/resend.secret.json")
        return False

    try:
        resp = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": FROM_EMAIL,
                "to": [to_email],
                "subject": subject,
                "html": html_body,
            },
            timeout=30,
        )

        if resp.status_code == 200:
            logger.info("Email sent to %s (id=%s)", to_email, resp.json().get("id", "?"))
            return True
        else:
            logger.error("Resend API error %d: %s", resp.status_code, resp.text)
            return False

    except Exception as e:
        logger.error("Email send failed to %s: %s", to_email, e)
        return False


def generate_watchlist_email(tickers: list[str], username: str) -> tuple[str, str]:
    """
    Generate HTML email content for a user's watchlist analysis.
    Returns (subject, html_body).
    """
    from src.bot.subscription_bot import _fetch_comprehensive_data, _calculate_deterministic_score

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Analyze all tickers
    entries = []
    for ticker in tickers:
        try:
            price_data = _fetch_comprehensive_data(ticker)
            ds = _calculate_deterministic_score(price_data)
            entries.append({
                "ticker": ticker,
                "score": ds["score"],
                "signal": ds["signal"],
                "action": ds["action"],
                "direction": ds["direction"],
                "price_data": price_data,
            })
        except Exception as e:
            logger.warning("Failed to analyze %s: %s", ticker, e)
            entries.append({
                "ticker": ticker,
                "score": 50,
                "signal": "Sideways",
                "action": "Hold",
                "direction": "NEUTRAL",
                "price_data": {},
            })

    # Categorize
    buy = [e for e in entries if "Buy" in e["action"]]
    watch = [e for e in entries if "Hold" in e["action"] or "Watch" in e["action"]]
    sell = [e for e in entries if "Sell" in e["action"]]

    subject = f"📊 StockAnalyst Daily — {date_str}"

    html = f"""<!DOCTYPE html>
<html>
<head>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f0f0f; color: #e0e0e0; margin: 0; padding: 20px; }}
.container {{ max-width: 600px; margin: 0 auto; }}
.header {{ text-align: center; padding: 20px; border-bottom: 2px solid #333; }}
.header h1 {{ color: #00ff88; margin: 0; }}
.summary {{ background: #1a1a1a; border-radius: 8px; padding: 15px; margin: 15px 0; text-align: center; }}
.section {{ background: #1a1a1a; border-radius: 8px; padding: 15px; margin: 10px 0; }}
.section h2 {{ margin-top: 0; }}
.buy {{ border-left: 3px solid #00ff88; }}
.watch {{ border-left: 3px solid #ffcc00; }}
.sell {{ border-left: 3px solid #ff4444; }}
.stock-row {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #2a2a2a; }}
.stock-ticker {{ font-weight: bold; font-family: monospace; }}
.score {{ font-weight: bold; }}
.score-high {{ color: #00ff88; }}
.score-mid {{ color: #ffcc00; }}
.score-low {{ color: #ff4444; }}
.footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; }}
a {{ color: #00ff88; }}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>📊 StockAnalyst Daily</h1>
<p>{date_str} — Analyzed {len(entries)} stocks</p>
</div>
<div class="summary">
<strong>🟢 Buy: {len(buy)}</strong> &nbsp;|&nbsp; <strong>🟡 Watch: {len(watch)}</strong> &nbsp;|&nbsp; <strong>🔴 Sell: {len(sell)}</strong>
</div>
"""

    if buy:
        html += '<div class="section buy"><h2>🟢 Buy</h2>'
        for e in buy:
            sc = "score-high" if e["score"] >= 70 else "score-mid"
            pd = e["price_data"]
            price = pd.get("Price", "N/A")
            change = pd.get("Change", "")
            html += f'<div class="stock-row"><span class="stock-ticker">{e["ticker"]}</span><span class="score {sc}">{e["score"]} — {e["signal"]}</span></div>'
            html += f'<div class="stock-row" style="font-size:13px;color:#aaa"><span>{price} {change}</span><span>RSI: {pd.get("RSI (14)","—")} | MACD: {pd.get("MACD Signal","—")}</span></div>'
        html += '</div>'

    if watch:
        html += '<div class="section watch"><h2>🟡 Watch</h2>'
        for e in watch:
            pd = e["price_data"]
            price = pd.get("Price", "N/A")
            change = pd.get("Change", "")
            html += f'<div class="stock-row"><span class="stock-ticker">{e["ticker"]}</span><span class="score score-mid">{e["score"]} — {e["signal"]}</span></div>'
            html += f'<div class="stock-row" style="font-size:13px;color:#aaa"><span>{price} {change}</span><span>RSI: {pd.get("RSI (14)","—")} | MACD: {pd.get("MACD Signal","—")}</span></div>'
        html += '</div>'

    if sell:
        html += '<div class="section sell"><h2>🔴 Sell</h2>'
        for e in sell:
            pd = e["price_data"]
            price = pd.get("Price", "N/A")
            change = pd.get("Change", "")
            html += f'<div class="stock-row"><span class="stock-ticker">{e["ticker"]}</span><span class="score score-low">{e["score"]} — {e["signal"]}</span></div>'
            html += f'<div class="stock-row" style="font-size:13px;color:#aaa"><span>{price} {change}</span><span>RSI: {pd.get("RSI (14)","—")} | MACD: {pd.get("MACD Signal","—")}</span></div>'
        html += '</div>'

    html += f"""
<div class="footer">
<p>StockAnalyst Bot — <a href="https://t.me/claw_analyst_bot">@claw_analyst_bot</a></p>
<p>Use /analyze TICKER in the bot for full Battle Plan analysis</p>
<p>Unsubscribe: /email off in the bot</p>
</div>
</div>
</body>
</html>"""

    return subject, html


def send_daily_emails() -> dict:
    """
    Send daily email to all Pro users who have email configured.
    Checks each user's delivery_time against their local time.
    Returns stats: {sent, failed, skipped}
    """
    stats = {"sent": 0, "failed": 0, "skipped": 0}

    pro_users = db.get_users_by_tier(TIER_PRO)

    for user in pro_users:
        email = user.get("email")
        if not email:
            stats["skipped"] += 1
            continue

        # Check if it's their delivery time (within 30 min window)
        timezone_str = user.get("timezone")
        delivery_time = user.get("delivery_time", "08:00")

        if timezone_str:
            try:
                import zoneinfo
                tz_obj = zoneinfo.ZoneInfo(timezone_str)
                now_local = datetime.now(tz_obj)
                current_hm = now_local.strftime("%H:%M")
                current_min = int(current_hm.split(":")[0]) * 60 + int(current_hm.split(":")[1])
                delivery_min = int(delivery_time.split(":")[0]) * 60 + int(delivery_time.split(":")[1])
                if abs(current_min - delivery_min) > 30:
                    stats["skipped"] += 1
                    continue
            except Exception:
                pass

        # Get watchlist
        watchlist = user.get("watchlist", [])
        if not watchlist:
            stats["skipped"] += 1
            continue

        # Generate and send
        try:
            subject, html = generate_watchlist_email(
                watchlist, user.get("username", "Trader")
            )
            ok = send_email(email, subject, html)
            if ok:
                stats["sent"] += 1
            else:
                stats["failed"] += 1
        except Exception as e:
            logger.error("Email delivery failed for user %d: %s", user["telegram_id"], e)
            stats["failed"] += 1

    logger.info("Daily email batch: sent=%d failed=%d skipped=%d", stats["sent"], stats["failed"], stats["skipped"])
    return stats