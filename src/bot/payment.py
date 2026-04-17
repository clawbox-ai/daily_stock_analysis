# -*- coding: utf-8 -*-
"""
Payment module for StockAnalyst Pro subscriptions.

Supports:
- TON (Toncoin) payments
- USDT on TRON (TRC-20) payments

Each user gets a unique memo (their Telegram ID) to identify payments.
"""
import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from src.bot import db
from src.bot.tiers import TIER_PRO, PRO_PRICE_USD, PRO_DURATION_DAYS

logger = logging.getLogger(__name__)

# ===========================
# Wallet Configuration
# ===========================

# TON wallet - needs to be set up
TON_WALLET = os.environ.get("TON_WALLET", "")

# TRON USDT wallet - already have this one
TRON_USDT_WALLET = os.environ.get("TRON_USDT_WALLET", "TKqdJwQCzGVNdTLZZusrXUtcVqT8zPZ2pU")

# USDT contract on TRON (TRC-20)
TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

# Payment amounts (in smallest units)
TON_AMOUNT = 10  # ~$9 worth of TON at current prices, rounded up for simplicity
TRON_USDT_AMOUNT = 9  # $9 USDT

# TON API
TON_API = "https://toncenter.com/api/v2"
TON_API_KEY = os.environ.get("TON_API_KEY", "")

# TronGrid API
TRONGRID_API = "https://api.trongrid.io"
TRONGRID_API_KEY = os.environ.get("TRONGRID_API_KEY", "")


def get_payment_info(telegram_id: int) -> dict:
    """Get payment info for a user: addresses, amounts, memo"""
    memo = _generate_memo(telegram_id)
    return {
        "ton_wallet": TON_WALLET,
        "ton_amount": TON_AMOUNT,
        "tron_wallet": TRON_USDT_WALLET,
        "tron_usdt_amount": TRON_USDT_AMOUNT,
        "memo": memo,
        "tron_memo": memo,  # TRON memo = same
        "currency_usd": PRO_PRICE_USD,
        "duration_days": PRO_DURATION_DAYS,
    }


def _generate_memo(telegram_id: int) -> str:
    """Generate a unique memo for payment identification.
    Uses a short hash of the telegram ID so it's not immediately obvious.
    Format: SA-{4chars}"""
    h = hashlib.sha256(f"stockanalyst-{telegram_id}".encode()).hexdigest()[:8].upper()
    return f"SA-{h}"


def check_tron_payment(telegram_id: int) -> Optional[dict]:
    """Check if user has sent USDT to our TRON wallet.
    Returns payment dict if found, None otherwise."""
    memo = _generate_memo(telegram_id)
    headers = {}
    if TRONGRID_API_KEY:
        headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY

    try:
        # Check recent TRC-20 USDT transfers to our wallet
        url = f"{TRONGRID_API}/v1/accounts/{TRON_USDT_WALLET}/transactions/trc20"
        params = {
            "limit": 50,
            "contract_address": TRON_USDT_CONTRACT,
        }
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            logger.warning("TronGrid API error: %d", resp.status_code)
            return None

        data = resp.json()
        transfers = data.get("data", [])

        for tx in transfers:
            # Check if memo matches or if we use value + recent timestamp
            tx_memo = tx.get("memo", "")
            tx_value_str = tx.get("value", "0")
            tx_from = tx.get("from", "")
            tx_ts = tx.get("block_timestamp", 0)

            # USDT has 6 decimals
            try:
                tx_value = int(tx_value_str) / 1_000_000
            except (ValueError, TypeError):
                continue

            # Check if amount matches (within tolerance)
            if tx_value >= TRON_USDT_AMOUNT * 0.95:  # Allow 5% tolerance
                # Check if this is a recent transaction (within last 24 hours)
                tx_time = datetime.fromtimestamp(tx_ts / 1000, tz=timezone.utc)
                now = datetime.now(timezone.utc)
                hours_ago = (now - tx_time).total_seconds() / 3600

                if hours_ago <= 24:
                    # Check memo or if we have a pending payment record
                    pending = db.get_pending_payment(telegram_id, "tron_usdt")
                    if pending:
                        # Verify the transaction
                        if tx_from.lower() == pending.get("sender_address", "").lower() or tx_memo == memo:
                            return {
                                "type": "tron_usdt",
                                "amount": tx_value,
                                "tx_hash": tx.get("transaction_id", ""),
                                "from": tx_from,
                                "timestamp": tx_time.isoformat(),
                            }
                    # Also accept if memo matches directly
                    if tx_memo == memo:
                        return {
                            "type": "tron_usdt",
                            "amount": tx_value,
                            "tx_hash": tx.get("transaction_id", ""),
                            "from": tx_from,
                            "timestamp": tx_time.isoformat(),
                        }

    except Exception as e:
        logger.error("TRON payment check failed: %s", e)

    return None


def check_ton_payment(telegram_id: int) -> Optional[dict]:
    """Check if user has sent TON to our wallet.
    Returns payment dict if found, None otherwise."""
    if not TON_WALLET:
        return None

    memo = _generate_memo(telegram_id)
    headers = {}
    if TON_API_KEY:
        headers["X-API-Key"] = TON_API_KEY

    try:
        url = f"{TON_API}/getTransactions"
        params = {
            "address": TON_WALLET,
            "limit": 50,
            "lt": 0,
            "hash": "",
        }
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            logger.warning("TON API error: %d", resp.status_code)
            return None

        data = resp.json()
        transactions = data.get("result", [])

        for tx in transactions:
            # Check out messages for memo
            out_msgs = tx.get("out_msgs", [])
            in_msg = tx.get("in_msg", {})

            tx_memo = in_msg.get("message", "")
            tx_value = int(in_msg.get("value", 0)) / 1_000_000_000  # nanotons to TON
            tx_from = in_msg.get("source", "")
            tx_hash = tx.get("hash", "")
            tx_lt = tx.get("lt", "")

            # Check memo match and amount
            if tx_memo == memo and tx_value >= TON_AMOUNT * 0.95:
                # Check if recent (within 24 hours)
                tx_utime = tx.get("utime", 0)
                tx_time = datetime.fromtimestamp(tx_utime, tz=timezone.utc)
                now = datetime.now(timezone.utc)
                hours_ago = (now - tx_time).total_seconds() / 3600

                if hours_ago <= 24:
                    return {
                        "type": "ton",
                        "amount": tx_value,
                        "tx_hash": tx_hash,
                        "from": tx_from,
                        "timestamp": tx_time.isoformat(),
                    }

    except Exception as e:
        logger.error("TON payment check failed: %s", e)

    return None


def check_all_payments(telegram_id: int) -> Optional[dict]:
    """Check both TON and TRON for payments. Returns first match found."""
    # Check TRON first (more likely)
    result = check_tron_payment(telegram_id)
    if result:
        return result

    # Then check TON
    result = check_ton_payment(telegram_id)
    if result:
        return result

    return None


async def process_payment(telegram_id: int, payment: dict) -> bool:
    """Process a confirmed payment and upgrade user to Pro"""
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(days=PRO_DURATION_DAYS)).isoformat()

    # Upgrade user
    success = db.update_user_tier(telegram_id, TIER_PRO, expires_at=expires_at)
    if success:
        # Record payment
        db.record_payment(
            telegram_id=telegram_id,
            payment_type=payment["type"],
            amount=payment["amount"],
            tx_hash=payment.get("tx_hash", ""),
            from_address=payment.get("from", ""),
        )
        logger.info("User %d upgraded to Pro via %s payment", telegram_id, payment["type"])
        return True

    return False