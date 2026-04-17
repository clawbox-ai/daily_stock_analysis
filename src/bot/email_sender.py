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
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; color: #222; margin: 0; padding: 20px; }}
.container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; }}
.header {{ background: #1a1a2e; padding: 24px 20px; text-align: center; }}
.header h1 {{ color: #ffffff; margin: 0; font-size: 24px; }}
.header p {{ color: #aab; margin: 6px 0 0; font-size: 14px; }}
.summary {{ display: flex; justify-content: center; gap: 20px; padding: 16px 20px; background: #f8f9fa; border-bottom: 1px solid #eee; }}
.summary-item {{ text-align: center; }}
.summary-item .count {{ font-size: 24px; font-weight: bold; }}
.summary-item .label {{ font-size: 12px; color: #666; }}
.section {{ padding: 0 20px; margin: 0; }}
.section-title {{ padding: 16px 0 8px; font-size: 18px; margin: 0; }}
.stock-card {{ background: #f8f9fa; border-radius: 8px; padding: 14px 16px; margin: 8px 0; border-left: 4px solid #ccc; }}
.stock-card.buy {{ border-left-color: #00c853; }}
.stock-card.watch {{ border-left-color: #ff9800; }}
.stock-card.sell {{ border-left-color: #f44336; }}
.ticker {{ font-size: 20px; font-weight: 800; color: #111; font-family: 'SF Mono', 'Consolas', monospace; letter-spacing: 0.5px; }}
.signal {{ font-size: 14px; font-weight: 600; margin-left: 8px; }}
.signal-buy {{ color: #00c853; }}
.signal-watch {{ color: #ff9800; }}
.signal-sell {{ color: #f44336; }}
.score-badge {{ display: inline-block; background: #e8f5e9; color: #2e7d32; border-radius: 12px; padding: 2px 10px; font-weight: 700; font-size: 13px; }}
.score-badge.mid {{ background: #fff3e0; color: #e65100; }}
.score-badge.low {{ background: #ffebee; color: #c62828; }}
.details {{ font-size: 13px; color: #555; margin-top: 4px; }}
.footer {{ text-align: center; padding: 20px; color: #999; font-size: 12px; border-top: 1px solid #eee; }}
.footer a {{ color: #1a1a2e; }}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>📊 StockAnalyst Daily</h1>
<p>{date_str} — {len(entries)} stocks analyzed</p>
</div>
<div class="summary">
<div class="summary-item"><div class="count" style="color:#00c853">{len(buy)}</div><div class="label">Buy</div></div>
<div class="summary-item"><div class="count" style="color:#ff9800">{len(watch)}</div><div class="label">Watch</div></div>
<div class="summary-item"><div class="count" style="color:#f44336">{len(sell)}</div><div class="label">Sell</div></div>
</div>
"""

    if buy:
        html += '<div class="section"><h2 class="section-title" style="color:#00c853">🟢 Buy</h2>'
        for e in buy:
            pd = e["price_data"]
            price = pd.get("Price", "N/A")
            change = pd.get("Change", "")
            score_class = "" if e["score"] >= 70 else "mid"
            html += f'''
<div class="stock-card buy">
  <span class="ticker">{e["ticker"]}</span>
  <span class="signal signal-buy">{e["signal"]}</span>
  <span class="score-badge {score_class}">{e["score"]}</span>
  <div class="details">{price} {change} &bull; RSI: {pd.get("RSI (14)","—")} &bull; MACD: {pd.get("MACD Signal","—")}</div>
</div>'''
        html += '</div>'

    if watch:
        html += '<div class="section"><h2 class="section-title" style="color:#ff9800">🟡 Watch</h2>'
        for e in watch:
            pd = e["price_data"]
            price = pd.get("Price", "N/A")
            change = pd.get("Change", "")
            html += f'''
<div class="stock-card watch">
  <span class="ticker">{e["ticker"]}</span>
  <span class="signal signal-watch">{e["signal"]}</span>
  <span class="score-badge mid">{e["score"]}</span>
  <div class="details">{price} {change} &bull; RSI: {pd.get("RSI (14)","—")} &bull; MACD: {pd.get("MACD Signal","—")}</div>
</div>'''
        html += '</div>'

    if sell:
        html += '<div class="section"><h2 class="section-title" style="color:#f44336">🔴 Sell</h2>'
        for e in sell:
            pd = e["price_data"]
            price = pd.get("Price", "N/A")
            change = pd.get("Change", "")
            html += f'''
<div class="stock-card sell">
  <span class="ticker">{e["ticker"]}</span>
  <span class="signal signal-sell">{e["signal"]}</span>
  <span class="score-badge low">{e["score"]}</span>
  <div class="details">{price} {change} &bull; RSI: {pd.get("RSI (14)","—")} &bull; MACD: {pd.get("MACD Signal","—")}</div>
</div>'''
        html += '</div>'

    html += f"""
<div style="background:#f5f5f5;padding:20px 20px 16px;text-align:center;border-top:1px solid #ddd">
<p style="margin:0 0 8px;color:#1a1a2e;font-weight:700;font-size:13px">StockAnalyst Bot &mdash; <a href="https://t.me/claw_analyst_bot" style="color:#1a1a2e">@claw_analyst_bot</a></p>
<p style="margin:0 0 8px;font-size:12px;color:#999">Use /analyze TICKER in the bot for full Battle Plan analysis</p>
<p style="margin:0 0 4px;font-size:11px;color:#bbb;">Unsubscribe: /email off in the bot</p>
</div>
<div style="background:#eee;padding:12px 20px;text-align:center">
<p style="margin:0;font-size:10px;color:#999;">⚠️ Not financial advice. All analysis is algorithmic and for informational purposes only. Past performance does not guarantee future results. Always do your own research before making investment decisions. StockAnalyst is not a registered financial advisor.</p>
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