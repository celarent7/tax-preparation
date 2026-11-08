"""Tests for the MCP `compare_deductions` tool (plan CC Task 10).

The `mcp` package is not installed in this environment, so the module-level
fixture installs the same minimal stub as the plan's "MCP smoke check" before
importing scripts/mcp_server.py, and removes it afterwards.
"""

import json
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


@pytest.fixture(scope="module")
def mcp_server():
    fm = types.ModuleType("mcp.server.fastmcp")

    class FastMCP:
        def __init__(self, *a, **k): pass
        def tool(self, *a, **k): return lambda f: f
        def run(self, *a, **k): pass

    fm.FastMCP = FastMCP
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "mcp", types.ModuleType("mcp"))
        mp.setitem(sys.modules, "mcp.server", types.ModuleType("mcp.server"))
        mp.setitem(sys.modules, "mcp.server.fastmcp", fm)
        mp.delitem(sys.modules, "mcp_server", raising=False)
        import mcp_server as module
        yield module
        sys.modules.pop("mcp_server", None)


def test_compare_deductions_runs(mcp_server):
    # Pinned to 2025: the default year is 2026 (Task 1), and 31,500 is the
    # 2025 MFJ standard deduction.
    out = mcp_server.compare_deductions(150000, "mfj", year="2025")
    assert isinstance(out, str), out
    assert json.loads(out)["standard_deduction"] == 31500


def test_compare_deductions_unknown_status_is_validation_error(mcp_server):
    out = mcp_server.compare_deductions(150000, "bogus")
    assert isinstance(out, dict) and out["isError"] is True
    assert out["metadata"]["errorCategory"] == "validation"


def test_compare_deductions_property_cap_from_constants(mcp_server):
    out = mcp_server.compare_deductions(100000, "single", charitable_property=50000)
    assert isinstance(out, str), out
    # 30% of AGI via charitable_property_agi_limit.
    assert json.loads(out)["total_itemized"] == 30000.0
