import asyncio

from mcp_servers.hummingbot_api.tools.market_data import get_prices


class FakeMarketData:
    def __init__(self):
        self.connector_name = None

    async def get_prices(self, connector_name, trading_pairs):
        self.connector_name = connector_name
        return {"prices": {trading_pairs[0]: 100.0}, "timestamp": 0}


class FakeClient:
    def __init__(self):
        self.market_data = FakeMarketData()


def test_get_prices_normalizes_hyperliquid_connector_alias():
    client = FakeClient()

    result = asyncio.run(get_prices(client, "hyperliquid", ["BTC-USD"]))

    assert client.market_data.connector_name == "hyperliquid_perpetual"
    assert result["connector_name"] == "hyperliquid"