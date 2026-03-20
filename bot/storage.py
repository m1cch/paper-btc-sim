"""SQLite persistence for bot state, trades, and recent price samples."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Collection, Optional

# В БД пишется каждая закрытая нога; для дашборда эти строки не показываем (промежуточный выход перед новым входом).
KINDS_HIDDEN_FROM_RECENT_TABLE = frozenset({"stop_reversal_exit"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_num INTEGER NOT NULL,
    market_slug TEXT,
    side TEXT NOT NULL,
    entry_price REAL,
    exit_price REAL,
    stake REAL NOT NULL,
    pnl REAL NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_samples (
    ts REAL PRIMARY KEY,
    yes_price REAL NOT NULL,
    no_price REAL NOT NULL
);
"""


class Storage:
    def __init__(self, db_path: Path):
        self._path = db_path
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def get_kv(self, key: str, default: Any = None) -> Any:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value FROM bot_kv WHERE key = ?", (key,)
                ).fetchone()
                if not row:
                    return default
                return json.loads(row[0])

    def set_kv(self, key: str, value: Any) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO bot_kv (key, value) VALUES (?, ?)",
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
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO trades (cycle_num, market_slug, side, entry_price, exit_price, stake, pnl, kind, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                )
                conn.commit()
                return int(cur.lastrowid)

    def recent_trades(
        self,
        limit: int = 50,
        *,
        exclude_kinds: Optional[Collection[str]] = None,
    ) -> list[dict[str, Any]]:
        """Список закрытых сделок. По умолчанию скрывает промежуточные выходы по стопу разворота."""
        if exclude_kinds is not None:
            exclude = frozenset(exclude_kinds)
        else:
            exclude = KINDS_HIDDEN_FROM_RECENT_TABLE
        with self._lock:
            with self._connect() as conn:
                if exclude:
                    ph = ",".join("?" * len(exclude))
                    rows = conn.execute(
                        f"""
                        SELECT * FROM trades
                        WHERE kind NOT IN ({ph})
                        ORDER BY created_at DESC, id DESC
                        LIMIT ?
                        """,
                        (*tuple(exclude), limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM trades
                        ORDER BY created_at DESC, id DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                return [dict(r) for r in rows]

    def add_price_sample(self, ts: float, yes: float, no: float) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO price_samples (ts, yes_price, no_price) VALUES (?, ?, ?)",
                    (ts, yes, no),
                )
                conn.execute(
                    "DELETE FROM price_samples WHERE ts < ?",
                    (ts - 900.0,),
                )
                conn.commit()

    def load_price_samples_since(self, since_ts: float) -> list[dict[str, float]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT ts, yes_price, no_price FROM price_samples WHERE ts >= ? ORDER BY ts ASC",
                    (since_ts,),
                ).fetchall()
                return [
                    {"ts": r["ts"], "yes": r["yes_price"], "no": r["no_price"]}
                    for r in rows
                ]

    def clear_all_data(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM trades")
                conn.execute("DELETE FROM price_samples")
                conn.execute("DELETE FROM bot_kv")
                conn.commit()

    def win_loss_counts(self) -> tuple[int, int]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT
                      COALESCE(SUM(CASE WHEN pnl > 0.000001 THEN 1 ELSE 0 END), 0),
                      COALESCE(SUM(CASE WHEN pnl < -0.000001 THEN 1 ELSE 0 END), 0)
                    FROM trades
                    """
                ).fetchone()
                if not row:
                    return 0, 0
                return int(row[0]), int(row[1])

    def trade_stats(self) -> dict[str, Any]:
        """Сводка по закрытым сделкам в БД (открытая нога в trades не попадает)."""
        with self._lock:
            with self._connect() as conn:
                total = conn.execute(
                    "SELECT COUNT(*) FROM trades"
                ).fetchone()[0]
                rev = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE kind = ?",
                    ("stop_reversal_exit",),
                ).fetchone()[0]
                tp = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE kind = ?",
                    ("take_profit",),
                ).fetchone()[0]
                msw = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE kind = ?",
                    ("market_switch",),
                ).fetchone()[0]
                mx = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE kind = ?",
                    ("max_reversal_close",),
                ).fetchone()[0]
                ust = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE kind = ?",
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
