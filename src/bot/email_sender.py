# -*- coding: utf-8 -*-
"""
Email delivery engine for StockAnalyst Pro.

Sends full watchlist analysis to Pro users at their scheduled time.
Supports any SMTP provider (Gmail, Resend, SendGrid, etc.)
"""
import json
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone as tz
from typing import Optional

from src.bot import db
from src.bot.tiers import TIER_PRO

logger = logging.getLogger(__name__)


def _get_smtp_config() -> dict:
    """Load SMTP config from env or credentials file"""
    config = {
        "host": os.environ.get("SMTP_HOST", ""),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "pass": os.environ.get("SMTP_PASS", ""),
        "from_name": os.environ.get("SMTP_FROM_NAME", "StockAnalyst"),
        "from_email": os.environ.get("SMTP_FROM_EMAIL", ""),
    }

    # Try credentials file if env vars missing
    if not config["host"]:
        cwd = os.getcwd()
        key_paths = [
            os.path.join(cwd, "credentials", "smtp.secret.json"),
            "./credentials/smtp.secret.json",
        ]
        for path in key_paths:
            try:
                if os.path.exists(path):
                    with open(path) as f:
                        data = json.load(f)
                    config["host"] = data.get("host", "")
                    config["port"] = int(data.get("port", 587))
                    config["user"] = data.get("user", "")
                    config["pass_"] = data.get("pass", data.get("password", ""))
                    config["from_name"] = data.get("from_name", "StockAnalyst")
                    config["from_email"] = data.get("from_email", "")
                    break
            except Exception:
                continue

    # Rename pass_ back to pass for consistency
    if "pass_" in config and not config["pass"]:
        config["pass"] = config.pop("pass_")
    elif "pass_" in config:
        config.pop("pass_")

    return config


def send_email(to_email: str, subject: str, html_body: str, text_body: str = "") -> bool:
    """Send an email via SMTP"""
    config = _get_smtp_config()

    if not config["host"] or not config["user"] or not config["from_email"]:
        logger.error("SMTP not configured. Set SMTP_HOST, SMTP_USER, SMTP_FROM_EMAIL env vars or credentials/smtp.secret.json")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{config['from_name']} <{config['from_email']}>"
        msg["To"] = to_email

        if text_body:
            msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(config["host"], config["port"]) as server:
            server.starttls()
            server.login(config["user"], config["pass"])
            server.sendmail(config["from_email"], to_email, msg.as_string())

        logger.info("Email sent to %s", to_email)
        return True

    except Exception as e:
        logger.error("Email send failed to %s: %s", to_email, e)
        return False


def generate_watchlist_email(tickers: list[str], username: str) -> tuple[str, str]:
    """
    Generate HTML email content for a user's watchlist analysis.
    Returns (subject, html_body).
    """
    from src.bot.subscription_bot import _fetch_comprehensive_data, _calculate_deterministic_score

    date_str = datetime.now(tz(tz.utc)).strftime("%Y-%m-%d")

    # Analyze all tickers
    entries = []
    for ticker in tickers:
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

    # Categorize
    buy = [e for e in entries if "Buy" in e["action"]]
    watch = [e for e in entries if "Hold" in e["action"] or "Watch" in e["action"]]
    sell = [e for e in entries if "Sell" in e["action"]]

    # Build HTML
    subject = f"📊 StockAnalyst Daily — {date_str}"

    html = f"""<!DOCTYPE html>
<html>
<head>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f0f0f; color: #e0e0e0; margin: 0; padding: 20px; }}
.container {{ max-width: 600px; margin: 0 auto; }}
.header {{ text-align: center; padding: 20px; border-bottom: 2px solid #333; }}
.header h1 {{ color: #00ff88; margin: 0; }}
.summary {{ background: #1a1a1a; border-radius: 8px; padding: 15px; margin: 15px 0; }}
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
<p>Buy: {len(buy)} | Watch: {len(watch)} | Sell: {len(sell)}</p>
</div>
"""

    if buy:
        html += '<div class="section buy"><h2>🟢 Buy</h2>'
        for e in buy:
            score_class = "score-high" if e["score"] >= 70 else "score-mid"
            html += f'<div class="stock-row"><span class="stock-ticker">{e["ticker"]}</span><span class="score {score_class}">{e["score"]} — {e["signal"]}</span></div>'
        html += '</div>'

    if watch:
        html += '<div class="section watch"><h2>🟡 Watch</h2>'
        for e in watch:
            score_class = "score-mid"
            html += f'<div class="stock-row"><span class="stock-ticker">{e["ticker"]}</span><span class="score {score_class}">{e["score"]} — {e["signal"]}</span></div>'
        html += '</div>'

    if sell:
        html += '<div class="section sell"><h2>🔴 Sell</h2>'
        for e in sell:
            score_class = "score-low"
            html += f'<div class="stock-row"><span class="stock-ticker">{e["ticker"]}</span><span class="score {score_class}">{e["score"]} — {e["signal"]}</span></div>'
        html += '</div>'

    # Add price details for each stock
    html += '<div class="section"><h2>📈 Price Details</h2>'
    for e in entries:
        pd = e["price_data"]
        price = pd.get("Price", "N/A")
        change = pd.get("Change", "")
        rsi = pd.get("RSI (14)", "")
        macd = pd.get("MACD Signal", "")
        html += f'<div class="stock-row"><span><strong>{e["ticker"]}</strong> — {price} {change}</span><span>RSI: {rsi} | MACD: {macd}</span></div>'
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

    # Plain text fallback
    text = f"StockAnalyst Daily — {date_str}\n\n"
    text += f"Analyzed {len(entries)} stocks | Buy: {len(buy)} | Watch: {len(watch)} | Sell: {len(sell)}\n\n"
    for e in entries:
        text += f"{e['ticker']}: Score {e['score']} | {e['signal']} | {e['action']}\n"
    text += "\nUse /analyze TICKER for full Battle Plan.\n@claw_analyst_bot"

    return subject, html


def send_daily_emails() -> dict:
    """
    Send daily email to all Pro users who have email configured.
    Checks each user's delivery_time against current UTC time.
    Returns stats: {sent, failed, skipped}
    """
    stats = {"sent": 0, "failed": 0, "skipped": 0}

    # Get all Pro users
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
                current_time = now_local.strftime("%H:%M")
                # Check within 30 min window
                current_min = int(current_time.split(":")[0]) * 60 + int(current_time.split(":")[1])
                delivery_min = int(delivery_time.split(":")[0]) * 60 + int(delivery_time.split(":")[1])
                if abs(current_min - delivery_min) > 30:
                    stats["skipped"] += 1
                    continue
            except Exception:
                pass  # If timezone fails, just send anyway

        # Get watchlist
        watchlist = user.get("watchlist", [])
        if not watchlist:
            stats["skipped"] += 1
            continue

        # Generate and send email
        try:
            subject, html = generate_watchlist_email(watchlist, user.get("username", "Trader"))
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