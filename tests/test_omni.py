from clients.omni import OmniClient, OmniSupportedAsset


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
