from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_telegram():
    with patch("lib.telegram.send", new_callable=AsyncMock) as m:
        yield m
