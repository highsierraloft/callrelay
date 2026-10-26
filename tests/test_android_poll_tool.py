import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from app.mcp_tools import register_tools


async def test_android_poll_name_reuses_wait_contract_and_preserves_legacy_tool():
    class Service:
        async def wait_for_call_event(self, call_id, *, after_sequence, timeout_seconds):
            assert call_id == "call_test" and after_sequence == 7 and timeout_seconds == 20
            return {
                "terminal": False,
                "state": "active",
                "next_after_sequence": 7,
                "timed_out": True,
            }

    mcp = FastMCP("poll-contract")
    register_tools(mcp, lambda: Service(), android_backend=True)
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
        assert {"phone_call_poll", "wait_for_call_event"} <= names
        args = {"call_id": "call_test", "after_sequence": 7, "timeout_seconds": 20}
        alias = await client.call_tool("phone_call_poll", args)
        legacy = await client.call_tool("wait_for_call_event", args)
        assert alias.data == legacy.data
        assert not alias.is_error and alias.data["timed_out"] and not alias.data["terminal"]
        with pytest.raises(ToolError):
            await client.call_tool("phone_call_poll", {**args, "timeout_seconds": 0})


async def test_upstream_tool_list_does_not_gain_android_alias():
    mcp = FastMCP("upstream-contract")
    register_tools(mcp, lambda: None)
    assert "phone_call_poll" not in {t.name for t in await mcp.list_tools()}
