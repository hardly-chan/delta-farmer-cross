import time

import pytest

from lib import update


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current", "latest", "expected"),
    [
        ("v0.7.0", "v0.7.1", True),
        ("v0.7.0-abc123", "v0.7.1", True),
        ("v0.7.0-abc123", "v0.7.0", False),
        ("v0.7.1", "v0.7.0", False),
        ("v0.7.0", "v0.7.0", False),
        ("v0.7.9", "v0.8.0", True),
    ],
)
async def test_latest_release_notice_versions(tmp_path, monkeypatch, current, latest, expected):
    cache = tmp_path / "update.json"
    monkeypatch.setattr(update, "_CACHE", str(cache))
    update.json_dump(
        str(cache),
        {
            "ts": int(time.time()),
            "tag": latest,
            "url": f"https://github.com/vladkens/delta-farmer/releases/tag/{latest}",
        },
    )

    notice = await update.latest_release_notice(current)

    if expected:
        assert notice == (
            f":: update available: {current} -> {latest} | "
            f"https://github.com/vladkens/delta-farmer/releases/tag/{latest}"
        )
    else:
        assert notice is None


@pytest.mark.asyncio
async def test_latest_release_notice_can_be_disabled(tmp_path, monkeypatch):
    cache = tmp_path / "update.json"
    monkeypatch.setattr(update, "_CACHE", str(cache))
    monkeypatch.setenv("DF_NO_UPDATE_NOTIFIER", "1")
    update.json_dump(
        str(cache),
        {
            "ts": int(time.time()),
            "tag": "v0.7.1",
            "url": "https://github.com/vladkens/delta-farmer/releases/tag/v0.7.1",
        },
    )

    assert await update.latest_release_notice("v0.7.0") is None
