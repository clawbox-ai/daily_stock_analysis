#!/bin/bash
cd /Users/clawbox/.openclaw/workspace/daily_stock_analysis
export OPENAI_API_KEY=$(cat /tmp/dsa_openai_key.txt | tr -d '\n')
export TON_WALLET=UQC4r2fpzeIv51rrOQDFHIj2X8WWB5q5WaIVlRgZeRNgTH0_
export TON_API_KEY=4c26ecaffe9a22161b4118432b7c50165560b699135e6e9a68435cb7bc8b2171
.venv/bin/python3 -m src.bot.subscription_bot