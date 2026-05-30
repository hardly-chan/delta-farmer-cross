from lib.models import DurationSec, TimeRange
from lib.utils import parse_duration


def test_parse_duration_supports_days():
    assert parse_duration("3d") == 259200
    assert parse_duration("1d2h30m") == 95400


def test_duration_sec_accepts_day_strings():
    assert DurationSec("4d") == 345600


def test_time_range_accepts_day_strings():
    duration = TimeRange.model_validate({"min": "3d", "max": "4d"})

    assert duration.min == 259200
    assert duration.max == 345600
