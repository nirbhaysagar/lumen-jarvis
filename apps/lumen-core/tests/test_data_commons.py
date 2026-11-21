import pytest
import os
from pathlib import Path
import pandas as pd
from src.tools.data_commons import DataCommonsTool
from src.analytics.engine import AnalystEngine
from src.tools.kernel import PythonKernelManager
from src.runtime.session import ConversationSession
from src.router.agent_loop import AGENT_TOOLS, LumenAgentLoop


def test_data_commons_tool_schema():
    """Verify data_commons tool is registered in AGENT_TOOLS."""
    dc_tool = next((t for t in AGENT_TOOLS if t["function"]["name"] == "data_commons"), None)
    assert dc_tool is not None, "data_commons tool definition missing from AGENT_TOOLS"
    fn = dc_tool["function"]
    assert "query" in fn["parameters"]["properties"]
    assert "required" in fn["parameters"] and "query" in fn["parameters"]["required"]


def test_data_commons_place_resolution():
    """Verify place name to DCID resolution."""
    tool = DataCommonsTool()
    places = ["United States", "Germany", "India"]
    resolved = tool.resolve_places(places)
    assert isinstance(resolved, dict)
    assert resolved.get("United States") in ["country/USA", "geoId/00"]
    assert resolved.get("Germany") == "country/DEU"
    assert resolved.get("India") == "country/IND"


def test_data_commons_indicator_resolution():
    """Verify semantic indicator discovery."""
    tool = DataCommonsTool()
    vars_found = tool.resolve_variables(["fertility rate"])
    assert len(vars_found) > 0
    first_var = vars_found[0]
    assert "dcid" in first_var
    assert "Fertility" in first_var["dcid"] or "Birth" in first_var["dcid"]


def test_data_commons_ground_query_live():
    """Verify end-to-end data grounding and CSV generation."""
    tool = DataCommonsTool()
    query = "Compare carbon emissions per capita in United States, Germany, and India"
    res = tool.ground_query(query)

    assert res["success"] is True
    assert res["rows"] > 50
    assert Path(res["csv_path"]).exists()
    assert "country/USA" in res["entities"].values() or "country/DEU" in res["entities"].values()
    assert len(res["provenances"]) > 0
    assert any("worldbank" in p.lower() or "http" in p for p in res["provenances"])

    # Verify CSV file is valid and readable
    df = pd.read_csv(res["csv_path"])
    assert not df.empty
    assert "value" in df.columns
    assert "date" in df.columns


@pytest.mark.asyncio
async def test_analyst_engine_grounding_via_data_commons():
    """Verify AnalystEngine Layer 1 grounds queries through Data Commons and plots."""
    km = PythonKernelManager(timeout_seconds=20.0)
    engine = AnalystEngine(kernel=km)

    tokens = []
    async for token in engine.analyze_and_plot(
        prompt="Compare carbon emissions per capita in United States and Germany",
        preview=False,
    ):
        tokens.append(token)

    full_output = "".join(tokens)
    assert "Google Data Commons Grounding" in full_output
    assert "Verified empirical time-series dataset resolved" in full_output
    assert "Archiving Reproducible Dossier to Obsidian Vault" in full_output

    # Check generated files in vault/analytics and vault/charts
    analytics_files = sorted(
        Path("vault/analytics").glob("*carbon_emissions*.md"),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )
    assert len(analytics_files) >= 1
    dossier_text = analytics_files[0].read_text()
    assert "datacommons.org" in dossier_text or "worldbank" in dossier_text.lower()
    assert "Executed Python Code" in dossier_text

    await km.stop()
