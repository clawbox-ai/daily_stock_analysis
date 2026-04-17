# -*- coding: utf-8 -*-
"""
Tier definitions and validation

Responsibilities:
1. Define Free / Pro tier permissions and limits
2. Provide unified tier validation helper functions
"""
from dataclasses import dataclass, field
from typing import Optional


# ===========================
# Tier Constants
# ===========================

TIER_FREE = "free"
TIER_PRO = "pro"

ALL_TIERS = (TIER_FREE, TIER_PRO)


@dataclass
class TierConfig:
    """Single tier configuration"""
    name: str                        # Tier identifier
    label: str                       # Display name
    price_monthly: float             # Monthly price (USD)
    max_watchlist: Optional[int]     # Max watchlist stocks; None = unlimited
    on_demand_analysis: bool         # Whether /analyze on-demand is supported
    daily_broadcast: bool           # Whether daily broadcast is received
    market_review: bool              # Whether market review is received
    priority_analysis: bool          # Whether priority analysis is enabled
    email_delivery: bool            # Whether full analysis is delivered via email
    description: str = ""            # Tier description (shown to users)


# ===========================
# Tier Definitions
# ===========================

TIER_CONFIGS: dict[str, TierConfig] = {
    TIER_FREE: TierConfig(
        name=TIER_FREE,
        label="Free",
        price_monthly=0.0,
        max_watchlist=None,           # Free users use default stock list, no custom watchlist
        on_demand_analysis=False,
        daily_broadcast=True,
        market_review=False,
        priority_analysis=False,
        email_delivery=False,
        description="Daily broadcast with random stock Battle Plan. Upgrade to Pro for unlimited access + email delivery.",
    ),
    TIER_PRO: TierConfig(
        name=TIER_PRO,
        label="Pro",
        price_monthly=9.0,
        max_watchlist=20,            # 20 stock limit
        on_demand_analysis=True,
        daily_broadcast=True,
        market_review=True,
        priority_analysis=True,
        email_delivery=True,
        description="20-stock watchlist, on-demand analysis, daily market review, email delivery, priority processing. Full access.",
    ),
}


def get_tier_config(tier: str) -> TierConfig:
    """Get tier config; unknown tier falls back to Free"""
    return TIER_CONFIGS.get(tier, TIER_CONFIGS[TIER_FREE])


def can_use_on_demand(tier: str) -> bool:
    """Whether /analyze on-demand analysis is available"""
    return get_tier_config(tier).on_demand_analysis


def can_customize_watchlist(tier: str) -> bool:
    """Whether custom watchlist is available"""
    cfg = get_tier_config(tier)
    return cfg.max_watchlist is not None or cfg.name == TIER_PRO


def watchlist_limit(tier: str) -> Optional[int]:
    """Watchlist stock limit; None = unlimited"""
    return get_tier_config(tier).max_watchlist


def can_receive_market_review(tier: str) -> bool:
    """Whether market review reports are received"""
    return get_tier_config(tier).market_review


def is_valid_tier(tier: str) -> bool:
    """Validate tier identifier"""
    return tier in ALL_TIERS


def format_tier_menu() -> str:
    """Generate tier selection menu text (for /subscribe reply)"""
    lines = ["📋 *Subscription Plans*\n"]
    for tier_key in ALL_TIERS:
        cfg = TIER_CONFIGS[tier_key]
        price = "Free" if cfg.price_monthly == 0 else f"${cfg.price_monthly:.0f}/mo"
        watchlist = "Default stocks" if tier_key == TIER_FREE else f"Up to {cfg.max_watchlist}"
        features = []
        if cfg.daily_broadcast:
            features.append("Daily Broadcast")
        if cfg.on_demand_analysis:
            features.append("On-Demand Analysis")
        if cfg.market_review:
            features.append("Market Review")
        if cfg.priority_analysis:
            features.append("Priority Processing")
        if cfg.email_delivery:
            features.append("Email Delivery")

        lines.append(
            f"*{cfg.label}* — {price}\n"
            f"  • Watchlist: {watchlist}\n"
            f"  • Features: {' / '.join(features)}\n"
            f"  • {cfg.description}\n"
        )
    lines.append("_To upgrade, contact admin for a payment link._")
    return "\n".join(lines)