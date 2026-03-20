"""Resolve current BTC 15m Up/Down market and fetch midpoints from public Gamma/CLOB HTTP APIs."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
CLOB_MIDPOINT_URL = "https://clob.polymarket.com/midpoint"
# На части хостингов короткий таймаут даёт постоянные null mid
CLOB_TIMEOUT_SECONDS = 12.0


@dataclass
class MarketSnapshot:
    slug: str
    condition_id: str
    title: str
    token_yes: str  # Up
    token_no: str  # Down
    event_start: datetime
    end_date: datetime
    accepting_orders: bool


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def fetch_series_events(
    client: httpx.AsyncClient, series_id: int, limit: int = 80
) -> list[dict[str, Any]]:
    r = await client.get(
        GAMMA_EVENTS_URL,
        params={
            "series_id": series_id,
            "active": "true",
            "closed": "false",
            "limit": limit,
        },
        timeout=30.0,
    )
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def _market_from_event(event: dict[str, Any]) -> Optional[dict[str, Any]]:
    markets = event.get("markets") or []
    if not markets:
        return None
    return markets[0]


def pick_current_market(
    events: list[dict[str, Any]], now: Optional[datetime] = None
) -> Optional[MarketSnapshot]:
    """Select the active 15m window: eventStart <= now < endDate."""
    now = now or datetime.now(timezone.utc)
    prefix = "btc-updown-15m-"
    candidates: list[tuple[datetime, MarketSnapshot]] = []

    for event in events:
        slug = event.get("slug") or ""
        if not slug.startswith(prefix):
            continue
        m = _market_from_event(event)
        if not m:
            continue
        est = _parse_dt(m.get("eventStartTime") or event.get("eventStartTime"))
        end = _parse_dt(m.get("endDate") or event.get("endDate"))
        if not est or not end:
            continue
        if est <= now < end:
            try:
                token_ids = json.loads(m.get("clobTokenIds") or "[]")
            except json.JSONDecodeError:
                continue
            if len(token_ids) < 2:
                continue
            # btc-updown: Up, Down — token order matches API
            token_yes = str(token_ids[0])
            token_no = str(token_ids[1])
            snap = MarketSnapshot(
                slug=slug,
                condition_id=str(m.get("conditionId") or ""),
                title=str(m.get("question") or event.get("title") or slug),
                token_yes=token_yes,
                token_no=token_no,
                event_start=est,
                end_date=end,
                accepting_orders=bool(m.get("acceptingOrders", True)),
            )
            candidates.append((est, snap))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def market_snapshot_for_slug(
    events: list[dict[str, Any]], slug: str, now: Optional[datetime] = None
) -> Optional[MarketSnapshot]:
    """Активное окно с точным slug (тот же парсинг, что у pick_current_market)."""
    now = now or datetime.now(timezone.utc)
    for event in events:
        if (event.get("slug") or "") != slug:
            continue
        m = _market_from_event(event)
        if not m:
            continue
        est = _parse_dt(m.get("eventStartTime") or event.get("eventStartTime"))
        end = _parse_dt(m.get("endDate") or event.get("endDate"))
        if not est or not end:
            continue
        if est <= now < end:
            try:
                token_ids = json.loads(m.get("clobTokenIds") or "[]")
            except json.JSONDecodeError:
                continue
            if len(token_ids) < 2:
                continue
            token_yes = str(token_ids[0])
            token_no = str(token_ids[1])
            return MarketSnapshot(
                slug=slug,
                condition_id=str(m.get("conditionId") or ""),
                title=str(m.get("question") or event.get("title") or slug),
                token_yes=token_yes,
                token_no=token_no,
                event_start=est,
                end_date=end,
                accepting_orders=bool(m.get("acceptingOrders", True)),
            )
    return None


async def get_midpoint(client: httpx.AsyncClient, token_id: str) -> Optional[float]:
    try:
        r = await client.get(
            CLOB_MIDPOINT_URL,
            params={"token_id": token_id},
            timeout=CLOB_TIMEOUT_SECONDS,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        mid = data.get("mid")
        if mid is None:
            return None
        return float(mid)
    except (httpx.HTTPError, ValueError, TypeError) as e:
        logger.debug("midpoint error for %s: %s", token_id[:16], e)
        return None


async def fetch_yes_no_prices(
    client: httpx.AsyncClient, market: MarketSnapshot
) -> tuple[Optional[float], Optional[float]]:
    yes_p, no_p = await asyncio.gather(
        get_midpoint(client, market.token_yes),
        get_midpoint(client, market.token_no),
    )
    return yes_p, no_p
