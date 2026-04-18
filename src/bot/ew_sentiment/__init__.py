"""
Elliott Wave Sentiment Engine for StockAnalyst Pro.

Scrapes social media for EW opinions, classifies sentiment,
validates against price structure, and produces a fused signal.
"""

import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ─── Sentiment Classification ─────────────────────────────────

EW_BULLISH_PATTERNS = [
    r'\bwave\s*3\b', r'\bwave\s*1\b', r'\bimpulse\s*up\b',
    r'\bbullish\s*ew\b', r'\bbullish\s*elliott\b',
    r'\bwave\s*5\s*extension\b', r'\bbreakout\b.*\bwave\b',
    r'\bup\s*impulse\b', r'\bleading\s*diagonal\b',
    r'\bending\s*diagonal\s*up\b',
]

EW_BEARISH_PATTERNS = [
    r'\bwave\s*[4c]\b', r'\bcorrecti[vo]n\b', r'\babc\b.*\bdown\b',
    r'\bbearish\s*ew\b', r'\bbearish\s*elliott\b',
    r'\bwave\s*[2b]\b.*\bretrac', r'\bzigzag\b.*\bdown\b',
    r'\bflat\s*correct', r'\btriangle\b.*\bbear\b',
    r'\bdown\s*impulse\b',
]

EW_NEUTRAL_PATTERNS = [
    r'\bsideways\b', r'\branging\b', r'\bconsolidat',
    r'\bwave\s*4\b', r'\btriangle\b.*\bneutral\b',
    r'\bflat\s*range\b',
]

WAVE_NUMBER_RE = re.compile(r'\bwave\s*(\d)\b', re.IGNORECASE)
CONFIDENCE_BOOSTERS = [r'\bdefinitely\b', r'\bcertainly\b', r'\bclearly\b', r'\bobvious\b', r'\bstrong\b']
CONFIDENCE_REDUCERS = [r'\bmaybe\b', r'\bperhaps\b', r'\blooks\s*like\b', r'\bpossibly\b', r'\bmight\b', r'\bcould\b']


def classify_text(text: str) -> dict:
    """Classify a single text for EW sentiment.
    
    Returns: {
        'direction': 'UP' | 'DOWN' | 'FLAT' | None,
        'wave_count': int | None,  # which wave number mentioned
        'confidence': float,  # 0.0-1.0
        'source_text': str,  # original text (truncated)
    }
    """
    text_lower = text.lower()
    
    # Direction scoring
    bull_score = sum(1 for p in EW_BULLISH_PATTERNS if re.search(p, text_lower))
    bear_score = sum(1 for p in EW_BEARISH_PATTERNS if re.search(p, text_lower))
    neut_score = sum(1 for p in EW_NEUTRAL_PATTERNS if re.search(p, text_lower))
    
    # Also check general sentiment if no EW patterns
    general_bull = sum(1 for w in ['bullish', 'pump', 'moon', 'long', 'buy', 'up', 'breakout']
                       if w in text_lower)
    general_bear = sum(1 for w in ['bearish', 'dump', 'short', 'sell', 'down', 'crash', 'drop']
                       if w in text_lower)
    
    bull_total = bull_score * 2 + general_bull
    bear_total = bear_score * 2 + general_bear
    neut_total = neut_score
    
    # Determine direction
    if bull_total == 0 and bear_total == 0 and neut_total == 0:
        direction = None  # No signal
        raw_conf = 0.0
    elif bull_total > bear_total and bull_total > neut_total:
        direction = 'UP'
        raw_conf = bull_total / (bull_total + bear_total + neut_total + 0.1)
    elif bear_total > bull_total and bear_total > neut_total:
        direction = 'DOWN'
        raw_conf = bear_total / (bull_total + bear_total + neut_total + 0.1)
    else:
        direction = 'FLAT'
        raw_conf = 0.5
    
    # Wave count extraction
    wave_match = WAVE_NUMBER_RE.search(text_lower)
    wave_count = int(wave_match.group(1)) if wave_match else None
    
    # Confidence modifiers
    conf_boost = sum(1 for p in CONFIDENCE_BOOSTERS if re.search(p, text_lower))
    conf_reduce = sum(1 for p in CONFIDENCE_REDUCERS if re.search(p, text_lower))
    
    confidence = min(0.85, max(0.50, raw_conf * 0.6 + 0.40 + conf_boost * 0.05 - conf_reduce * 0.05))
    
    return {
        'direction': direction,
        'wave_count': wave_count,
        'confidence': confidence,
        'source_text': text[:200],
    }


def aggregate_sentiments(classifications: list[dict]) -> dict:
    """Aggregate multiple classifications into a single signal.
    
    Returns: {
        'direction': 'UP' | 'DOWN' | 'FLAT',
        'confidence': float,
        'count': int,
        'bull_count': int,
        'bear_count': int,
        'neutral_count': int,
        'avg_wave': float | None,
        'consensus': str,  # 'strong_bull', 'bull', 'mixed', 'bear', 'strong_bear'
    }
    """
    if not classifications:
        return {
            'direction': 'FLAT', 'confidence': 0.50, 'count': 0,
            'bull_count': 0, 'bear_count': 0, 'neutral_count': 0,
            'avg_wave': None, 'consensus': 'no_data',
        }
    
    bull = [c for c in classifications if c['direction'] == 'UP']
    bear = [c for c in classifications if c['direction'] == 'DOWN']
    neut = [c for c in classifications if c['direction'] == 'FLAT']
    none_c = [c for c in classifications if c['direction'] is None]
    
    total = len(classifications)
    bull_weight = sum(c['confidence'] for c in bull) if bull else 0
    bear_weight = sum(c['confidence'] for c in bear) if bear else 0
    
    # Weighted direction
    if bull_weight > bear_weight and len(bull) > len(bear):
        direction = 'UP'
        confidence = min(0.80, 0.50 + (bull_weight / total) * 0.3)
    elif bear_weight > bull_weight and len(bear) > len(bull):
        direction = 'DOWN'
        confidence = min(0.80, 0.50 + (bear_weight / total) * 0.3)
    else:
        direction = 'FLAT'
        confidence = 0.50
    
    # Average wave count
    waves = [c['wave_count'] for c in classifications if c['wave_count'] is not None]
    avg_wave = sum(waves) / len(waves) if waves else None
    
    # Consensus label
    bull_pct = len(bull) / total if total else 0
    bear_pct = len(bear) / total if total else 0
    
    if bull_pct > 0.7:
        consensus = 'strong_bull'
    elif bull_pct > 0.5:
        consensus = 'bull'
    elif bear_pct > 0.7:
        consensus = 'strong_bear'
    elif bear_pct > 0.5:
        consensus = 'bear'
    else:
        consensus = 'mixed'
    
    return {
        'direction': direction,
        'confidence': confidence,
        'count': total,
        'bull_count': len(bull),
        'bear_count': len(bear),
        'neutral_count': len(neut) + len(none_c),
        'avg_wave': avg_wave,
        'consensus': consensus,
    }


# ─── X/Twitter Scraper ────────────────────────────────────────

def scrape_x(ticker: str, max_results: int = 20) -> list[dict]:
    """Scrape X for Elliott Wave posts about a ticker.
    
    Uses xurl CLI for X API access.
    Returns list of classified posts.
    """
    classifications = []
    
    # Search queries targeting EW content
    queries = [
        f'{ticker} elliott wave',
        f'{ticker} EW analysis',
        f'{ticker} wave count',
    ]
    
    # Try xurl search
    try:
        import subprocess
        import json as json_mod
        
        for query in queries:
            try:
                result = subprocess.run(
                    ['node', '/Users/clawbox/.openclaw/workspace/tradingview-mcp/src/cli/index.js',
                     'search', query, '--max-results', str(max_results // len(queries))],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    # xurl would return tweets — for now use xurl directly
                    pass
            except Exception:
                pass
        
        # Use xurl CLI instead
        for query in queries:
            try:
                result = subprocess.run(
                    ['xurl', 'search', query, '--max-results', str(max_results // len(queries))],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    data = json_mod.loads(result.stdout)
                    tweets = data if isinstance(data, list) else data.get('tweets', data.get('data', []))
                    for tweet in tweets:
                        text = tweet.get('text', tweet.get('content', ''))
                        if text:
                            classifications.append(classify_text(text))
            except Exception as e:
                logger.debug(f"X search failed for '{query}': {e}")
    
    except Exception as e:
        logger.warning(f"X scraping failed: {e}")
    
    return classifications


# ─── Reddit Scraper ────────────────────────────────────────────

REDDIT_SUBREDDITS = [
    'BitcoinMarkets', 'ElliottWave', 'ethtrader',
    'StockMarket', 'Daytrading', 'CryptoCurrency',
]

def scrape_reddit(ticker: str, max_results: int = 20) -> list[dict]:
    """Scrape Reddit for EW posts about a ticker."""
    classifications = []
    
    ticker_lower = ticker.lower().replace('-usd', '').replace('usdt', '')
    
    for sub in REDDIT_SUBREDDITS:
        try:
            url = f"https://www.reddit.com/r/{sub}/search.json"
            params = {
                'q': f'{ticker_lower} elliott OR wave OR EW',
                'sort': 'new',
                'limit': min(max_results // len(REDDIT_SUBREDDITS), 10),
                't': 'day',  # Last 24 hours
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
                    if ticker_lower in text.lower() or ticker.lower() in text.lower():
                        classifications.append(classify_text(text))
        except Exception as e:
            logger.debug(f"Reddit scrape failed for r/{sub}: {e}")
    
    return classifications


# ─── Structure Validator ──────────────────────────────────────

def validate_against_structure(
    sentiment: dict,
    rsi: Optional[float] = None,
    macd_signal: Optional[str] = None,
    price_vs_ma20: Optional[str] = None,  # 'above' or 'below'
    change_pct: Optional[float] = None,
) -> dict:
    """Validate crowd sentiment against price structure.
    
    Returns: {
        'direction': 'UP' | 'DOWN' | 'FLAT',
        'confidence': float,
        'validation': 'confirmed' | 'divergent' | 'neutral',
        'reason': str,
    }
    """
    sent_dir = sentiment.get('direction', 'FLAT')
    sent_conf = sentiment.get('confidence', 0.50)
    
    # Build structure signals
    structure_signals = []
    
    if rsi is not None:
        if rsi > 70:
            structure_signals.append('overbought')  # Bearish signal
        elif rsi < 30:
            structure_signals.append('oversold')  # Bullish signal
    
    if macd_signal:
        structure_signals.append(f'macd_{macd_signal}')
    
    if price_vs_ma20:
        structure_signals.append(f'price_{price_vs_ma20}_ma20')
    
    # Check for divergence
    structure_dir = 'FLAT'
    if rsi is not None:
        if rsi < 30:
            structure_dir = 'UP'
        elif rsi > 70:
            structure_dir = 'DOWN'
    
    if macd_signal == 'bullish':
        structure_dir = 'UP' if structure_dir != 'DOWN' else 'FLAT'
    elif macd_signal == 'bearish':
        structure_dir = 'DOWN' if structure_dir != 'UP' else 'FLAT'
    
    # Divergence detection
    if sent_dir == 'UP' and structure_dir == 'DOWN':
        validation = 'divergent'
        reason = f"Crowd bullish but structure bearish (RSI={rsi}, {macd_signal})"
        confidence = max(0.50, sent_conf * 0.6)  # Reduce confidence on divergence
    elif sent_dir == 'DOWN' and structure_dir == 'UP':
        validation = 'divergent'
        reason = f"Crowd bearish but structure bullish (RSI={rsi}, {macd_signal})"
        confidence = max(0.50, sent_conf * 0.6)
    elif sent_dir == structure_dir and sent_dir != 'FLAT':
        validation = 'confirmed'
        reason = f"Sentiment confirmed by structure (RSI={rsi}, {macd_signal})"
        confidence = min(0.85, sent_conf * 1.2)  # Boost on confirmation
    else:
        validation = 'neutral'
        reason = f"No strong structure signal to validate (RSI={rsi})"
        confidence = sent_conf
    
    return {
        'direction': sent_dir if validation != 'divergent' else structure_dir,
        'confidence': confidence,
        'validation': validation,
        'reason': reason,
    }


# ─── Main Entry Point ─────────────────────────────────────────

def get_ew_sentiment(
    ticker: str,
    rsi: Optional[float] = None,
    macd_signal: Optional[str] = None,
    price_vs_ma20: Optional[str] = None,
    change_pct: Optional[float] = None,
) -> dict:
    """Get Elliott Wave sentiment signal for a ticker.
    
    Returns: {
        'direction': 'UP' | 'DOWN' | 'FLAT',
        'confidence': float,
        'sources': int,
        'consensus': str,
        'validation': str,
        'reason': str,
    }
    """
    logger.info(f"EW Sentiment: Scraping for {ticker}...")
    
    # Scrape all sources
    x_results = scrape_x(ticker, max_results=15)
    reddit_results = scrape_reddit(ticker, max_results=15)
    
    all_results = x_results + reddit_results
    
    if not all_results:
        logger.info(f"EW Sentiment: No results found for {ticker}")
        return {
            'direction': 'FLAT',
            'confidence': 0.50,
            'sources': 0,
            'consensus': 'no_data',
            'validation': 'neutral',
            'reason': 'No EW sentiment data found',
        }
    
    # Aggregate
    aggregated = aggregate_sentiments(all_results)
    logger.info(f"EW Sentiment ({ticker}): {aggregated['consensus']} "
                f"({aggregated['bull_count']}B/{aggregated['bear_count']}S/"
                f"{aggregated['neutral_count']}N) "
                f"direction={aggregated['direction']} conf={aggregated['confidence']:.1%}")
    
    # Validate against structure
    validated = validate_against_structure(
        aggregated, rsi=rsi, macd_signal=macd_signal,
        price_vs_ma20=price_vs_ma20, change_pct=change_pct,
    )
    
    return {
        'direction': validated['direction'],
        'confidence': validated['confidence'],
        'sources': aggregated['count'],
        'consensus': aggregated['consensus'],
        'validation': validated['validation'],
        'reason': validated['reason'],
        'bull_count': aggregated['bull_count'],
        'bear_count': aggregated['bear_count'],
        'avg_wave': aggregated['avg_wave'],
    }