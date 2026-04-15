# -*- coding: utf-8 -*-
"""
订阅机器人用户数据库

职责：
1. 管理 users 表（用户信息、套餐、自选股）
2. 管理 payments 表（付款记录）
3. 提供用户 CRUD 与订阅查询辅助函数

数据库文件路径通过环境变量 SUBSCRIPTION_DB_PATH 配置，
默认为 ./data/subscription.db
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

# 默认数据库路径（可通过 SUBSCRIPTION_DB_PATH 覆盖）
_DEFAULT_DB_PATH = "./data/subscription.db"


def _get_db_path() -> str:
    return os.environ.get("SUBSCRIPTION_DB_PATH", _DEFAULT_DB_PATH)


# ===========================
# 连接与初始化
# ===========================

@contextmanager
def _get_conn():
    """获取数据库连接（自动提交/回滚的上下文管理器）"""
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # 开启 WAL 模式，降低并发写入冲突
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
    """初始化数据库表结构（首次启动或迁移时调用）"""
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
    logger.info("订阅数据库初始化完成: %s", _get_db_path())


# ===========================
# 用户 CRUD
# ===========================

def get_user(telegram_id: int) -> Optional[dict]:
    """根据 telegram_id 查询用户；不存在返回 None"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    if row is None:
        return None
    return _row_to_user(row)


def create_user(telegram_id: int, username: Optional[str] = None) -> dict:
    """注册新用户，默认 Free 套餐；若已存在则直接返回现有记录"""
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
    logger.info("新用户注册: telegram_id=%d username=%s", telegram_id, username)
    return get_user(telegram_id)


def update_user_tier(telegram_id: int, tier: str, expires_at: Optional[str] = None) -> bool:
    """更新用户套餐等级和到期时间；返回是否成功"""
    if not is_valid_tier(tier):
        logger.warning("无效套餐: %s", tier)
        return False
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE users SET tier = ?, expires_at = ? WHERE telegram_id = ?",
            (tier, expires_at, telegram_id),
        )
    updated = cursor.rowcount > 0
    if updated:
        logger.info("用户套餐更新: telegram_id=%d tier=%s expires_at=%s", telegram_id, tier, expires_at)
    return updated


def update_watchlist(telegram_id: int, watchlist: List[str]) -> bool:
    """更新用户自选股列表；返回是否成功"""
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE users SET watchlist = ? WHERE telegram_id = ?",
            (json.dumps(watchlist, ensure_ascii=False), telegram_id),
        )
    return cursor.rowcount > 0


def get_watchlist(telegram_id: int) -> List[str]:
    """获取用户自选股列表；用户不存在时返回空列表"""
    user = get_user(telegram_id)
    if not user:
        return []
    return user.get("watchlist", [])


def get_users_by_tier(tier: str) -> List[dict]:
    """查询指定套餐的所有用户"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE tier = ?", (tier,)
        ).fetchall()
    return [_row_to_user(r) for r in rows]


def get_all_users() -> List[dict]:
    """获取所有用户"""
    with _get_conn() as conn:
        rows = conn.execute("SELECT * FROM users").fetchall()
    return [_row_to_user(r) for r in rows]


# ===========================
# 支付记录 CRUD
# ===========================

def record_payment(telegram_id: int, amount: float, tier: str, status: str = "pending") -> int:
    """记录一笔支付；返回新记录 id"""
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
    logger.info("支付记录: id=%d telegram_id=%d amount=%.2f tier=%s status=%s",
                payment_id, telegram_id, amount, tier, status)
    return payment_id


def update_payment_status(payment_id: int, status: str) -> bool:
    """更新支付状态（pending / paid / failed / refunded）"""
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE payments SET status = ? WHERE id = ?",
            (status, payment_id),
        )
    return cursor.rowcount > 0


def get_payments(telegram_id: int) -> List[dict]:
    """获取用户所有支付记录"""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM payments WHERE telegram_id = ? ORDER BY created_at DESC",
            (telegram_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ===========================
# 内部辅助
# ===========================

def _row_to_user(row: sqlite3.Row) -> dict:
    """将数据库行转为字典，并反序列化 watchlist"""
    d = dict(row)
    try:
        d["watchlist"] = json.loads(d.get("watchlist") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["watchlist"] = []
    return d


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串"""
    return datetime.now(timezone.utc).isoformat()
