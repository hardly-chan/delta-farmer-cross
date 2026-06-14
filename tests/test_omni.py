from decimal import Decimal

from clients.omni import OmniClient, OmniCompetitionStatus, OmniSupportedAsset, _volume_field_total


async def test_omni_rwa_commodity_instrument_payload():
    client = object.__new__(OmniClient)

    async def supported_assets():
        return {
            "CL": OmniSupportedAsset(
                asset="CL",
                has_perp=True,
                instrument_type="perpetual_rwa_future",
                asset_class="commodity",
            )
        }

    client.supported_assets = supported_assets  # type: ignore[method-assign]

    assert await client._instrument("CL") == {
        "underlying": "CL",
        "settlement_asset": "USDC",
        "instrument_type": "perpetual_rwa_future",
        "kind": "commodity",
    }


async def test_omni_crypto_instrument_payload_keeps_legacy_perp_format():
    client = object.__new__(OmniClient)

    async def supported_assets():
        return {
            "BTC": OmniSupportedAsset(
                asset="BTC",
                has_perp=True,
                instrument_type="perpetual_future",
            )
        }

    client.supported_assets = supported_assets  # type: ignore[method-assign]

    assert await client._instrument("BTC") == {
        "underlying": "BTC",
        "funding_interval_s": 3600,
        "settlement_asset": "USDC",
        "instrument_type": "perpetual_future",
    }


def test_omni_competition_status_parses_unregistered_response():
    status = OmniCompetitionStatus(
        **{
            "start_time": "2026-05-29T00:00:00Z",
            "end_time": "2026-06-12T00:00:00Z",
            "volume_threshold": "250000",
            "ongoing": True,
            "user": None,
        }
    )

    assert status.ongoing is True
    assert status.volume_threshold == 250000
    assert status.user is None


def test_omni_competition_status_parses_registered_response():
    status = OmniCompetitionStatus(
        **{
            "start_time": "2026-05-29T00:00:00Z",
            "end_time": "2026-06-12T00:00:00Z",
            "volume_threshold": "250000",
            "ongoing": True,
            "user": {
                "leaderboard_name": None,
                "volume_total": "1234.5",
                "volume_rank": 10,
                "pnl_total": "-1.25",
                "pnl_rank": 20,
                "roi_total": "-0.10",
                "roi_rank": 30,
            },
        }
    )

    assert status.user is not None
    assert status.user.volume_total == Decimal("1234.5")
    assert status.user.pnl_total == Decimal("-1.25")
    assert status.user.roi_rank == 30


def test_omni_volume_field_total_parses_referral_shapes():
    assert _volume_field_total({"current": "907128.910420", "goal": "1000000"}) == Decimal(
        "907128.910420"
    )
    assert _volume_field_total({"total": "2006120.351582", "last_24h": "0"}) == Decimal(
        "2006120.351582"
    )
