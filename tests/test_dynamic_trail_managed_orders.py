from types import SimpleNamespace

import pytest

from agents._shared.routines import hyperliquid_dynamic_trail as trail


@pytest.mark.asyncio
async def test_dynamic_trail_submits_owned_order_executor(monkeypatch):
    captured = {}

    async def fake_create(client, request):
        captured["client"] = client
        captured["request"] = request
        return {"executor_id": "exec-1"}

    monkeypatch.setattr(trail, "create_managed_executor", fake_create)
    cfg = SimpleNamespace(
        account_name="master_account",
        connector="hyperliquid_perpetual",
        trading_pair="ETH-USD",
        open_order_type="MARKET",
    )

    result = await trail._submit_managed_order(
        object(), cfg, "SELL", 0.02, "CLOSE"
    )

    assert result == {"executor_id": "exec-1"}
    request = captured["request"]
    assert request.action == "create"
    assert request.executor_type == "order_executor"
    assert request.controller_id == "routine:dynamic_trail:ETH-USD"
    assert request.executor_config == {
        "connector_name": "hyperliquid_perpetual",
        "trading_pair": "ETH-USD",
        "side": 2,
        "amount": 0.02,
        "execution_strategy": "MARKET",
        "position_action": "CLOSE",
    }


@pytest.mark.asyncio
async def test_dynamic_trail_surfaces_executor_failure(monkeypatch):
    async def fake_create(client, request):
        return {"error": "risk gate denied"}

    monkeypatch.setattr(trail, "create_managed_executor", fake_create)
    cfg = SimpleNamespace(
        account_name="master_account",
        connector="hyperliquid_perpetual",
        trading_pair="ETH-USD",
        open_order_type="LIMIT",
    )

    with pytest.raises(RuntimeError, match="risk gate denied"):
        await trail._submit_managed_order(object(), cfg, "BUY", 0.02, "OPEN")
