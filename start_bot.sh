#!/bin/bash
cd /Users/clawbox/.openclaw/workspace/daily_stock_analysis
export OPENAI_API_KEY=$(cat /tmp/dsa_openai_key.txt | tr -d '\n')
.venv/bin/python3 -m src.bot.subscription_bot