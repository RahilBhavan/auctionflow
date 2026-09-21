from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OHLCVBar(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    timestamp: datetime
    symbol: str = Field(default="MES", pattern=r"^MES$")
    contract_id: str | None = None
    sequence: int = Field(ge=0)
    open_ticks: int
    high_ticks: int
    low_ticks: int
    close_ticks: int
    volume: int = Field(gt=0)

    @model_validator(mode="after")
    def valid_bar(self):
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() != UTC.utcoffset(self.timestamp):
            raise ValueError("timestamp must be UTC")
        if self.timestamp.second or self.timestamp.microsecond:
            raise ValueError("timestamp must be minute start")
        if not (
            self.low_ticks <= self.open_ticks <= self.high_ticks
            and self.low_ticks <= self.close_ticks <= self.high_ticks
        ):
            raise ValueError("invalid OHLC geometry")
        return self

    @property
    def et_date(self):
        from zoneinfo import ZoneInfo
        return self.timestamp.astimezone(ZoneInfo("America/New_York")).date()
