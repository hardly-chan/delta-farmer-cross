# delta-farmer | https://github.com/vladkens/delta-farmer
import os
import time

import curl_cffi as curl

from .utils import json_dump, json_load

_URL = "https://api.github.com/repos/vladkens/delta-farmer/releases/latest"
_CACHE = ".cache/update.json"
_TTL = 24 * 60 * 60


def _version(version: str) -> tuple[int, ...]:
    version = version.strip().removeprefix("v")
    version = version.split("-", 1)[0]
    return tuple(int(x) for x in version.split(".") if x.isdigit())


async def latest_release_notice(current_version: str) -> str | None:
    if os.getenv("DF_NO_UPDATE_NOTIFIER"):
        return None

    now = int(time.time())
    data = json_load(_CACHE) or {}
    if now - data.get("ts", 0) > _TTL:
        try:
            async with curl.AsyncSession() as s:
                res = await s.get(_URL, timeout=5)
                release = res.json()
                data = {
                    "ts": now,
                    "tag": release.get("tag_name"),
                    "url": release.get("html_url"),
                }
        except Exception:
            data = {"ts": now}
        json_dump(_CACHE, data)

    tag, url = data.get("tag"), data.get("url")
    if isinstance(tag, str) and isinstance(url, str) and _version(tag) > _version(current_version):
        return f":: update available: {current_version.strip()} -> {tag} | {url}"
    return None
