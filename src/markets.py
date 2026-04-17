"""
Pre-market snapshot module — uses yfinance since Yahoo's raw endpoint now 401s.
"""

from __future__ import annotations

import logging
from typing import Dict, Tuple

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

log = logging.getLogger(__name__)


def _last_and_pct(symbol: str) -> Tuple[float | None, float | None]:
    if not YFINANCE_AVAILABLE:
        return None, None
    try:
        t = yf.Ticker(symbol)
        fi = getattr(t, "fast_info", None)
        if fi is not None:
            last = fi.get("last_price") if hasattr(fi, "get") else getattr(fi, "last_price", None)
            prev = fi.get("previous_close") if hasattr(fi, "get") else getattr(fi, "previous_close", None)
            if last is not None and prev not in (None, 0):
                pct = ((last - prev) / prev) * 100.0
                return float(last), float(pct)
        hist = t.history(period="2d", auto_adjust=False)
        if len(hist) >= 2:
            last = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2])
            pct = ((last - prev) / prev) * 100.0 if prev else None
            return last, pct
        if len(hist) == 1:
            return float(hist["Close"].iloc[-1]), None
    except Exception as e:
        log.warning("yfinance failed for %s: %s", symbol, e)
    return None, None


def _fmt(symbol: str) -> str:
    price, pct = _last_and_pct(symbol)
    if price is None:
        return "n/a"
    if pct is None:
        return f"{price:,.2f}"
    direction = "▲" if pct >= 0 else "▼"
    return f"{price:,.2f} {direction} {pct:+.2f}%"


def get_market_snapshot() -> Dict[str, str]:
    if not YFINANCE_AVAILABLE:
        log.warning("yfinance not installed — returning empty market snapshot")
        return {"equity_futures": "", "movers": "", "commodities": "",
                "yields": "", "fx": "", "crypto": ""}

    futures = f"S&P: {_fmt('ES=F')}; Nasdaq: {_fmt('NQ=F')}; Dow: {_fmt('YM=F')}"
    commodities = f"WTI: {_fmt('CL=F')}; Gold: {_fmt('GC=F')}"
    yields = f"10Y: {_fmt('^TNX')}"
    fx = f"DXY: {_fmt('DX-Y.NYB')}; EUR/USD: {_fmt('EURUSD=X')}"
    crypto = f"BTC: {_fmt('BTC-USD')}; ETH: {_fmt('ETH-USD')}"

    return {
        "equity_futures": futures,
        "movers": "",
        "commodities": commodities,
        "yields": yields,
        "fx": fx,
        "crypto": crypto,
    }