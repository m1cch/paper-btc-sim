"""PostgreSQL backend: те же таблицы, что у SQLite — данные переживают деплой на PaaS."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Collection, Optional

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

_PG_EXCLUDE_DEFAULT = frozenset({"stop_reversal_exit"})

PG_DDL = [
    """
CREATE TABLE IF NOT EXISTS bot_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
""",
    """
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    cycle_num INTEGER NOT NULL,
    market_slug TEXT,
    side TEXT NOT NULL,
    entry_price DOUBLE PRECISION,
    exit_price DOUBLE PRECISION,
    stake DOUBLE PRECISION NOT NULL,
    pnl DOUBLE PRECISION NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL
)
""",
    """
CREATE TABLE IF NOT EXISTS price_samples (
    ts DOUBLE PRECISION PRIMARY KEY,
    yes_price DOUBLE PRECISION NOT NULL,
    no_price DOUBLE PRECISION NOT NULL
)
""",
]


class PostgresStorage:
    def __init__(self, conninfo: str):
        self._conninfo = conninfo
        self._lock = threading.Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            with psycopg.connect(self._conninfo) as conn:
                for ddl in PG_DDL:
                    conn.execute(ddl)
                conn.commit()

    def get_kv(self, key: str, default: Any = None) -> Any:
        with self._lock:
            with psycopg.connect(self._conninfo, row_factory=dict_row) as conn:
                row = conn.execute(
                    "SELECT value FROM bot_kv WHERE key = %s", (key,)
                ).fetchone()
                if not row:
                    return default
                return json.loads(row["value"])

    def set_kv(self, key: str, value: Any) -> None:
        with self._lock:
            with psycopg.connect(self._conninfo) as conn:
                conn.execute(
                    """
                    INSERT INTO bot_kv (key, value) VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                    """,
                    (key, json.dumps(value)),
                )
                conn.commit()

    def add_trade(
        self,
        cycle_num: int,
        market_slug: Optional[str],
        side: str,
        entry_price: Optional[float],
        exit_price: Optional[float],
        stake: float,
        pnl: float,
        kind: str,
        created_at: str,
    ) -> int:
        with self._lock:
            with psycopg.connect(self._conninfo) as conn:
                row = conn.execute(
                    """
                    INSERT INTO trades (cycle_num, market_slug, side, entry_price, exit_price, stake, pnl, kind, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        cycle_num,
                        market_slug,
                        side,
                        entry_price,
                        exit_price,
                        stake,
                        pnl,
                        kind,
                        created_at,
                    ),
                ).fetchone()
                conn.commit()
                return int(row[0]) if row else 0

    def recent_trades(
        self,
        limit: int = 50,
        *,
        exclude_kinds: Optional[Collection[str]] = None,
    ) -> list[dict[str, Any]]:
        if exclude_kinds is not None:
            exclude = frozenset(exclude_kinds)
        else:
            exclude = _PG_EXCLUDE_DEFAULT
        with self._lock:
            with psycopg.connect(self._conninfo, row_factory=dict_row) as conn:
                if exclude:
                    ph = ",".join(["%s"] * len(exclude))
                    rows = conn.execute(
                        f"""
                        SELECT * FROM trades
                        WHERE kind NOT IN ({ph})
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """,
                        (*tuple(exclude), limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM trades
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """,
                        (limit,),
                    ).fetchall()
                return [dict(r) for r in rows]

    def add_price_sample(self, ts: float, yes: float, no: float) -> None:
        with self._lock:
            with psycopg.connect(self._conninfo) as conn:
                conn.execute(
                    """
                    INSERT INTO price_samples (ts, yes_price, no_price) VALUES (%s, %s, %s)
                    ON CONFLICT (ts) DO UPDATE SET yes_price = EXCLUDED.yes_price, no_price = EXCLUDED.no_price
                    """,
                    (ts, yes, no),
                )
                conn.execute(
                    "DELETE FROM price_samples WHERE ts < %s",
                    (ts - 900.0,),
                )
                conn.commit()

    def load_price_samples_since(self, since_ts: float) -> list[dict[str, float]]:
        with self._lock:
            with psycopg.connect(self._conninfo, row_factory=dict_row) as conn:
                rows = conn.execute(
                    "SELECT ts, yes_price, no_price FROM price_samples WHERE ts >= %s ORDER BY ts ASC",
                    (since_ts,),
                ).fetchall()
                return [
                    {"ts": r["ts"], "yes": r["yes_price"], "no": r["no_price"]}
                    for r in rows
                ]

    def clear_all_data(self) -> None:
        with self._lock:
            with psycopg.connect(self._conninfo) as conn:
                conn.execute("DELETE FROM trades")
                conn.execute("DELETE FROM price_samples")
                conn.execute("DELETE FROM bot_kv")
                conn.commit()

    def win_loss_counts(self) -> tuple[int, int]:
        with self._lock:
            with psycopg.connect(self._conninfo) as conn:
                row = conn.execute(
                    """
                    SELECT
                      COALESCE(SUM(CASE WHEN pnl > 0.000001 THEN 1 ELSE 0 END), 0)::bigint,
                      COALESCE(SUM(CASE WHEN pnl < -0.000001 THEN 1 ELSE 0 END), 0)::bigint
                    FROM trades
                    """
                ).fetchone()
                if not row:
                    return 0, 0
                return int(row[0]), int(row[1])

    def trade_stats(self) -> dict[str, Any]:
        with self._lock:
            with psycopg.connect(self._conninfo) as conn:
                total = conn.execute(
                    "SELECT COUNT(*) FROM trades"
                ).fetchone()[0]
                rev = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE kind = %s",
                    ("stop_reversal_exit",),
                ).fetchone()[0]
                tp = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE kind = %s",
                    ("take_profit",),
                ).fetchone()[0]
                msw = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE kind = %s",
                    ("market_switch",),
                ).fetchone()[0]
                mx = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE kind = %s",
                    ("max_reversal_close",),
                ).fetchone()[0]
                ust = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE kind = %s",
                    ("user_stop",),
                ).fetchone()[0]
                row = conn.execute(
                    "SELECT COALESCE(SUM(pnl), 0) FROM trades"
                ).fetchone()
                sum_pnl = float(row[0]) if row else 0.0
                return {
                    "closed_trades_total": int(total),
                    "reversals_total": int(rev),
                    "take_profits_total": int(tp),
                    "market_switches_total": int(msw),
                    "max_sl_exits_total": int(mx),
                    "user_stops_total": int(ust),
                    "sum_closed_pnl": round(sum_pnl, 2),
                }
