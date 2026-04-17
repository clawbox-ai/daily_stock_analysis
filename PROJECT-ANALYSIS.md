# StockAnalyst Bot — Project Analysis & Launch Plan

## What We Built

A Telegram bot (`@claw_analyst_bot`) that provides deterministic stock/crypto analysis with:
- **Free tier**: Daily broadcast of a random stock Battle Plan, dashboard for default stocks
- **Pro tier ($9/mo)**: 20-stock watchlist, unlimited /analyze, daily email delivery, market review
- **Crypto payments**: TRON USDT (live), TON (ready to add)
- **Email delivery**: Resend API, city-based timezone, 1x/day Pro cap, once/day time change limit
- **Financial disclaimer + traceable watermark** on all analysis outputs

## Code Architecture

| File | Purpose |
|------|---------|
| `subscription_bot.py` | Main bot — commands, callbacks, inline buttons |
| `db.py` | SQLite DB — users, watchlists, payments, email settings |
| `tiers.py` | Tier config — Free/Pro, pricing, permissions |
| `broadcaster.py` | Daily broadcast to all users |
| `email_sender.py` | Resend API email engine (white theme, Buy/Watch/Sell) |
| `city_timezones.py` | 150+ cities → IANA timezones |
| `payment.py` | TRON USDT + TON payment verification |

## Cost Analysis

| Item | Cost |
|------|------|
| Telegram Bot | Free |
| Resend (100 emails/day) | Free tier |
| TronGrid API | Free tier |
| Gemini API (Free tier broadcasts) | Free |
| GPT-4o-mini (Pro analyses) | ~$0.003/analysis |
| Hetzner VPS (already running) | Already paid |
| **Total monthly at 0 users** | **$0** |
| **Per Pro user/month** | **~$0.30** (email + analyses) |
| **Revenue per Pro user** | **$9** |
| **Gross margin per Pro user** | **$8.70 (97%)** |

## Break-Even & Revenue Projections

| Pro Users | Monthly Revenue | Monthly Cost | Net |
|-----------|---------------|-------------|-----|
| 0 | $0 | $0 | $0 |
| 10 | $90 | $3 | +$87 |
| 50 | $450 | $15 | +$435 |
| 100 | $900 | $30 | +$870 |
| 500 | $4,500 | $150 | +$4,350 |

Break-even: **1 Pro user** (costs are negligible at low scale).

## What's Left Before Launch

### Must-Do (Blockers)
1. ✅ **Merge to main** — enables GitHub Actions free tier for daily broadcast
2. ⬜ **Add credit to OpenAI** — current key has $5 cap, need billing setup for Pro tier
3. ⬜ **Deploy to Hetzner** — 24/7 bot, persistent DB, systemd service
4. ⬜ **Free tier Gemini key** — separate key on hydrocolloid13 repo's Google Cloud project (free forever for broadcasts)

### Should-Do (Quality)
5. ⬜ **TON wallet** — create in Tonkeeper, add to all 3 projects (StockAnalyst + PocketChess + ChessAnon)
6. ⬜ **Daily broadcast scheduler** — wire up the random stock broadcast for Free users
7. ⬜ **Payment verification testing** — send a real 9 USDT test payment and verify /check works

### Nice-to-Have (Post-Launch)
8. ⬜ **Stripe integration** — card payments for non-crypto users
9. ⬜ **Referral system** — "Invite a friend, both get 1 week Pro free"
10. ⬜ **Admin commands** — /grant_pro, /revoke_pro for manual subscription management

## Merge Strategy

**The key insight**: The original repo (`hydrocolloid13/daily_stock_analysis`) uses GitHub Actions with a **free Gemini key** for the daily analysis. That workflow runs on main, costs $0, and is perfect for our Free tier broadcast.

Our Pro tier uses **GPT-4o-mini** (paid via OpenAI) on Hetzner for on-demand analysis.

**Two-layer architecture**:
- **Layer 1 (Free)**: GitHub Actions + Gemini free key → broadcasts to all Free users
- **Layer 2 (Pro)**: Hetzner bot + OpenAI → on-demand analysis, email delivery, payments

**Merge is safe because**:
- Our changes are additive (new files: `subscription_bot.py`, `db.py`, `payment.py`, etc.)
- Original analysis pipeline (`src/analysis/`, `src/daily_analysis.py`) is untouched
- Git preserves full history, `git revert <merge-commit>` for rollback
- No existing functionality breaks

## Marketing Plan — Intelligent Advertising

### Target Audience
- Crypto/stock traders on Telegram (18-45, male-skewed, tech-savvy)
- Active in trading groups, DeFi communities, signal channels

### Phase 1: Seed (Week 1-2, $0)
1. **Telegram Trading Groups** — Join top 20 crypto/stock trading groups, share analysis screenshots (with watermark)
2. **Twitter/X Posts** — @SDealz71716 can cross-promote, post Battle Plan screenshots with "Get full analysis free → @claw_analyst_bot"
3. **Reddit** — r/algotrading, r/Daytrading, r/CryptoCurrency — "Built a free stock analysis bot, here's today's BTC analysis"
4. **Product Hunt** — Launch page with "Free daily stock analysis, Pro for $9/mo"
5. **YouTube Shorts** — Screen recording of Battle Plan output, "link in bio"

### Phase 2: Growth (Week 3-4, $0-$50)
6. **Referral Program** — "Share your memo, both get Pro free for 1 week" (costs us ~$0.30/referral)
7. **Free Daily Broadcast Quality** — The broadcast IS the marketing. Make it high-value so people share it
8. **Telegram Channel** — Create @StockAnalyst channel for daily free analysis screenshots
9. **Cross-promote** — PocketChess user base (when live), Humanity Pulse audience

### Phase 3: Scale (Month 2+, $50-200/mo)
10. **Telegram Ads** — Target trading/crypto channels ($50-100/mo)
11. **Twitter Ads** — Promoted tweets targeting traders
12. **Influencer outreach** — Pay trading influencers 1-2 Pro subscriptions for a review
13. **SEO** — clawboxai.org blog posts: "Best Free Stock Analysis Bot 2026"

### Key Metric Targets
| Metric | Week 1 | Month 1 | Month 3 |
|--------|--------|---------|---------|
| Free users | 50 | 200 | 1,000 |
| Pro users | 2 | 10 | 50 |
| Revenue/mo | $18 | $90 | $450 |

### Conversion Strategy
- Free tier is **the funnel** — daily broadcast hooks them
- Battle Plan quality **sells Pro** — show enough to want more
- $9/mo is **impulse-buy cheap** for traders spending $50+/mo on tools
- Crypto payment = **no friction** (no card, no KYC, no chargebacks)

## Risk Analysis

| Risk | Impact | Mitigation |
|------|--------|------------|
| OpenAI key burns (leaked) | High | Already happened 4x. Keep in env vars, never in chat |
| TronGrid rate limit | Medium | Free tier: 100 req/min. Cache results, don't poll excessively |
| Gemini free tier exhausts | Low | Fallback to deterministic-only analysis |
| Bot gets popular, costs spike | Good problem | Scale Pro pricing or add tier caps |
| User disputes crypto payment | Low | Immutable blockchain, memo-based identification |
| Telegram bans bot | Medium | Keep content compliant, no financial advice claims |

## TL;DR — Minimum Path to Launch

1. **Merge to main** → Free tier broadcasts start via GitHub Actions
2. **Add $10 credit to OpenAI** → Pro analyses work
3. **Deploy to Hetzner** → 24/7 bot uptime
4. **Start marketing** → Telegram groups + Twitter + Reddit

Total cost to launch: **$0** (using existing infrastructure)
Time to launch: **1 day** after merge + deploy