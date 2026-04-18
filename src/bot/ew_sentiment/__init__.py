"""
Elliott Wave Sentiment Engine for StockAnalyst Pro.

Scrapes social media for EW opinions, validates against price structure,
and produces a clean trader-facing analysis with confidence index.

The output reads like a trader's EW analysis — no mention of scraping,
sources, or social posts. Background logic only.
"""

import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ─── EW Pattern Recognition ───────────────────────────────────

# Wave classification patterns with timeframe inference
EW_PATTERNS = {
    'impulse_up': {
        'regex': [r'\bwave\s*3\b', r'\bimpulse\s*up\b', r'\bbullish\s*impulse\b',
                  r'\bbreakout\b.*\bwave\b', r'\bwave\s*1\b.*\bup\b',
                  r'\bleading\s*diagonal\b', r'\bextending\b.*\bup\b'],
        'direction': 'UP',
        'wave': 3,  # default to wave 3 for impulse
        'structure': 'impulse',
    },
    'wave1_up': {
        'regex': [r'\bwave\s*1\b', r'\bnew\s*trend\b', r'\bfresh\s*impulse\b',
                  r'\bstart\s*of\s*new\b', r'\bbottom\s*in\b'],
        'direction': 'UP',
        'wave': 1,
        'structure': 'impulse',
    },
    'wave5_up': {
        'regex': [r'\bwave\s*5\b', r'\bwave\s*v\b', r'\bfifth\s*wave\b',
                  r'\bending\s*diagonal\s*up\b', r'\bfinal\s*thrust\b',
                  r'\blast\s*leg\s*up\b'],
        'direction': 'UP',
        'wave': 5,
        'structure': 'impulse',
    },
    'correction_down': {
        'regex': [r'\bwave\s*2\b', r'\bwave\s*4\b', r'\bcorrection\b',
                  r'\bretracement\b', r'\bpullback\b', r'\babc\b.*\bdown\b',
                  r'\bzigzag\b.*\bdown\b', r'\bflat\s*correction\b',
                  r'\bwave\s*b\b', r'\bdip\b.*\bbuy\b'],
        'direction': 'DOWN',
        'wave': 4,  # corrections could be 2 or 4
        'structure': 'corrective',
    },
    'bearish_impulse': {
        'regex': [r'\bwave\s*3\b.*\bdown\b', r'\bbearish\s*impulse\b',
                  r'\bwave\s*1\b.*\bdown\b', r'\bcrash\b', r'\bdump\b',
                  r'\bcapitulation\b', r'\bfalling\s*knife\b'],
        'direction': 'DOWN',
        'wave': 3,
        'structure': 'impulse',
    },
    'wave5_down': {
        'regex': [r'\bwave\s*5\b.*\bdown\b', r'\bwave\s*v\b.*\bdown\b',
                  r'\bfinal\s*decline\b', r'\bending\s*diagonal\s*down\b',
                  r'\bexhaustion\b'],
        'direction': 'DOWN',
        'wave': 5,
        'structure': 'impulse',
    },
    'sideways': {
        'regex': [r'\bsideways\b', r'\branging\b', r'\bconsolidat',
                  r'\btriangle\b', r'\bflat\s*range\b', r'\bchoppy\b',
                  r'\bwave\s*4\b.*\bsideways\b'],
        'direction': 'FLAT',
        'wave': 4,
        'structure': 'corrective',
    },
}

GENERAL_BULLISH = [r'\bbullish\b', r'\bpump\b', r'\bmoon\b', r'\blong\b', r'\bbuy\b',
                   r'\bbreakout\b', r'\bsupport\b', r'\bbounce\b']
GENERAL_BEARISH = [r'\bbearish\b', r'\bdump\b', r'\bshort\b', r'\bsell\b',
                   r'\bcrash\b', r'\bresistance\b', r'\bdrop\b']

CONFIDENCE_BOOSTERS = [r'\bdefinitely\b', r'\bcertainly\b', r'\bclearly\b',
                        r'\bobvious\b', r'\bstrong\b', r'\bconfirmed\b']
CONFIDENCE_REDUCERS = [r'\bmaybe\b', r'\bperhaps\b', r'\blooks\s*like\b',
                        r'\bpossibly\b', r'\bmight\b', r'\bcould\b', r'\bseems\b']

# Timeframe keywords
TIMEFRAME_MAP = {
    '1m': '1-minute', '5m': '5-minute', '15m': '15-minute',
    '1h': '1-hour', '4h': '4-hour', 'h4': '4-hour',
    '1d': 'daily', 'daily': 'daily', 'day': 'daily',
    '1w': 'weekly', 'weekly': 'weekly', 'week': 'weekly',
    '1M': 'monthly', 'monthly': 'monthly',
}


def classify_text(text: str) -> dict:
    """Classify a single text for EW sentiment.
    
    Returns clean internal data — no social references.
    """
    text_lower = text.lower()
    
    # Extract timeframe
    timeframe = '4-hour'  # default
    for tf_key, tf_name in TIMEFRAME_MAP.items():
        if tf_key in text_lower:
            timeframe = tf_name
            break
    
    # Check for explicit timeframe phrases
    if 'daily' in text_lower or '1 day' in text_lower or '1d' in text_lower:
        timeframe = 'daily'
    elif 'weekly' in text_lower or '1 week' in text_lower:
        timeframe = 'weekly'
    
    # Pattern matching with scoring
    best_pattern = None
    best_score = 0
    matched_wave = None
    
    for pattern_name, pattern_data in EW_PATTERNS.items():
        score = sum(2 if re.search(p, text_lower) else 0 for p in pattern_data['regex'])
        if score > best_score:
            best_score = score
            best_pattern = pattern_name
            matched_wave = pattern_data['wave']
    
    # Also check general sentiment
    gen_bull = sum(1 for p in GENERAL_BULLISH if re.search(p, text_lower))
    gen_bear = sum(1 for p in GENERAL_BEARISH if re.search(p, text_lower))
    
    # Determine direction
    if best_pattern:
        direction = EW_PATTERNS[best_pattern]['direction']
        structure = EW_PATTERNS[best_pattern]['structure']
        wave = matched_wave
        raw_conf = min(0.80, 0.55 + best_score * 0.05)
    elif gen_bull > gen_bear:
        direction = 'UP'
        structure = 'unknown'
        wave = None
        raw_conf = 0.55
    elif gen_bear > gen_bull:
        direction = 'DOWN'
        structure = 'unknown'
        wave = None
        raw_conf = 0.55
    else:
        direction = 'FLAT'
        structure = 'unknown'
        wave = None
        raw_conf = 0.50
    
    # Confidence modifiers
    conf_boost = sum(1 for p in CONFIDENCE_BOOSTERS if re.search(p, text_lower))
    conf_reduce = sum(1 for p in CONFIDENCE_REDUCERS if re.search(p, text_lower))
    confidence = min(0.85, max(0.50, raw_conf + conf_boost * 0.03 - conf_reduce * 0.03))
    
    return {
        'direction': direction,
        'wave': wave,
        'structure': structure,
        'timeframe': timeframe,
        'confidence': confidence,
        'text': text[:200],
    }


def aggregate_classifications(classifications: list[dict]) -> dict:
    """Aggregate classifications into a clean internal signal.
    
    No mention of sources, posts, or social data.
    """
    if not classifications:
        return {
            'direction': 'FLAT', 'confidence': 0.50, 'wave': None,
            'structure': 'unknown', 'timeframe': '4-hour',
            'count': 0, 'validation': 'no_data',
        }
    
    # Weight by confidence
    up_weight = sum(c['confidence'] for c in classifications if c['direction'] == 'UP')
    down_weight = sum(c['confidence'] for c in classifications if c['direction'] == 'DOWN')
    flat_weight = sum(c['confidence'] for c in classifications if c['direction'] == 'FLAT')
    
    total = up_weight + down_weight + flat_weight
    
    if total == 0:
        direction = 'FLAT'
        confidence = 0.50
    elif up_weight > down_weight and up_weight > flat_weight:
        direction = 'UP'
        confidence = min(0.85, 0.50 + (up_weight / total) * 0.35)
    elif down_weight > up_weight and down_weight > flat_weight:
        direction = 'DOWN'
        confidence = min(0.85, 0.50 + (down_weight / total) * 0.35)
    else:
        direction = 'FLAT'
        confidence = 0.50
    
    # Most common wave and timeframe
    waves = [c['wave'] for c in classifications if c['wave'] is not None]
    timeframes = [c['timeframe'] for c in classifications]
    
    avg_wave = None
    if waves:
        from collections import Counter
        wave_counts = Counter(waves)
        avg_wave = wave_counts.most_common(1)[0][0]
    
    # Most common structure
    structures = [c['structure'] for c in classifications]
    structure = 'impulse' if sum(1 for s in structures if s == 'impulse') > len(structures) / 2 else 'corrective'
    
    # Timeframe
    if timeframes:
        from collections import Counter
        tf_counts = Counter(timeframes)
        timeframe = tf_counts.most_common(1)[0][0]
    else:
        timeframe = '4-hour'
    
    return {
        'direction': direction,
        'confidence': confidence,
        'wave': avg_wave,
        'structure': structure,
        'timeframe': timeframe,
        'count': len(classifications),
        'validation': 'pending',  # will be set by structure validator
    }


def validate_ew_against_structure(
    ew_data: dict,
    rsi: float = 50,
    macd_signal: Optional[str] = None,
    price_vs_ma20: str = 'neutral',
    change_pct: float = 0,
) -> dict:
    """Validate EW reading against price structure.
    
    This is the core logic:
    - If EW says bullish impulse and RSI/MACD/MA confirm → high confidence
    - If EW says bullish but structure says overbought → divergence, lower confidence
    - If EW says correction and structure confirms → validated
    
    Returns a clean trader-facing analysis.
    """
    direction = ew_data.get('direction', 'FLAT')
    confidence = ew_data.get('confidence', 0.50)
    wave = ew_data.get('wave')
    structure_type = ew_data.get('structure', 'unknown')
    timeframe = ew_data.get('timeframe', '4-hour')
    
    # Build structure confirmation score (0-100)
    structure_score = 50  # neutral baseline
    
    # RSI check
    if direction == 'UP':
        if 40 <= rsi <= 65:
            structure_score += 15  # bullish RSI range
        elif rsi > 70:
            structure_score -= 20  # overbought = risk for bullish
        elif rsi < 35:
            structure_score += 10  # oversold bounce
    elif direction == 'DOWN':
        if 35 <= rsi <= 60:
            structure_score += 10  # bearish RSI range
        elif rsi < 30:
            structure_score -= 15  # oversold = bounce risk for bearish
        elif rsi > 65:
            structure_score += 15  # overbought = sell signal
    
    # MACD check
    if macd_signal:
        if direction == 'UP' and macd_signal == 'bullish':
            structure_score += 15
        elif direction == 'UP' and macd_signal == 'bearish':
            structure_score -= 15
        elif direction == 'DOWN' and macd_signal == 'bearish':
            structure_score += 15
        elif direction == 'DOWN' and macd_signal == 'bullish':
            structure_score -= 15
    
    # MA alignment check
    if direction == 'UP' and price_vs_ma20 == 'above':
        structure_score += 10
    elif direction == 'UP' and price_vs_ma20 == 'below':
        structure_score -= 10
    elif direction == 'DOWN' and price_vs_ma20 == 'below':
        structure_score += 10
    elif direction == 'DOWN' and price_vs_ma20 == 'above':
        structure_score -= 10
    
    # Price momentum check
    if direction == 'UP' and change_pct > 1:
        structure_score += 10
    elif direction == 'UP' and change_pct < -1:
        structure_score -= 10
    elif direction == 'DOWN' and change_pct < -1:
        structure_score += 10
    elif direction == 'DOWN' and change_pct > 1:
        structure_score -= 10
    
    # Clamp
    structure_score = max(0, min(100, structure_score))
    
    # Determine validation
    if structure_score >= 70:
        validation = 'confirmed'
    elif structure_score >= 55:
        validation = 'supported'
    elif structure_score >= 40:
        validation = 'neutral'
    elif structure_score >= 25:
        validation = 'divergent'
    else:
        validation = 'contrarian'
    
    # Build the EW description
    wave_names = {
        1: 'wave 1 (early impulse)',
        2: 'wave 2 (pullback)',
        3: 'wave 3 (strong impulse)',
        4: 'wave 4 (consolidation)',
        5: 'wave 5 (final extension)',
    }
    
    # Final confidence index (0-100)
    # Base from EW sentiment, adjusted by structure validation
    if validation in ('confirmed', 'supported'):
        ew_confidence = min(95, int(confidence * 100 + structure_score * 0.2))
    elif validation == 'neutral':
        ew_confidence = int(confidence * 100)
    elif validation == 'divergent':
        ew_confidence = max(20, int(confidence * 100 - 20))
    else:  # contrarian
        ew_confidence = max(15, int(confidence * 100 - 30))
    
    # Build natural language description
    if wave and wave in wave_names:
        if direction == 'UP':
            if structure_type == 'impulse':
                desc = f"Bullish impulse — currently in {wave_names[wave]} on {timeframe} timeframe"
            else:
                desc = f"Bullish retrace — potential {wave_names[wave]} on {timeframe} timeframe"
        elif direction == 'DOWN':
            if structure_type == 'corrective':
                desc = f"Corrective {wave_names[wave]} on {timeframe} timeframe"
            else:
                desc = f"Bearish impulse — {wave_names[wave]} on {timeframe} timeframe"
        else:
            desc = f"Sideways {wave_names[wave]} on {timeframe} timeframe"
    else:
        if direction == 'UP':
            desc = f"Bullish structure on {timeframe} timeframe"
        elif direction == 'DOWN':
            desc = f"Bearish structure on {timeframe} timeframe"
        else:
            desc = f"Consolidating on {timeframe} timeframe"
    
    # Validation description
    if validation == 'confirmed':
        val_desc = f"Indicators confirm the EW reading (confidence: {ew_confidence}/100)"
    elif validation == 'supported':
        val_desc = f"Indicators support the EW reading (confidence: {ew_confidence}/100)"
    elif validation == 'neutral':
        val_desc = f"Mixed signals — EW reading uncertain (confidence: {ew_confidence}/100)"
    elif validation == 'divergent':
        val_desc = f"Indicators diverge from EW reading — caution (confidence: {ew_confidence}/100)"
    else:  # contrarian
        val_desc = f"Indicators contradict EW — potential reversal (confidence: {ew_confidence}/100)"
    
    return {
        'direction': direction,
        'wave': wave,
        'timeframe': timeframe,
        'structure': structure_type,
        'description': desc,
        'validation': validation,
        'validation_desc': val_desc,
        'confidence_index': ew_confidence,  # 0-100
        'structure_score': structure_score,  # 0-100
        # Debug info (not shown to user)
        '_raw_confidence': confidence,
        '_rsi': rsi,
        '_macd': macd_signal,
    }


# ─── X/Twitter Scraper ────────────────────────────────────────

def scrape_x(ticker: str, max_results: int = 20) -> list[dict]:
    """Scrape X for EW posts about a ticker."""
    classifications = []
    queries = [
        f'{ticker} elliott wave',
        f'{ticker} EW analysis',
        f'{ticker} wave count',
    ]
    
    for query in queries:
        try:
            import subprocess
            import json as json_mod
            result = subprocess.run(
                ['xurl', 'search', query, '--max-results', str(max_results // len(queries))],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json_mod.loads(result.stdout)
                tweets = data if isinstance(data, list) else data.get('data', [])
                for tweet in tweets:
                    text = tweet.get('text', tweet.get('content', ''))
                    if text:
                        classifications.append(classify_text(text))
        except Exception as e:
            logger.debug(f"X search failed for '{query}': {e}")
    
    return classifications


# ─── Reddit Scraper ────────────────────────────────────────────

REDDIT_SUBREDDITS = [
    'BitcoinMarkets', 'ElliottWave', 'ethtrader',
    'StockMarket', 'Daytrading', 'CryptoCurrency',
]


def scrape_reddit(ticker: str, max_results: int = 20) -> list[dict]:
    """Scrape Reddit for EW posts about a ticker."""
    classifications = []
    ticker_clean = ticker.lower().replace('-usd', '').replace('usdt', '')
    
    for sub in REDDIT_SUBREDDITS:
        try:
            url = f"https://www.reddit.com/r/{sub}/search.json"
            params = {
                'q': f'{ticker_clean} elliott OR wave OR EW',
                'sort': 'new',
                'limit': min(max_results // len(REDDIT_SUBREDDITS), 10),
                't': 'day',
            }
            headers = {'User-Agent': 'StockAnalyst/1.0'}
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                posts = data.get('data', {}).get('children', [])
                for post in posts:
                    title = post.get('data', {}).get('title', '')
                    selftext = post.get('data', {}).get('selftext', '')
                    text = f"{title} {selftext}"
                    if ticker_clean in text.lower():
                        classifications.append(classify_text(text))
        except Exception as e:
            logger.debug(f"Reddit scrape failed for r/{sub}: {e}")
    
    return classifications


# ─── Main Entry Point ─────────────────────────────────────────

def get_ew_sentiment(
    ticker: str,
    rsi: float = 50,
    macd_signal: Optional[str] = None,
    price_vs_ma20: str = 'neutral',
    change_pct: float = 0,
) -> dict:
    """Get validated Elliott Wave analysis for a ticker.
    
    Returns a clean, trader-facing analysis — no social data exposed.
    """
    logger.info(f"EW Sentiment: Analyzing {ticker}...")
    
    # Scrape all sources
    x_results = scrape_x(ticker, max_results=15)
    reddit_results = scrape_reddit(ticker, max_results=15)
    all_results = x_results + reddit_results
    
    if not all_results:
        logger.info(f"EW Sentiment: No data for {ticker}")
        return {
            'direction': 'FLAT',
            'wave': None,
            'timeframe': '4-hour',
            'description': 'No clear EW pattern detected',
            'validation': 'no_data',
            'validation_desc': 'Insufficient data for EW analysis',
            'confidence_index': 0,
            'available': False,
        }
    
    # Aggregate
    aggregated = aggregate_classifications(all_results)
    logger.info(f"EW Sentiment ({ticker}): {aggregated['direction']} wave={aggregated['wave']} "
                f"tf={aggregated['timeframe']} ({aggregated['count']} sources)")
    
    # Validate against structure
    validated = validate_ew_against_structure(
        aggregated,
        rsi=rsi,
        macd_signal=macd_signal,
        price_vs_ma20=price_vs_ma20,
        change_pct=change_pct,
    )
    
    validated['available'] = True
    validated['count'] = aggregated['count']
    
    logger.info(f"EW Validated ({ticker}): {validated['description']} "
                f"confidence={validated['confidence_index']}/100 "
                f"validation={validated['validation']}")
    
    return validated