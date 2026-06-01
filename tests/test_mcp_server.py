"""
In-memory tests for platform-docs MCP server.

These tests run BEFORE cloud deployment to validate logic locally.
They use FastMCP's in-memory Client for zero-latency testing.

Run with:
    uv run pytest tests/test_mcp_server.py -v
"""
import pytest
from fastmcp import Client
from src.platform_docs.server import mcp


@pytest.mark.asyncio
async def test_server_ping():
    """Test basic server connectivity."""
    async with Client(mcp) as client:
        result = await client.ping()
        assert result is True, "Server should respond to ping"


@pytest.mark.asyncio
async def test_list_tools():
    """Test that all expected tools are registered."""
    async with Client(mcp) as client:
        tools = await client.list_tools()
        tool_names = [tool.name for tool in tools]

        assert len(tools) == 2, f"Expected 2 tools, found {len(tools)}"
        assert "search_docs" in tool_names, "search_docs tool should be registered"
        assert "list_sources" in tool_names, "list_sources tool should be registered"


@pytest.mark.asyncio
async def test_list_sources():
    """Test the list_sources tool returns expected documentation sources."""
    async with Client(mcp) as client:
        result = await client.call_tool("list_sources", {})
        content = result.content[0].text

        # Validate expected sources are listed
        expected_sources = [
            "Anthropic", "LangChain", "Prefect", "FastMCP",
            "PydanticAI", "Zep", "McpProtocol", "Temporal"
        ]
        for source in expected_sources:
            assert source in content, f"{source} source should be listed"

        # Validate TOTAL count is shown
        assert "TOTAL" in content, "Total count should be shown"


@pytest.mark.asyncio
async def test_search_docs_basic():
    """Test basic semantic search functionality."""
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_docs",
            {"query": "FastMCP deployment", "k": 3}
        )
        content = result.content[0].text

        # Validate results format
        assert "Found 3 results" in content, "Should return exactly 3 results"
        assert "Title:" in content, "Should include result titles"
        assert "Source:" in content, "Should include source names"
        assert "Content Preview" in content, "Should include content preview"


@pytest.mark.asyncio
async def test_search_docs_with_source_filter():
    """Test source filtering functionality."""
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_docs",
            {"query": "authentication", "source": "Anthropic", "k": 2}
        )
        content = result.content[0].text

        # Should return a response (either results or no results message)
        assert len(content) > 0, "Should return a response"


@pytest.mark.asyncio
async def test_search_docs_k_parameter_validation():
    """Test k parameter validation and clamping."""
    async with Client(mcp) as client:
        # Test with k > 20 (should clamp to 20)
        result = await client.call_tool(
            "search_docs",
            {"query": "test query", "k": 100}
        )
        content = result.content[0].text
        assert "Found" in content or "No results" in content, "Should handle large k values"


@pytest.mark.asyncio
async def test_error_handling():
    """Test error handling for invalid inputs."""
    async with Client(mcp) as client:
        # Test with empty query string
        result = await client.call_tool(
            "search_docs",
            {"query": "", "k": 5}
        )
        content = result.content[0].text
        assert len(content) > 0, "Should return a response for empty query"


@pytest.mark.asyncio
async def test_search_performance_baseline():
    """Establish performance baseline for in-memory tests."""
    import time

    async with Client(mcp) as client:
        start = time.time()

        result = await client.call_tool(
            "search_docs",
            {"query": "test performance", "k": 5}
        )

        elapsed = time.time() - start
        assert elapsed < 10.0, f"Search took {elapsed:.2f}s, expected < 10s"
        print(f"\n⏱️  Search performance: {elapsed:.3f}s")


if __name__ == "__main__":
    import asyncio

    async def run_all_tests():
        """Run all tests manually for debugging."""
        print("🧪 Running platform-docs in-memory tests...\n")

        tests = [
            ("Server Ping", test_server_ping()),
            ("List Tools", test_list_tools()),
            ("List Sources", test_list_sources()),
            ("Basic Search", test_search_docs_basic()),
            ("Source Filter", test_search_docs_with_source_filter()),
            ("K Parameter Validation", test_search_docs_k_parameter_validation()),
            ("Error Handling", test_error_handling()),
            ("Performance Baseline", test_search_performance_baseline()),
        ]

        passed = 0
        failed = 0

        for name, test_coro in tests:
            try:
                await test_coro
                print(f"✅ {name}")
                passed += 1
            except Exception as e:
                print(f"❌ {name}: {e}")
                failed += 1

        print(f"\n{'='*60}")
        print(f"Results: {passed} passed, {failed} failed")
        print(f"{'='*60}")

    asyncio.run(run_all_tests())
