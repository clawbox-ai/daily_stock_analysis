# -*- coding: utf-8 -*-
"""
套餐等级定义与校验

职责：
1. 定义 Free / Pro / Elite 三档套餐的权限与限制
2. 提供统一的套餐校验辅助函数
"""
from dataclasses import dataclass, field
from typing import Optional


# ===========================
# 套餐等级常量
# ===========================

TIER_FREE = "free"
TIER_PRO = "pro"
TIER_ELITE = "elite"

ALL_TIERS = (TIER_FREE, TIER_PRO, TIER_ELITE)


@dataclass
class TierConfig:
    """单档套餐配置"""
    name: str                        # 套餐标识符
    label: str                       # 显示名称
    price_monthly: float             # 月费（USD）
    max_watchlist: Optional[int]     # 自选股上限；None 表示无限制
    on_demand_analysis: bool         # 是否支持 /analyze 按需分析
    daily_broadcast: bool            # 是否接收每日广播
    market_review: bool              # 是否接收大盘复盘
    priority_analysis: bool          # 是否优先处理分析任务
    description: str = ""            # 套餐简介（展示给用户）


# ===========================
# 套餐定义
# ===========================

TIER_CONFIGS: dict[str, TierConfig] = {
    TIER_FREE: TierConfig(
        name=TIER_FREE,
        label="免费版 Free",
        price_monthly=0.0,
        max_watchlist=None,           # 免费用户使用系统默认股票列表，不支持自定义
        on_demand_analysis=False,
        daily_broadcast=True,
        market_review=False,
        priority_analysis=False,
        description="每日接收系统默认股票的分析广播，无需配置。",
    ),
    TIER_PRO: TierConfig(
        name=TIER_PRO,
        label="专业版 Pro",
        price_monthly=9.0,
        max_watchlist=10,             # 最多 10 只自选股
        on_demand_analysis=True,
        daily_broadcast=True,
        market_review=False,
        priority_analysis=False,
        description="自定义最多 10 只股票，支持随时按需分析。",
    ),
    TIER_ELITE: TierConfig(
        name=TIER_ELITE,
        label="旗舰版 Elite",
        price_monthly=29.0,
        max_watchlist=None,           # 无限制
        on_demand_analysis=True,
        daily_broadcast=True,
        market_review=True,
        priority_analysis=True,
        description="无限自选股、优先分析、每日大盘复盘，全功能解锁。",
    ),
}


def get_tier_config(tier: str) -> TierConfig:
    """获取套餐配置；未知等级降级到 Free"""
    return TIER_CONFIGS.get(tier, TIER_CONFIGS[TIER_FREE])


def can_use_on_demand(tier: str) -> bool:
    """是否可以使用 /analyze 按需分析"""
    return get_tier_config(tier).on_demand_analysis


def can_customize_watchlist(tier: str) -> bool:
    """是否可以自定义自选股列表"""
    cfg = get_tier_config(tier)
    return cfg.max_watchlist is not None or cfg.name == TIER_ELITE


def watchlist_limit(tier: str) -> Optional[int]:
    """自选股数量上限；None 表示无限"""
    return get_tier_config(tier).max_watchlist


def can_receive_market_review(tier: str) -> bool:
    """是否接收大盘复盘报告"""
    return get_tier_config(tier).market_review


def is_valid_tier(tier: str) -> bool:
    """校验套餐标识符是否合法"""
    return tier in ALL_TIERS


def format_tier_menu() -> str:
    """生成套餐选择菜单文本（用于 /subscribe 回复）"""
    lines = ["📋 *套餐说明*\n"]
    for tier_key in ALL_TIERS:
        cfg = TIER_CONFIGS[tier_key]
        price = "免费" if cfg.price_monthly == 0 else f"${cfg.price_monthly:.0f}/月"
        watchlist = "系统默认" if tier_key == TIER_FREE else (
            "无限制" if cfg.max_watchlist is None else f"最多 {cfg.max_watchlist} 只"
        )
        features = []
        if cfg.daily_broadcast:
            features.append("每日广播")
        if cfg.on_demand_analysis:
            features.append("按需分析")
        if cfg.market_review:
            features.append("大盘复盘")
        if cfg.priority_analysis:
            features.append("优先处理")

        lines.append(
            f"*{cfg.label}* — {price}\n"
            f"  • 自选股：{watchlist}\n"
            f"  • 功能：{' / '.join(features)}\n"
            f"  • {cfg.description}\n"
        )
    lines.append("_如需升级，请联系管理员获取支付链接。_")
    return "\n".join(lines)
