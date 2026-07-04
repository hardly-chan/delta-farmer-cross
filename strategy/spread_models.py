from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from lib.models import AccountConfig, DurationSec, SizeRange, TgConfig

from .models import load_config


class SpreadConfig(BaseModel):
    """Config for two-exchange spread trading (Omni/Nado)."""

    omni: AccountConfig
    nado: AccountConfig
    symbol: str = Field(..., min_length=1)
    leverage: int = Field(10, gt=0, lt=50)

    trade_size_usd: SizeRange | None = None
    trade_size_pct: float | None = Field(None, ge=0.01, le=1.0)

    min_open_spread_pct: Decimal = Field(Decimal("0.10"), gt=0)
    min_close_spread_pct: Decimal = Field(Decimal("0.02"), ge=0)
    max_abs_pnl_usd: Decimal | None = Field(None, gt=0)
    max_abs_roi: Decimal | None = Field(None, gt=0)

    poll_interval: DurationSec = DurationSec("3s")
    position_check_interval: DurationSec = DurationSec("5s")
    min_open_time: DurationSec = DurationSec("20m")
    cooldown_after_close: DurationSec = DurationSec("1m")

    max_failures: int = Field(0, ge=0)
    telegram: TgConfig = Field(default_factory=TgConfig)

    @model_validator(mode="after")
    def _validate_sizing(self):
        if self.trade_size_usd is None and self.trade_size_pct is None:
            raise ValueError("either trade_size_usd or trade_size_pct must be specified")
        if self.trade_size_usd is not None and self.trade_size_pct is not None:
            raise ValueError("trade_size_usd and trade_size_pct are mutually exclusive")
        if self.min_close_spread_pct > self.min_open_spread_pct:
            raise ValueError("min_close_spread_pct must be <= min_open_spread_pct")
        return self

    @classmethod
    def load(cls, filepath: str) -> "SpreadConfig":
        return load_config(cls, filepath)
