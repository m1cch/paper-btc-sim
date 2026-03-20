"""Paper trading: virtual balance and trade logging with [PAPER] prefix."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional


def _base_stake_usd() -> float:
    return float(os.getenv("BASE_STAKE_USD", "2") or "2")


def stake_for_level(level: int) -> float:
    """Ставка уровня level: base × 2**level (ровно удвоение на каждом развороте)."""
    return _base_stake_usd() * (2**level)


@dataclass
class OpenLeg:
    side: str  # "yes" | "no"
    reversal_level: int
    shares: float
    cost_usd: float  # USD spent to open this leg
    entry_price: float


def setup_paper_logger(log_path: str) -> logging.Logger:
    """File logger: every line must start with [PAPER]."""
    log = logging.getLogger("paper_trades")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    log.propagate = False
    return log


def paper_line(logger: logging.Logger, text: str) -> None:
    logger.info("[PAPER] %s", text)


def _utc_now_iso() -> str:
    """Время записи сделки в БД — с микросекундами, чтобы порядок не путался при двух выходах в одну секунду."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class PaperPortfolio:
    def __init__(
        self,
        initial: float,
        logger: logging.Logger,
        on_trade: Optional[Callable[..., None]] = None,
    ):
        self.balance = initial
        self.initial = initial
        self.log = logger
        self.on_trade = on_trade
        self.realized_pnl_window = 0.0
        self.open_leg: Optional[OpenLeg] = None

    def snapshot_leg(self) -> Optional[OpenLeg]:
        return self.open_leg

    def _emit_trade_db(
        self,
        cycle_num: int,
        slug: Optional[str],
        side: str,
        entry_p: Optional[float],
        exit_p: Optional[float],
        stake: float,
        pnl: float,
        kind: str,
    ) -> None:
        if self.on_trade:
            self.on_trade(
                {
                    "cycle_num": cycle_num,
                    "market_slug": slug,
                    "side": side,
                    "entry_price": entry_p,
                    "exit_price": exit_p,
                    "stake": stake,
                    "pnl": pnl,
                    "kind": kind,
                    "created_at": _utc_now_iso(),
                }
            )

    def buy_market(
        self,
        side: str,
        stake_usd: float,
        price: float,
        reversal_level: int,
        cycle_num: int,
        slug: Optional[str],
        note: str = "",
    ) -> bool:
        if price <= 0 or stake_usd <= 0:
            paper_line(self.log, f"skip buy: invalid price/stake {note}")
            return False
        if stake_usd > self.balance + 1e-9:
            paper_line(
                self.log,
                f"insufficient balance: need {stake_usd:.2f}$ have {self.balance:.2f}$",
            )
            return False
        shares = stake_usd / price
        self.balance -= stake_usd
        self.open_leg = OpenLeg(
            side=side,
            reversal_level=reversal_level,
            shares=shares,
            cost_usd=stake_usd,
            entry_price=price,
        )
        paper_line(
            self.log,
            f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
            f"Симуляция: куплено {shares:.4f} акций {side.upper()} @ {price:.2f}$ ({note})".strip(),
        )
        return True

    def sell_all(
        self,
        price: float,
        cycle_num: int,
        slug: Optional[str],
        kind: str,
    ) -> float:
        """Close current leg at price. Returns realized PnL for this exit."""
        if not self.open_leg or self.open_leg.shares <= 0:
            return 0.0
        leg = self.open_leg
        proceeds = leg.shares * price
        pnl = proceeds - leg.cost_usd
        self.balance += proceeds
        self.realized_pnl_window += pnl
        paper_line(
            self.log,
            f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
            f"Симуляция: продано {leg.shares:.4f} {leg.side.upper()} @ {price:.2f}$ "
            f"PnL ноги {pnl:+.2f}$ [{kind}]",
        )
        self._emit_trade_db(
            cycle_num,
            slug,
            leg.side,
            leg.entry_price,
            price,
            leg.cost_usd,
            pnl,
            kind,
        )
        self.open_leg = None
        return pnl

    def close_flat(self) -> None:
        self.open_leg = None
