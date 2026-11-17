import asyncio

from kb_gateway.server import mcp


def test_mcp_tools_registered() -> None:
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {"kb_init_guide", "kb_list", "kb_search", "kb_read"} <= names
    assert all(tool.description for tool in tools)


def test_call_kb_list_tool(monkeypatch, tmp_path) -> None:
    registry = tmp_path / "kb_registry.yaml"
    registry.write_text("version: 1\nknowledge_bases: []\n", encoding="utf-8")
    monkeypatch.setenv("KB_CONFIG", str(registry))
    result = asyncio.run(mcp.call_tool("kb_list", {"include_prefixes": False}))
    assert not result.is_error
