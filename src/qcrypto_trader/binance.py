from __future__ import annotations

import hashlib
import hmac
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import BinanceCredentials
from .models import ApprovedOrder, MarketType, PositionSide, SymbolRule, Ticker


SPOT_REST_BASE = "https://api.binance.com"
FUTURES_REST_BASE = "https://fapi.binance.com"
SPOT_WS_BASE = "wss://stream.binance.com:9443/ws"
FUTURES_WS_BASE = "wss://fstream.binance.com/ws"


@dataclass(frozen=True)
class BinanceSigner:
    credentials: BinanceCredentials

    def signed_params(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.credentials.api_secret:
            raise RuntimeError("missing_binance_api_secret")
        payload = {**params, "timestamp": int(time.time() * 1000)}
        query = urlencode(payload)
        signature = hmac.new(
            self.credentials.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {**payload, "signature": signature}


def build_futures_leverage_params(symbol: str, leverage: int) -> dict[str, Any]:
    return {"symbol": symbol.upper(), "leverage": int(leverage)}


def build_futures_position_mode_params(hedge_mode: bool = True) -> dict[str, str]:
    return {"dualSidePosition": "true" if hedge_mode else "false"}


def build_order_params(order: ApprovedOrder) -> dict[str, Any]:
    params = order.to_binance_params()
    if order.market_type == MarketType.SPOT:
        params.pop("positionSide", None)
        params.pop("reduceOnly", None)
    elif order.position_side == PositionSide.BOTH:
        raise ValueError("futures hedge orders must use LONG or SHORT positionSide")
    return params


def order_endpoint(order: ApprovedOrder) -> str:
    if order.market_type == MarketType.SPOT:
        return "/api/v3/order"
    return "/fapi/v1/order"


def parse_spot_ticker(raw: dict[str, Any]) -> Ticker:
    return Ticker(
        symbol=str(raw["symbol"]),
        last_price=float(raw.get("lastPrice", 0) or 0),
        quote_volume=float(raw.get("quoteVolume", 0) or 0),
        price_change_percent=float(raw.get("priceChangePercent", 0) or 0),
        high_price=float(raw.get("highPrice", 0) or 0),
        low_price=float(raw.get("lowPrice", 0) or 0),
        bid_price=float(raw.get("bidPrice", 0) or 0) or None,
        ask_price=float(raw.get("askPrice", 0) or 0) or None,
    )


def parse_futures_ticker(raw: dict[str, Any]) -> Ticker:
    return Ticker(
        symbol=str(raw["symbol"]),
        last_price=float(raw.get("lastPrice", 0) or 0),
        quote_volume=float(raw.get("quoteVolume", 0) or 0),
        price_change_percent=float(raw.get("priceChangePercent", 0) or 0),
        high_price=float(raw.get("highPrice", 0) or 0),
        low_price=float(raw.get("lowPrice", 0) or 0),
    )


def parse_symbol_rules(exchange_info: dict[str, Any], futures: bool = False) -> dict[str, SymbolRule]:
    rules: dict[str, SymbolRule] = {}
    for raw_symbol in exchange_info.get("symbols", []):
        symbol = raw_symbol.get("symbol", "")
        filters = {item.get("filterType"): item for item in raw_symbol.get("filters", [])}
        lot = filters.get("LOT_SIZE", {})
        price = filters.get("PRICE_FILTER", {})
        notional = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
        max_leverage = 125 if futures else 1
        rules[symbol] = SymbolRule(
            symbol=symbol,
            min_qty=float(lot.get("minQty", 0) or 0),
            step_size=float(lot.get("stepSize", 0) or 0),
            min_notional=float(notional.get("minNotional", 0) or 0),
            tick_size=float(price.get("tickSize", 0) or 0),
            max_leverage=max_leverage,
            status=raw_symbol.get("status", "TRADING"),
        )
    return rules


class BinanceMarketData:
    def __init__(self, http_client: Any):
        self.http = http_client

    async def spot_tickers(self) -> list[Ticker]:
        response = await self.http.get(f"{SPOT_REST_BASE}/api/v3/ticker/24hr")
        response.raise_for_status()
        return [parse_spot_ticker(item) for item in response.json()]

    async def futures_tickers(self) -> list[Ticker]:
        response = await self.http.get(f"{FUTURES_REST_BASE}/fapi/v1/ticker/24hr")
        response.raise_for_status()
        return [parse_futures_ticker(item) for item in response.json()]

    async def spot_rules(self) -> dict[str, SymbolRule]:
        response = await self.http.get(f"{SPOT_REST_BASE}/api/v3/exchangeInfo")
        response.raise_for_status()
        return parse_symbol_rules(response.json(), futures=False)

    async def futures_rules(self) -> dict[str, SymbolRule]:
        response = await self.http.get(f"{FUTURES_REST_BASE}/fapi/v1/exchangeInfo")
        response.raise_for_status()
        return parse_symbol_rules(response.json(), futures=True)


class BinanceRestMarketData:
    """Small stdlib REST client for public Binance market data."""

    async def spot_tickers(self) -> list[Ticker]:
        raw = await asyncio.to_thread(_get_json, f"{SPOT_REST_BASE}/api/v3/ticker/24hr")
        return [parse_spot_ticker(item) for item in raw]

    async def futures_tickers(self) -> list[Ticker]:
        raw = await asyncio.to_thread(_get_json, f"{FUTURES_REST_BASE}/fapi/v1/ticker/24hr")
        return [parse_futures_ticker(item) for item in raw]

    async def spot_rules(self) -> dict[str, SymbolRule]:
        raw = await asyncio.to_thread(_get_json, f"{SPOT_REST_BASE}/api/v3/exchangeInfo")
        return parse_symbol_rules(raw, futures=False)

    async def futures_rules(self) -> dict[str, SymbolRule]:
        raw = await asyncio.to_thread(_get_json, f"{FUTURES_REST_BASE}/fapi/v1/exchangeInfo")
        return parse_symbol_rules(raw, futures=True)


def _get_json(url: str) -> Any:
    request = Request(url, headers={"User-Agent": "QCryptoTrader/0.1"})
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))
