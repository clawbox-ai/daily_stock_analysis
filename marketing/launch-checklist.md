# StockAnalyst Launch Checklist

**Bot:** @claw_analyst_bot  
**Channel:** @StockAnalyst_channel (TBD)  
**Important:** Do NOT execute any external-facing steps without Adrian's approval.

---

## Phase 1: Pre-Launch (Internal Readiness)

- [ ] **Bot is live and responding** — test all commands on @claw_analyst_bot
  - [ ] `/start` works and shows welcome message
  - [ ] `/analyze <ticker>` returns a Battle Plan
  - [ ] Free tier daily broadcast works
  - [ ] Pro tier subscription flow works (USDT TRC-20 payment)
  - [ ] Dashboard is accessible and functional
- [ ] **Financial disclaimer** appears on all bot outputs
- [ ] **Battle Plan formatting** is consistent and readable on mobile
- [ ] **Error handling** — bot responds gracefully to invalid tickers, network errors, etc.
- [ ] **Rate limiting** — prevent spam/abuse of free tier
- [ ] **Pro tier payment** — TRON USDT (TRC-20) wallet is set up and monitored
- [ ] **Pro tier onboarding** — after payment, user gets watchlist setup instructions

## Phase 2: Channel Setup

- [ ] **Create Telegram channel** @StockAnalyst_channel (or closest available)
- [ ] **Set channel to Public**
- [ ] **Write channel description:** "Free daily stock & crypto Battle Plans. Transparent, deterministic scoring. Powered by @claw_analyst_bot"
- [ ] **Set channel profile photo** (StockAnalyst branding)
- [ ] **Add @claw_analyst_bot as channel admin** with post/edit/delete permissions
- [ ] **Post and pin Welcome message** (see telegram-channel.md Broadcast #1)
- [ ] **Post first daily Battle Plan** (see telegram-channel.md Broadcast #2)

## Phase 3: Social Media Preparation

- [ ] **Twitter/X account** — @SDealz71716
  - [ ] Update bio to mention StockAnalyst
  - [ ] Pin launch tweet (see twitter-posts.md Tweet #1)
  - [ ] Schedule first 7 tweets (1/day for launch week)
- [ ] **Reddit** — prepare accounts with sufficient karma
  - [ ] Verify karma meets minimums for r/algotrading, r/Daytrading, r/CryptoCurrency
  - [ ] Customize each post draft (see reddit-posts.md) — don't cross-post identical content
  - [ ] Check each subreddit's self-promotion rules before posting

## Phase 4: Launch Day

- [ ] **Post Welcome + first Battle Plan** to Telegram channel
- [ ] **Tweet launch announcement** (Tweet #1)
- [ ] **Post to r/algotrading** first (most friendly to self-promotion)
- [ ] **Monitor all channels** for 4-6 hours after posting
  - [ ] Reply to every comment on Reddit within 2 hours
  - [ ] Reply to every tweet reply within 1 hour
  - [ ] Respond to Telegram channel questions
- [ ] **Track metrics:**
  - [ ] Telegram channel subscribers (baseline: 0)
  - [ ] Bot users (check /start count)
  - [ ] Twitter impressions and engagement
  - [ ] Reddit upvotes and comments
  - [ ] Pro tier signups (if any on day 1)

## Phase 5: First Week

- [ ] **Daily:** Post Battle Plan to Telegram channel at 8:00 AM EST
- [ ] **Daily:** Post 1 tweet from the scheduled content
- [ ] **Day 3:** Post to r/Daytrading (stagger from r/algotrading)
- [ ] **Day 5:** Post to r/CryptoCurrency
- [ ] **Daily:** Engage with all comments/replies across platforms
- [ ] **Day 7:** Post weekly recap (see telegram-channel.md Broadcast #5)
- [ ] **End of week:** Review metrics and adjust strategy

## Phase 6: Ongoing

- [ ] **Daily Battle Plan broadcast** — consistency is king
- [ ] **3-4 tweets/week** — mix of Battle Plan teasers, educational content, Pro tier highlights
- [ ] **1 Reddit post/week** — rotate subreddits, always value-first
- [ ] **Weekly recap** every Friday on Telegram channel
- [ ] **Monitor and respond** to all user questions within 2 hours
- [ ] **Monthly:** Review subscriber growth, Pro conversions, and content performance
- [ ] **Quarterly:** Update marketing copy based on what's working

---

## Key Metrics to Track

| Metric | Target (Month 1) | Target (Month 3) |
|--------|-------------------|-------------------|
| Telegram channel subscribers | 100 | 500 |
| Bot users (total) | 50 | 250 |
| Pro tier subscribers | 5 | 25 |
| Twitter followers gained | 50 | 200 |
| Reddit post engagement | 50 upvotes | 200 upvotes |

---

## Risk Mitigation

- **Financial disclaimer:** Mandatory on ALL outputs. No exceptions.
- **Reddit spam rules:** Stagger posts, customize each one, engage genuinely. Never post the same content to multiple subs on the same day.
- **Twitter shadowban:** Don't post more than 3-4 times/day. Don't use the same hashtags on every tweet.
- **Bot downtime:** Monitor @claw_analyst_bot uptime. Set up alerts if possible.
- **Payment issues:** Have a manual process ready for TRON USDT payment verification failures.

---

## Quick Reference

- **Bot:** @claw_analyst_bot
- **Channel:** @StockAnalyst_channel
- **Twitter:** @SDealz71716
- **Pro price:** $9/mo (USDT on TRON, TRC-20)
- **Free tier:** Daily random stock Battle Plan + dashboard
- **Pro tier:** 20-stock watchlist, unlimited analysis, email delivery
- **Scoring:** RSI + MACD + Price Change + MA Alignment + Volume (0-100)
- **Battle Plan format:** Decision Score → Signal → Action → Entry/SL/TP