"""Paper strategy: entry 0.60, TP 0.90, SL 0.40 with martingale levels 0–4."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from bot.monitor import MarketSnapshot
from bot.paper import PaperPortfolio, paper_line, stake_for_level

logger = logging.getLogger(__name__)

ENTRY = 0.60
TAKE_PROFIT = 0.90
STOP_REVERSAL = 0.40
MAX_LEVEL = 4


def entry_max_price() -> float:
    """Макс. цена для любого авто-входа (первый скальп и разворот мартингейла)."""
    v = float(os.getenv("ENTRY_MAX_PRICE", "0.70") or "0.70")
    return max(0.01, min(0.99, v))


def _tp_confirm_ticks_required() -> int:
    """Сколько подряд тиков с mid ≥ TP, чтобы зафиксировать тейк (анти-дребезг у 0.90)."""
    return max(1, int(os.getenv("TAKE_PROFIT_CONFIRM_TICKS", "2") or "2"))


def _entry_delay_after_arm() -> float:
    return max(0.0, float(os.getenv("ENTRY_DELAY_AFTER_ARM_SECONDS", "2.0") or "2.0"))


def _entry_confirm_ticks_required() -> int:
    return max(1, int(os.getenv("ENTRY_CONFIRM_TICKS", "3") or "3"))


def _midpoint_sum_sane(yes: float, no: float) -> bool:
    """Бинарный рынок: сумма mid обычно ~1. Иначе — шум API, не входим."""
    s = yes + no
    lo = float(os.getenv("MID_SUM_MIN", "0.93") or "0.93")
    hi = float(os.getenv("MID_SUM_MAX", "1.07") or "1.07")
    return lo <= s <= hi


@dataclass
class TickResult:
    markers: list[dict[str, Any]]
    log_ui: list[str]


class PaperStrategy:
    def __init__(self, portfolio: PaperPortfolio, paper_logger: logging.Logger):
        self.p = portfolio
        self.log = paper_logger
        self.skip_rest_of_window = False
        # После тейк-профита False до смены 15m окна (новый slug)
        self.entries_armed = True
        # Анти-спайк: не входить в тот же момент, что и arm; ждать N подряд тиков с тем же сигналом
        self._entry_gate_until: float = 0.0
        self._entry_confirm_side: Optional[str] = None
        self._entry_confirm_count: int = 0
        # Один автоматический вход (уровень 0) на одно 15m окно — без повторных «скальпов»
        self.scalp_used_this_window: bool = False
        self._tp_confirm_count: int = 0

    def arm_for_new_window(self) -> None:
        """Начало нового 15m окна — снова можно искать вход (если включён глобальный авто)."""
        self.entries_armed = True
        self.scalp_used_this_window = False
        self._entry_gate_until = time.time() + _entry_delay_after_arm()
        self._entry_confirm_side = None
        self._entry_confirm_count = 0
        self._tp_confirm_count = 0

    def reset_skip(self) -> None:
        self.skip_rest_of_window = False

    def on_market_change(
        self,
        old_slug: Optional[str],
        new_market: MarketSnapshot,
        yes: float,
        no: float,
        cycle_num: int,
    ) -> list[str]:
        """Close open position at current mids; reset skip; log switch."""
        lines: list[str] = []
        if self.p.open_leg:
            leg = self.p.open_leg
            px = yes if leg.side == "yes" else no
            self.p.sell_all(px, cycle_num, old_slug, "market_switch")
            lines.append(f"Позиция закрыта при смене окна @ {px:.2f}$")
        self.skip_rest_of_window = False
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        msg = f"[{ts}] Рынок: {new_market.title[:80]}"
        paper_line(self.log, msg)
        lines.append(msg)
        return lines

    def user_stop(
        self,
        yes: Optional[float],
        no: Optional[float],
        cycle_num: int,
        slug: Optional[str],
    ) -> bool:
        if not self.p.open_leg:
            return False
        fy = 0.5 if yes is None else yes
        fn = 0.5 if no is None else no
        leg = self.p.open_leg
        px = fy if leg.side == "yes" else fn
        self.p.sell_all(px, cycle_num, slug, "user_stop")
        self.entries_armed = False
        paper_line(
            self.log,
            f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] STOP: выход по {px:.2f}$ — автовходы до следующего окна выкл.",
        )
        return True

    def tick(
        self,
        market: MarketSnapshot,
        yes: Optional[float],
        no: Optional[float],
        cycle_num: int,
        seconds_to_end: float,
        min_seconds_to_end: float,
        *,
        allow_new_entries: bool = True,
    ) -> TickResult:
        markers: list[dict[str, Any]] = []
        log_ui: list[str] = []
        slug = market.slug

        if yes is None or no is None:
            return TickResult(markers, log_ui)

        ts = datetime.now(timezone.utc).timestamp()

        if self.skip_rest_of_window:
            return TickResult(markers, log_ui)

        if self.p.open_leg:
            return self._manage_position(
                market,
                yes,
                no,
                cycle_num,
                slug,
                ts,
                markers,
                log_ui,
            )

        if not allow_new_entries:
            return TickResult(markers, log_ui)

        return self._maybe_enter(
            market,
            yes,
            no,
            cycle_num,
            slug,
            seconds_to_end,
            min_seconds_to_end,
            ts,
            markers,
            log_ui,
        )

    def _maybe_enter(
        self,
        market: MarketSnapshot,
        yes: float,
        no: float,
        cycle_num: int,
        slug: str,
        seconds_to_end: float,
        min_seconds: float,
        ts: float,
        markers: list,
        log_ui: list,
    ) -> TickResult:
        if self.scalp_used_this_window:
            return TickResult(markers, log_ui)

        if seconds_to_end < min_seconds:
            return TickResult(markers, log_ui)

        if time.time() < self._entry_gate_until:
            return TickResult(markers, log_ui)

        if not _midpoint_sum_sane(yes, no):
            self._entry_confirm_side = None
            self._entry_confirm_count = 0
            return TickResult(markers, log_ui)

        y_ok = yes >= ENTRY
        n_ok = no >= ENTRY
        if not y_ok and not n_ok:
            self._entry_confirm_side = None
            self._entry_confirm_count = 0
            return TickResult(markers, log_ui)

        if y_ok and n_ok:
            side = "yes" if yes >= no else "no"
        elif y_ok:
            side = "yes"
        else:
            side = "no"
        price = yes if side == "yes" else no
        if price > entry_max_price():
            self._entry_confirm_side = None
            self._entry_confirm_count = 0
            return TickResult(markers, log_ui)

        need = _entry_confirm_ticks_required()
        if self._entry_confirm_side != side:
            self._entry_confirm_side = side
            self._entry_confirm_count = 1
        else:
            self._entry_confirm_count += 1
        if self._entry_confirm_count < need:
            return TickResult(markers, log_ui)

        stake = stake_for_level(0)

        paper_line(
            self.log,
            f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
            f"{side.upper()} достигла {price:.2f}$ → виртуальный вход",
        )
        log_ui.append(f"Вход {side.upper()} @ {price:.2f}$")

        ok = self.p.buy_market(
            side, stake, price, 0, cycle_num, slug, note="вход"
        )
        self._entry_confirm_side = None
        self._entry_confirm_count = 0
        if ok:
            self.scalp_used_this_window = True
            markers.append(
                {
                    "ts": ts,
                    "kind": "entry",
                    "side": side,
                }
            )
        return TickResult(markers, log_ui)

    def _manage_position(
        self,
        market: MarketSnapshot,
        yes: float,
        no: float,
        cycle_num: int,
        slug: str,
        ts: float,
        markers: list,
        log_ui: list,
    ) -> TickResult:
        leg = self.p.open_leg
        if not leg:
            return TickResult(markers, log_ui)
        px = yes if leg.side == "yes" else no

        if px >= TAKE_PROFIT:
            self._tp_confirm_count += 1
        else:
            self._tp_confirm_count = 0

        need_tp = _tp_confirm_ticks_required()
        if px >= TAKE_PROFIT and self._tp_confirm_count < need_tp:
            return TickResult(markers, log_ui)

        if px >= TAKE_PROFIT:
            self._tp_confirm_count = 0
            self.p.sell_all(px, cycle_num, slug, "take_profit")
            self.entries_armed = False
            paper_line(
                self.log,
                f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                f"Тейк-профит @ {px:.2f}$ — новые входы отложены до следующего 15m окна",
            )
            log_ui.append("Тейк-профит; ждём следующее 15m окно")
            markers.append({"ts": ts, "kind": "tp", "side": leg.side})
            return TickResult(markers, log_ui)

        if px <= STOP_REVERSAL:
            level = leg.reversal_level
            if level < MAX_LEVEL:
                self.p.sell_all(px, cycle_num, slug, "stop_reversal_exit")
                self._tp_confirm_count = 0
                new_level = level + 1
                new_side = "no" if leg.side == "yes" else "yes"
                new_px = no if new_side == "no" else yes
                emax = entry_max_price()
                if new_px > emax:
                    self.skip_rest_of_window = True
                    paper_line(
                        self.log,
                        f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                        f"Стоп-разворот: выход @ {px:.2f}$ — {new_side.upper()} @ {new_px:.2f}$ "
                        f"дороже лимита {emax:.2f}$; повторный вход отменён, окно пропущено",
                    )
                    log_ui.append(
                        f"Разворот отменён ({new_side.upper()} @ {new_px:.2f}$ > {emax:.2f}$)"
                    )
                    markers.append(
                        {"ts": ts, "kind": "reversal_skipped", "side": new_side}
                    )
                    return TickResult(markers, log_ui)

                stake = stake_for_level(new_level)
                paper_line(
                    self.log,
                    f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                    f"Цена {px:.2f}$ → разворот #{new_level}: покупка {new_side.upper()} на {stake:.2f}$",
                )
                log_ui.append(f"Разворот {new_level} → {new_side.upper()}")
                ok = self.p.buy_market(
                    new_side,
                    stake,
                    new_px,
                    new_level,
                    cycle_num,
                    slug,
                    note=f"разворот {new_level}",
                )
                if not ok:
                    paper_line(
                        self.log,
                        f"ОШИБКА: разворот #{new_level} не выполнен (баланс/цена). Позиция закрыта.",
                    )
                    log_ui.append("Ошибка разворота — см. лог")
                if ok:
                    markers.append(
                        {
                            "ts": ts,
                            "kind": "reversal",
                            "side": new_side,
                            "level": new_level,
                        }
                    )
                return TickResult(markers, log_ui)

            self.p.sell_all(px, cycle_num, slug, "max_reversal_close")
            self.skip_rest_of_window = True
            paper_line(
                self.log,
                f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                f"Макс. разворот: выход @ {px:.2f}$, цикл пропущен до следующего окна",
            )
            log_ui.append("Макс. разворот — пропуск окна")
            markers.append({"ts": ts, "kind": "max_sl", "side": leg.side})
            return TickResult(markers, log_ui)

        return TickResult(markers, log_ui)
