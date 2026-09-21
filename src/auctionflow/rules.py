from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Final


def _nonnegative_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class RuleSnapshot:
    rule_id: str
    source_url: str
    source_page_date: str
    verified_at: str
    account_size_cents: int
    trailing_cushion_cents: int
    trail_basis: str
    lock_floor_cents: int
    breach_basis: str

    def __post_init__(self) -> None:
        if self.trail_basis != "EOD_BALANCE":
            raise ValueError("trail_basis must be EOD_BALANCE")
        if self.breach_basis != "REAL_TIME_EQUITY":
            raise ValueError("breach_basis must be REAL_TIME_EQUITY")
        for name in ("account_size_cents", "trailing_cushion_cents", "lock_floor_cents"):
            _nonnegative_int(getattr(self, name), name)

    @property
    def revision(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


OFFICIAL_TOPSTEP_50K_MLL: Final = RuleSnapshot(
    rule_id="TOPSTEP_50K_MLL_2026-07-01",
    source_url="https://help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit",
    source_page_date="2026-07-01",
    verified_at="2026-08-04",
    account_size_cents=5_000_000,
    trailing_cushion_cents=200_000,
    trail_basis="EOD_BALANCE",
    lock_floor_cents=5_000_000,
    breach_basis="REAL_TIME_EQUITY",
)
RULE_SNAPSHOT: Final = OFFICIAL_TOPSTEP_50K_MLL


def next_mll_floor_cents(
    previous_floor_cents: int,
    eod_balance_cents: int,
    snapshot: RuleSnapshot = OFFICIAL_TOPSTEP_50K_MLL,
) -> int:
    _nonnegative_int(previous_floor_cents, "previous floor")
    _nonnegative_int(eod_balance_cents, "EOD balance")
    if previous_floor_cents > snapshot.lock_floor_cents:
        raise ValueError("previous floor cannot exceed lock floor")
    return min(snapshot.lock_floor_cents, max(previous_floor_cents, eod_balance_cents - snapshot.trailing_cushion_cents))


def mll_breached(real_time_equity_cents: int, floor_cents: int) -> bool:
    _nonnegative_int(real_time_equity_cents, "real-time equity")
    _nonnegative_int(floor_cents, "floor")
    return real_time_equity_cents <= floor_cents


def current_mll_buffer_cents(balance_cents: int, floor_cents: int) -> int:
    _nonnegative_int(balance_cents, "balance")
    _nonnegative_int(floor_cents, "floor")
    return balance_cents - floor_cents


def update_account_eod(account, eod_balance_cents: int, snapshot: RuleSnapshot = OFFICIAL_TOPSTEP_50K_MLL):
    """Return a validated account preserving every safety field."""
    if eod_balance_cents < 0:
        raise ValueError("EOD balance must be non-negative")
    from auctionflow.domain import AccountState
    values = account.model_dump()
    values.update(balance_cents=eod_balance_cents,
                  mll_floor_cents=next_mll_floor_cents(account.mll_floor_cents, eod_balance_cents, snapshot))
    return AccountState.model_validate(values)
