"""FastAPI app: paper BTC sim, WebSocket dashboard, SQLite persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from bot import __version__ as BOT_VERSION
from bot.monitor import (
    MarketSnapshot,
    fetch_series_events,
    fetch_yes_no_prices,
    market_snapshot_for_slug,
    pick_current_market,
)
from bot.paper import (
    OpenLeg,
    PaperPortfolio,
    paper_line,
    setup_paper_logger,
    stake_for_level,
)
from bot.storage import create_storage
from bot.strategy import ENTRY, PaperStrategy, TAKE_PROFIT, entry_max_price

# httpx иногда режется CDN/прокси на PaaS; trust_env=False — не брать HTTP(S)_PROXY из окружения
HTTPX_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PaperBtcSim/1.2; +https://github.com/m1cch/paper-btc-sim) "
        "httpx"
    ),
    "Accept": "application/json",
}
HTTPX_TIMEOUT = httpx.Timeout(45.0, connect=20.0)

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# В Docker: смонтируйте том и задайте DATA_DIR=/data — SQLite и trades.log переживут рестарт
DATA_ROOT = Path(os.getenv("DATA_DIR", str(ROOT))).resolve()

STATE_KEY = "paper_bot_v1"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")


def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v is not None and v != "" else default


def listen_port() -> int:
    """Render/Railway/Fly задают PORT; локально — DASHBOARD_PORT."""
    p = os.getenv("PORT")
    if p is not None and str(p).strip() != "":
        return int(p)
    return env_int("DASHBOARD_PORT", 8080)


def env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    return float(v) if v is not None and v != "" else default


def env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class BotRuntime:
    def __init__(self) -> None:
        self.initial = env_float("INITIAL_DEPOSIT", 300.0)
        # Опрос midpoints; нижняя граница 20 мс (ограничение — сеть и API)
        self.refresh = max(0.02, env_float("PRICE_REFRESH_SECONDS", 0.08))
        self.series_id = env_int("GAMMA_SERIES_ID", 10192)
        # Список событий Gamma тяжёлый — кэшируем, midpoints качаем каждый тик
        self.gamma_cache_ttl = max(0.2, env_float("GAMMA_REFRESH_SECONDS", 2.0))
        self.persist_debounce = max(0.05, env_float("PERSIST_DEBOUNCE_SECONDS", 0.22))
        self.db_price_min_interval = max(
            0.0, env_float("DB_PRICE_SAMPLE_MIN_SECONDS", 0.05)
        )
        self._events_cache: list[dict[str, Any]] = []
        self._events_cache_ts: float = 0.0
        self._last_sec_left: Optional[float] = None
        self._last_persist_ts: float = 0.0
        self._last_db_price_ts: float = 0.0
        # Подтверждение смены slug (сек), иначе Gamma «мигает» → ложный market_switch + мгновенные сделки
        self.slug_confirm_seconds = max(0.15, env_float("SLUG_CHANGE_CONFIRM_SECONDS", 1.5))
        self._market_committed: Optional[MarketSnapshot] = None
        self._slug_pending_slug: Optional[str] = None
        self._slug_pending_since: float = 0.0
        self.min_end = env_int("MIN_SECONDS_TO_END", 30)
        self.db_path = DATA_ROOT / "db" / "trades.db"
        self.log_path = DATA_ROOT / "trades.log"
        self.storage = create_storage(self.db_path)
        self.paper_logger = setup_paper_logger(str(self.log_path))
        self.portfolio = PaperPortfolio(
            self.initial, self.paper_logger, on_trade=self._on_trade_closed
        )
        self.strategy = PaperStrategy(self.portfolio, self.paper_logger)
        self.cycle_num = 1
        self.current_slug: Optional[str] = None
        self.low_balance_warned = False
        self.wins = 0
        self.losses = 0
        self.markers: deque = deque(maxlen=120)
        self.ui_log: deque[str] = deque(maxlen=200)
        self._price_points_max = max(2000, min(100_000, env_int("PRICE_POINTS_MAX", 15_000)))
        self._chart_snapshot_points = max(
            120,
            min(self._price_points_max, env_int("CHART_SNAPSHOT_POINTS", 6_000)),
        )
        self.price_points: deque = deque(maxlen=self._price_points_max)
        # Увеличивается при api_reset — клиент сбрасывает накопленную историю графика
        self.chart_history_seq: int = 0
        self._tick_diag: str = ""
        self._last_good_market: Optional[MarketSnapshot] = None
        self._last_good_yes: Optional[float] = None
        self._last_good_no: Optional[float] = None
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.ws_clients: set[WebSocket] = set()
        self._last_snapshot: dict[str, Any] = {}
        # False = только котировки, без автоматического входа (см. AUTO_TRADE в .env)
        self.auto_trade = env_bool("AUTO_TRADE", False)
        self._load_state()

    def _load_state(self) -> None:
        w, l = self.storage.win_loss_counts()
        self.wins, self.losses = w, l
        raw = self.storage.get_kv(STATE_KEY)
        if not raw:
            return
        try:
            if "auto_trade" in raw:
                self.auto_trade = bool(raw["auto_trade"])
            self.cycle_num = int(raw.get("cycle_num", 1))
            self.current_slug = raw.get("current_slug")
            self.low_balance_warned = bool(raw.get("low_balance_warned", False))
            self.portfolio.balance = float(raw.get("balance", self.initial))
            self.strategy.skip_rest_of_window = bool(
                raw.get("skip_rest_of_window", False)
            )
            leg = raw.get("open_leg")
            if leg:
                self.portfolio.open_leg = OpenLeg(
                    side=leg["side"],
                    reversal_level=int(leg["reversal_level"]),
                    shares=float(leg["shares"]),
                    cost_usd=float(leg["cost_usd"]),
                    entry_price=float(leg["entry_price"]),
                )
            self.portfolio.realized_pnl_window = float(
                raw.get("realized_pnl_window", 0.0)
            )
            self.strategy.entries_armed = bool(raw.get("entries_armed", True))
            self.strategy.scalp_used_this_window = bool(
                raw.get("scalp_used_this_window", False)
            )
            self.strategy._entry_gate_until = 0.0
            self.strategy._entry_confirm_side = None
            self.strategy._entry_confirm_count = 0
        except (KeyError, TypeError, ValueError) as e:
            log.warning("state restore failed: %s", e)

    def _state_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "cycle_num": self.cycle_num,
            "current_slug": self.current_slug,
            "auto_trade": self.auto_trade,
            "balance": self.portfolio.balance,
            "low_balance_warned": self.low_balance_warned,
            "wins": self.wins,
            "losses": self.losses,
            "skip_rest_of_window": self.strategy.skip_rest_of_window,
            "realized_pnl_window": self.portfolio.realized_pnl_window,
            "entries_armed": self.strategy.entries_armed,
            "scalp_used_this_window": self.strategy.scalp_used_this_window,
        }
        if self.portfolio.open_leg:
            leg = self.portfolio.open_leg
            d["open_leg"] = {
                "side": leg.side,
                "reversal_level": leg.reversal_level,
                "shares": leg.shares,
                "cost_usd": leg.cost_usd,
                "entry_price": leg.entry_price,
            }
        return d

    def _persist(self) -> None:
        self.storage.set_kv(STATE_KEY, self._state_dict())

    async def _get_events_cached(
        self, client: httpx.AsyncClient, sec_left_hint: Optional[float]
    ) -> list[dict[str, Any]]:
        now = time.time()
        ttl = self.gamma_cache_ttl
        if sec_left_hint is not None and sec_left_hint < 75.0:
            ttl = min(ttl, 0.45)
        if self._events_cache and (now - self._events_cache_ts) < ttl:
            return self._events_cache
        ev = await fetch_series_events(client, self.series_id, limit=100)
        self._events_cache = ev
        self._events_cache_ts = now
        return ev

    def _slug_debounce_confirmed(self, candidate: str) -> bool:
        """Стабильный slug не менялся ~slug_confirm_seconds — подтверждение смены окна."""
        now = time.time()
        if self._slug_pending_slug != candidate:
            self._slug_pending_slug = candidate
            self._slug_pending_since = now
            return False
        return (now - self._slug_pending_since) >= self.slug_confirm_seconds

    def _on_trade_closed(self, rec: dict[str, Any]) -> None:
        self.storage.add_trade(
            cycle_num=rec["cycle_num"],
            market_slug=rec.get("market_slug"),
            side=rec["side"],
            entry_price=rec.get("entry_price"),
            exit_price=rec.get("exit_price"),
            stake=rec["stake"],
            pnl=rec["pnl"],
            kind=rec["kind"],
            created_at=rec["created_at"],
        )
        pnl = float(rec["pnl"])
        if pnl > 1e-9:
            self.wins += 1
        elif pnl < -1e-9:
            self.losses += 1

    def _check_low_balance(self) -> None:
        if self.portfolio.balance >= 50.0:
            return
        if self.low_balance_warned:
            return
        self.low_balance_warned = True
        paper_line(
            self.paper_logger,
            f"Внимание: виртуальный баланс ниже $50 ({self.portfolio.balance:.2f}$)",
        )

    async def broadcast(self) -> None:
        if not self.ws_clients:
            return
        payload = jsonable_encoder(self._last_snapshot)
        dead: list[WebSocket] = []
        for ws in self.ws_clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ws_clients.discard(ws)

    def build_snapshot(
        self,
        market: Optional[MarketSnapshot],
        yes: Optional[float],
        no: Optional[float],
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        profit = self.portfolio.balance - self.initial
        closed = self.wins + self.losses
        winrate = (self.wins / closed * 100.0) if closed > 0 else 0.0

        seconds_left = 0.0
        if market:
            seconds_left = max(0.0, (market.end_date - now).total_seconds())

        leg = self.portfolio.open_leg
        position_block: dict[str, Any] = {
            "has": leg is not None,
            "skip": self.strategy.skip_rest_of_window,
            "side": leg.side if leg else None,
            "reversal": f"{leg.reversal_level}/4" if leg else None,
            "reversal_level": leg.reversal_level if leg else None,
            "entry": leg.entry_price if leg else None,
            "shares": round(leg.shares, 6) if leg else None,
            "stake": stake_for_level(leg.reversal_level) if leg else None,
            "unrealized_pnl": None,
            "realized_cycle": round(self.portfolio.realized_pnl_window, 2),
            "progress_tp": None,
            "current_price": None,
        }
        if leg and yes is not None and no is not None:
            px = yes if leg.side == "yes" else no
            position_block["current_price"] = round(px, 4)
            position_block["unrealized_pnl"] = round(
                leg.shares * px - leg.cost_usd, 2
            )
            if TAKE_PROFIT > leg.entry_price:
                position_block["progress_tp"] = min(
                    1.0,
                    max(0.0, (px - leg.entry_price) / (TAKE_PROFIT - leg.entry_price)),
                )

        status_text = "Ожидание входа"
        if self.strategy.skip_rest_of_window:
            status_text = "Пропуск до следующего окна"
        elif leg:
            status_text = f"В позиции {leg.side.upper()}"
        elif not self.auto_trade:
            status_text = "Только котировки (автоторговля выкл.)"
        elif not self.strategy.entries_armed:
            status_text = "Тейк взят — ждём новое 15m окно"

        ws_ts = None
        we_ts = None
        if market:
            ws_ts = market.event_start.timestamp()
            we_ts = market.end_date.timestamp()

        snap: dict[str, Any] = {
            "version": BOT_VERSION,
            "ts": now.timestamp(),
            "server_time_ms": int(time.time() * 1000),
            "poll_interval_ms": int(round(self.refresh * 1000)),
            "paper_mode": True,
            "auto_trade": self.auto_trade,
            "entries_armed": self.strategy.entries_armed,
            "balance": round(self.portfolio.balance, 2),
            "profit": round(profit, 2),
            "cycles": self.cycle_num,
            "winrate": round(winrate, 1),
            "market_title": market.title if market else "—",
            "market_slug": market.slug if market else None,
            "window_ends_at": market.end_date.isoformat() if market else None,
            "window_started_at": market.event_start.isoformat() if market else None,
            "window_start_ts": ws_ts,
            "window_end_ts": we_ts,
            "yes": yes,
            "no": no,
            "seconds_left": seconds_left,
            "position": position_block,
            "status_text": status_text,
            "thresholds": {
                "entry": ENTRY,
                "entry_max": entry_max_price(),
                "tp": 0.9,
                "sl": 0.4,
            },
            "help": (
                "При «Авто: ВКЛ» вход от 0,60 до лимита (ENTRY_MAX_PRICE в .env, по умолчанию 0,70$); "
                "разворот при 0,40 не покупает противоположную сторону дороже того же лимита (иначе только выход, окно пропускается). "
                "После arm — пауза и подтверждение тиков (ENTRY_DELAY_AFTER_ARM_SECONDS, ENTRY_CONFIRM_TICKS). "
                "Тейк 0,90 — TAKE_PROFIT_CONFIRM_TICKS подряд тиков. После TP — без входов до следующего окна."
            ),
            "chart_history_seq": self.chart_history_seq,
            "price_history": list(self.price_points)[-self._chart_snapshot_points :],
            "markers": list(self.markers)[-80:],
            "stats": self.storage.trade_stats(),
            "event_log": [str(x) for x in self.ui_log],
            "recent_trades": self.storage.recent_trades(30),
            "tick_diag": self._tick_diag,
        }
        if market is not None and yes is not None and no is not None:
            self._last_good_market = market
            self._last_good_yes = yes
            self._last_good_no = no
        self._last_snapshot = snap
        return snap

    async def _loop(self) -> None:
        limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
        async with httpx.AsyncClient(
            limits=limits,
            headers=HTTPX_DEFAULT_HEADERS,
            timeout=HTTPX_TIMEOUT,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            while self._running:
                async with self._lock:
                    try:
                        events = await self._get_events_cached(
                            client, self._last_sec_left
                        )
                        m = pick_current_market(events)
                        if m is None and self._events_cache:
                            self._events_cache_ts = 0.0
                            events = await fetch_series_events(
                                client, self.series_id, limit=100
                            )
                            self._events_cache = events
                            self._events_cache_ts = time.time()
                            m = pick_current_market(events)
                        yes: Optional[float] = None
                        no: Optional[float] = None
                        extra: list[str] = []
                        slug_changed = False
                        if m:
                            m_pick = m
                            now = datetime.now(timezone.utc)
                            m_work: Optional[MarketSnapshot] = None

                            if self.current_slug and m_pick.slug == self.current_slug:
                                self._slug_pending_slug = None
                                self._market_committed = m_pick

                            if self.current_slug is None:
                                first_ready = self._slug_debounce_confirmed(m_pick.slug)
                                switch_ready = False
                            elif m_pick.slug != self.current_slug:
                                switch_ready = self._slug_debounce_confirmed(m_pick.slug)
                                first_ready = False
                            else:
                                switch_ready = False
                                first_ready = False

                            if switch_ready:
                                yes_sw, no_sw = await fetch_yes_no_prices(
                                    client, m_pick
                                )
                                if self.current_slug is not None:
                                    extra.extend(
                                        self.strategy.on_market_change(
                                            self.current_slug,
                                            m_pick,
                                            yes_sw or 0.0,
                                            no_sw or 0.0,
                                            self.cycle_num,
                                        )
                                    )
                                    self.cycle_num += 1
                                    self.markers.clear()
                                    self.portfolio.realized_pnl_window = 0.0
                                    for line in extra:
                                        self.ui_log.append(line)
                                self.current_slug = m_pick.slug
                                self._market_committed = m_pick
                                self.strategy.reset_skip()
                                self.strategy.arm_for_new_window()
                                self.ui_log.append(
                                    "Новое 15m окно — до тейк-профита снова разрешены входы (при «Авто: ВКЛ»)."
                                )
                                slug_changed = True
                                self._slug_pending_slug = None
                                yes, no = yes_sw, no_sw
                                m_work = m_pick
                            elif first_ready:
                                self.current_slug = m_pick.slug
                                self._market_committed = m_pick
                                self.strategy.reset_skip()
                                self.strategy.arm_for_new_window()
                                self.ui_log.append(
                                    "Рынок привязан — до тейк-профита разрешены входы (при «Авто: ВКЛ»)."
                                )
                                slug_changed = True
                                self._slug_pending_slug = None
                                m_work = m_pick
                                yes, no = await fetch_yes_no_prices(client, m_work)
                            else:
                                if self.current_slug is None:
                                    m_work = m_pick
                                elif m_pick.slug == self.current_slug:
                                    m_work = m_pick
                                else:
                                    m_work = self._market_committed
                                    if m_work is None or m_work.slug != self.current_slug:
                                        m_work = market_snapshot_for_slug(
                                            events, self.current_slug
                                        )
                                    if m_work is None:
                                        m_work = m_pick
                                yes, no = await fetch_yes_no_prices(client, m_work)

                            sec_left = max(0.0, (m_work.end_date - now).total_seconds())
                            self._last_sec_left = sec_left

                            if yes is not None and no is not None:
                                ts = now.timestamp()
                                self.price_points.append(
                                    {"ts": ts, "yes": yes, "no": no}
                                )
                                if (
                                    self.db_price_min_interval <= 0
                                    or ts - self._last_db_price_ts
                                    >= self.db_price_min_interval
                                ):
                                    self.storage.add_price_sample(ts, yes, no)
                                    self._last_db_price_ts = ts

                            tr = self.strategy.tick(
                                m_work,
                                yes,
                                no,
                                self.cycle_num,
                                sec_left,
                                float(self.min_end),
                                allow_new_entries=(
                                    self.auto_trade
                                    and self.strategy.entries_armed
                                    and self.current_slug is not None
                                ),
                            )
                            for mk in tr.markers:
                                self.markers.append(mk)
                            for line in tr.log_ui:
                                self.ui_log.append(line)

                            self._check_low_balance()
                            now_mono = time.time()
                            must_persist = (
                                bool(extra)
                                or bool(tr.log_ui)
                                or bool(tr.markers)
                                or slug_changed
                                or (now_mono - self._last_persist_ts)
                                >= self.persist_debounce
                            )
                            if must_persist:
                                self._persist()
                                self._last_persist_ts = now_mono
                            if yes is None or no is None:
                                self._tick_diag = (
                                    "CLOB: нет mid (сеть/таймаут) — проверь логи Render"
                                )
                            else:
                                self._tick_diag = ""
                            self.build_snapshot(m_work, yes, no)
                        else:
                            self._last_sec_left = None
                            self._tick_diag = (
                                f"Нет активного 15m окна в Gamma (событий: {len(events)})"
                            )
                            self.build_snapshot(None, None, None)
                            now_mono = time.time()
                            if now_mono - self._last_persist_ts >= self.persist_debounce:
                                self._persist()
                                self._last_persist_ts = now_mono
                    except Exception as e:
                        log.exception("bot tick: %s", e)
                        err_msg = f"Ошибка: {e}"
                        self.ui_log.append(err_msg)
                        self._tick_diag = err_msg[:500]
                        self.build_snapshot(
                            self._last_good_market,
                            self._last_good_yes,
                            self._last_good_no,
                        )

                await self.broadcast()
                await asyncio.sleep(self.refresh)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop_task(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def api_stop(self) -> bool:
        async with self._lock:
            m = None
            y2: Optional[float] = None
            n2: Optional[float] = None
            async with httpx.AsyncClient() as hc:
                try:
                    ev = await fetch_series_events(hc, self.series_id)
                    m = pick_current_market(ev)
                    if m:
                        y2, n2 = await fetch_yes_no_prices(hc, m)
                except Exception as e:
                    log.warning("stop price fetch: %s", e)
            ok = self.strategy.user_stop(
                y2, n2, self.cycle_num, self.current_slug
            )
            if ok:
                self.ui_log.append(
                    "STOP: позиция закрыта; авто-входы до следующего 15m окна отключены"
                )
            self._persist()
            self.build_snapshot(m, y2, n2)
        await self.broadcast()
        return ok

    def _reset_paper_logger(self) -> None:
        for h in list(self.paper_logger.handlers):
            try:
                h.close()
            except Exception:
                pass
            self.paper_logger.removeHandler(h)
        try:
            self.log_path.write_text("", encoding="utf-8")
        except OSError as e:
            log.warning("trades.log truncate: %s", e)
        self.paper_logger = setup_paper_logger(str(self.log_path))
        self.portfolio.log = self.paper_logger
        self.strategy.log = self.paper_logger

    async def api_reset(self) -> None:
        async with self._lock:
            self.portfolio.balance = self.initial
            self.portfolio.open_leg = None
            self.portfolio.realized_pnl_window = 0.0
            self.strategy.skip_rest_of_window = False
            self.strategy.reset_skip()
            self.strategy.entries_armed = True
            self.strategy.scalp_used_this_window = False
            self.strategy._entry_gate_until = 0.0
            self.strategy._entry_confirm_side = None
            self.strategy._entry_confirm_count = 0
            self.cycle_num = 1
            self.current_slug = None
            self.low_balance_warned = False
            self.wins = 0
            self.losses = 0
            self.markers.clear()
            self.ui_log.clear()
            self.price_points.clear()
            self.chart_history_seq += 1
            self._events_cache.clear()
            self._events_cache_ts = 0.0
            self._last_sec_left = None
            self._market_committed = None
            self._slug_pending_slug = None
            self._slug_pending_since = 0.0
            self.storage.clear_all_data()
            self._reset_paper_logger()
            self._persist()
            paper_line(
                self.paper_logger,
                f"RESET: состояние обнулено, баланс {self.initial:.2f}$",
            )
            self.build_snapshot(None, None, None)
        await self.broadcast()


runtime = BotRuntime()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await runtime.start()
    yield
    await runtime.stop_task()


app = FastAPI(title="Paper BTC Sim", lifespan=lifespan)

_cors_origins = os.getenv("CORS_ORIGINS", "").strip()
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.mount("/assets", StaticFiles(directory=str(ROOT / "dashboard")), name="assets")


@app.get("/api-config.js")
async def api_config_js():
    return FileResponse(
        str(ROOT / "dashboard" / "api-config.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@app.get("/")
async def index():
    return FileResponse(
        str(ROOT / "dashboard" / "index.html"),
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@app.post("/api/stop")
async def api_stop():
    ok = await runtime.api_stop()
    return {"ok": ok}


@app.post("/api/reset")
async def api_reset():
    await runtime.api_reset()
    return {"ok": True}


@app.post("/api/auto_trade")
async def api_auto_trade(body: dict[str, Any]):
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        return {"ok": False, "error": 'JSON: {"enabled": true} или {"enabled": false}'}
    async with runtime._lock:
        runtime.auto_trade = enabled
        runtime._persist()
        runtime.ui_log.append(
            f"Автоторговля: {'ВКЛ' if enabled else 'ВЫКЛ'} (новые входы только при ≥0,60$)"
        )
        if runtime._last_snapshot:
            runtime._last_snapshot["auto_trade"] = enabled
            runtime._last_snapshot["entries_armed"] = runtime.strategy.entries_armed
    await runtime.broadcast()
    return {"ok": True, "enabled": enabled}


@app.get("/api/health")
async def api_health():
    return {
        "ok": True,
        "name": "paper-btc-sim",
        "version": BOT_VERSION,
    }


@app.get("/api/state")
async def api_state():
    snap = runtime._last_snapshot or runtime.build_snapshot(None, None, None)
    return jsonable_encoder(snap)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    runtime.ws_clients.add(ws)
    try:
        snap = runtime._last_snapshot or runtime.build_snapshot(None, None, None)
        await ws.send_json(jsonable_encoder(snap))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        runtime.ws_clients.discard(ws)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=listen_port(),
        reload=False,
    )
