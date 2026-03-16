# delta-farmer | https://github.com/vladkens/delta-farmer
# Copyright (c) vladkens | MIT License | Sleep is overrated anyway
import sys
import tomllib
import warnings
from typing import Type, TypeVar

from pydantic import BaseModel, Field, ValidationError, model_validator

from lib.models import DurationSec, SizeRange, TgConfig, TimeRange

ConfigT = TypeVar("ConfigT", bound=BaseModel)


class StrategyConfig(BaseModel):
    """Base config for trading strategies."""

    symbols: list[str] = Field(..., min_length=1)
    symbols_per_trade: int = Field(1, gt=0, le=4)
    leverage: int = Field(10, gt=0, lt=50)
    trade_size_usd: SizeRange | None = None
    trade_size_pct: float | None = Field(None, ge=0.01, le=1.0)
    trade_duration: TimeRange
    trade_cooldown: TimeRange
    trade_heartbeat: DurationSec = DurationSec("15s")
    position_roi_limit: float = Field(0.8, gt=0, lt=1)
    combined_roi_limit: float = Field(0.1, gt=0, lt=1)
    use_limit: bool = False
    limit_wait: DurationSec = DurationSec("90s")
    limit_market_fallback: bool = True
    first_as_main: bool = False
    group_size: int | None = Field(None, ge=2, le=5)
    regroup_interval: DurationSec | None = None
    telegram: TgConfig = Field(default_factory=lambda: TgConfig())

    @model_validator(mode="before")
    @classmethod
    def _before(cls, values):
        if isinstance(values, dict):
            if "symbols" in values and "markets" in values:
                raise ValueError("Use `symbols` only; replace legacy `markets` with `symbols`")
            if "markets" in values:
                warnings.warn("`markets` is deprecated, use `symbols` instead")
                values["symbols"] = values.pop("markets")
        return values


def load_config(config_cls: Type[ConfigT], filepath: str) -> ConfigT:
    """Load and validate a Pydantic config from a TOML file with user-friendly errors."""
    try:
        with open(filepath, "rb") as fp:
            obj = tomllib.load(fp)
    except FileNotFoundError:
        raise SystemExit(f"❌ Config file not found: {filepath}")
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"❌ Invalid TOML syntax in {filepath}: {e}")

    try:
        return config_cls.model_validate(obj)
    except ValidationError as e:
        print(f"❌ Config validation failed for {filepath}\n", file=sys.stderr)
        errors = []
        for err in e.errors():
            field = ".".join(str(x) for x in err["loc"])
            msg = err["msg"]
            errors.append(f"  • {field}: {msg}")
        print("\n".join(errors), file=sys.stderr)
        print(f"\n💡 Fix the errors above in {filepath}", file=sys.stderr)
        raise SystemExit(1)
