# -*- coding: utf-8 -*-
"""
Subscription bot user database

Responsibilities:
1. Manage users table (user info, tier, watchlist)
2. Manage payments table (payment records)
3. Provide user CRUD and subscription query helpers

Database file path configured via SUBSCRIPTION_DB_PATH env var,
defaults to ./data/subscription.db
"""
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Optional

from src.bot.tiers import TIER_FREE, is_valid_tier

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "./data/subscription.db"


def _get_db_path() -> str:
    return os.environ.get("SUBSCRIPTION_DB_PATH", _DEFAULT_DB_PATH)


# ===========================
# Connection & init
# ===========================

@contextmanager
def _get_conn():
    """Get database connection (auto-commit/rollback context manager)"""
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialize database tables (called on first start or migration)"""
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id   INTEGER PRIMARY KEY,
                username      TEXT,
                tier          TEXT    NOT NULL DEFAULT 'free',
                watchlist     TEXT    NOT NULL DEFAULT '[]',
                created_at    TEXT    NOT NULL,
                expires_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS payments (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id   INTEGER NOT NULL,
                amount        REAL    NOT NULL,
                tier          TEXT    NOT NULL,
                status        TEXT    NOT NULL DEFAULT 'pending',
                created_at    TEXT    NOT NULL,
                FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
            );

            CREATE INDEX IF NOT EXISTS idx_payments_telegram_id
                ON payments(telegram_id);
        """)
    # Migration: add email/timezone columns if missing
    try:
        with _get_conn() as conn:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    except Exception:
        pass
    try:
        with _get_conn() as conn:
            conn.execute("ALTER TABLE users ADD COLUMN timezone TEXT")
    except Exception:
        pass
    try:
        with _get_conn() as conn:
            conn.execute("ALTER TABLE users ADD COLUMN delivery_time TEXT NOT NULL DEFAULT '08:00'")
    except Exception:
        pass
    logger.info("Database initialized: %s", _get_db_path())


# ===========================
# User CRUD
# ===========================

def get_user(telegram_id: int) -> Optional[dict]:
    """Get user by telegram_id; returns None if not found"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    if row is None:
        return None
    return _row_to_user(row)


def get_users_by_tier(tier: str) -> list[dict]:
    """Get all users with a specific tier"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE tier = ?", (tier,)
        ).fetchall()
    return [_row_to_user(row) for row in rows]


def create_user(telegram_id: int, username: Optional[str] = None) -> dict:
    """Register new user with Free tier; returns existing user if already exists"""
    existing = get_user(telegram_id)
    if existing:
        return existing

    now = _now_iso()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (telegram_id, username, tier, watchlist, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (telegram_id, username, TIER_FREE, "[]", now, None),
        )
    logger.info("New user registered: telegram_id=%d username=%s", telegram_id, username)
    return get_user(telegram_id)


def update_user_tier(telegram_id: int, tier: str, expires_at: Optional[str] = None) -> bool:
    """Update user tier and expiry; returns True if successful"""
    if not is_valid_tier(tier):
        logger.warning("Invalid tier: %s", tier)
        return False
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE users SET tier = ?, expires_at = ? WHERE telegram_id = ?",
            (tier, expires_at, telegram_id),
        )
    updated = cursor.rowcount > 0
    if updated:
        logger.info("User tier updated: telegram_id=%d tier=%s expires_at=%s", telegram_id, tier, expires_at)
    return updated


def update_watchlist(telegram_id: int, watchlist: List[str]) -> bool:
    """Update user's watchlist; returns True if successful"""
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE users SET watchlist = ? WHERE telegram_id = ?",
            (json.dumps(watchlist, ensure_ascii=False), telegram_id),
        )
    return cursor.rowcount > 0


def get_watchlist(telegram_id: int) -> List[str]:
    """Get user's watchlist; returns empty list if user not found"""
    user = get_user(telegram_id)
    if not user:
        return []
    return user.get("watchlist", [])


def get_users_by_tier(tier: str) -> List[dict]:
    """Get all users with the specified tier"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE tier = ?", (tier,)
        ).fetchall()
    return [_row_to_user(r) for r in rows]


def get_all_users() -> List[dict]:
    """Get all users"""
    with _get_conn() as conn:
        rows = conn.execute("SELECT * FROM users").fetchall()
    return [_row_to_user(r) for r in rows]


# ===========================
# Payment CRUD
# ===========================

def record_payment(telegram_id: int, amount: float, tier: str, status: str = "pending") -> int:
    """Record a payment; returns new record id"""
    now = _now_iso()
    with _get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO payments (telegram_id, amount, tier, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (telegram_id, amount, tier, status, now),
        )
        payment_id = cursor.lastrowid
    logger.info("Payment recorded: id=%d telegram_id=%d amount=%.2f tier=%s status=%s",
                payment_id, telegram_id, amount, tier, status)
    return payment_id


def update_payment_status(payment_id: int, status: str) -> bool:
    """Update payment status (pending / paid / failed / refunded)"""
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE payments SET status = ? WHERE id = ?",
            (status, payment_id),
        )
    return cursor.rowcount > 0


def get_payments(telegram_id: int) -> List[dict]:
    """Get all payments for a user"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM payments WHERE telegram_id = ? ORDER BY created_at DESC",
            (telegram_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_email(telegram_id: int) -> Optional[str]:
    """Get user's email address"""
    user = get_user(telegram_id)
    if not user:
        return None
    return user.get("email")


def set_email(telegram_id: int, email: str) -> bool:
    """Set user's email address for Pro email delivery"""
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE users SET email = ? WHERE telegram_id = ?",
            (email, telegram_id),
        )
    return cursor.rowcount > 0


def set_email_schedule(telegram_id: int, email: str, timezone: str, delivery_time: str) -> bool:
    """Set email, timezone, and delivery time for Pro email delivery"""
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE users SET email = ?, timezone = ?, delivery_time = ? WHERE telegram_id = ?",
            (email, timezone, delivery_time, telegram_id),
        )
    return cursor.rowcount > 0


def get_email_schedule(telegram_id: int) -> dict:
    """Get user's email delivery settings"""
    user = get_user(telegram_id)
    if not user:
        return {"email": None, "timezone": None, "delivery_time": "08:00"}
    return {
        "email": user.get("email"),
        "timezone": user.get("timezone"),
        "delivery_time": user.get("delivery_time", "08:00"),
    }


# ===========================
# Helpers
# ===========================

def _row_to_user(row: sqlite3.Row) -> dict:
    """Convert database row to dict, deserialize watchlist JSON"""
    d = dict(row)
    try:
        d["watchlist"] = json.loads(d.get("watchlist") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["watchlist"] = []
    return d


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string"""
    return datetime.now(timezone.utc).isoformat()