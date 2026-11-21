"""
Google Data Commons Integration for Lumen.
Interfaces with Google Data Commons Knowledge Graph (aggregating UN, World Bank,
US Census, CDC, Eurostat, NOAA, IPCC) via datacommons-client (API v2).
Provides entity resolution, variable discovery, and time-series extraction for AnalystEngine.
"""
from __future__ import annotations
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
from datacommons_client import DataCommonsClient

logger = logging.getLogger("lumen.tools.datacommons")

# Default official public trial key provided by Google Data Commons documentation
DEFAULT_DC_API_KEY = "AIzaSyCTI4Xz-UW_G2Q2RfknhcfdAnTHq5X5XuI"


class DataCommonsTool:
    """
    Wrapper around Google Data Commons API v2.
    Resolves natural language places and indicators into canonical DCIDs
    and exports clean Pandas DataFrames for sandboxed analysis and charting.
    """

    def __init__(self, api_key: Optional[str] = None, workspace_root: Optional[Path] = None):
        self.api_key = api_key or os.getenv("DC_API_KEY") or os.getenv("DATA_COMMONS_API_KEY") or DEFAULT_DC_API_KEY
        self.workspace_root = workspace_root or Path(__file__).resolve().parents[4]
        self.scratch_dir = self.workspace_root / "vault" / "scratch"
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        self._client = DataCommonsClient(api_key=self.api_key)

    def resolve_places(self, place_names: List[str]) -> Dict[str, str]:
        """
        Resolves natural language place names (e.g. 'California', 'Germany', 'India')
        into canonical Data Commons DCIDs (e.g. 'geoId/06', 'country/DEU', 'country/IND').
        """
        if not place_names:
            return {}
        try:
            res = self._client.resolve.fetch_dcids_by_name(names=place_names)
            resolved = {}
            for item in getattr(res, "entities", []):
                node = getattr(item, "node", None) or (item.get("node") if isinstance(item, dict) else None)
                candidates = getattr(item, "candidates", []) or (item.get("candidates") if isinstance(item, dict) else [])
                if node and candidates:
                    cand = candidates[0]
                    dcid = getattr(cand, "dcid", None) or (cand.get("dcid") if isinstance(cand, dict) else None)
                    if dcid:
                        resolved[node] = dcid
            return resolved
        except Exception as e:
            logger.warning(f"Data Commons place resolution failed for {place_names}: {e}")
            return {}

    def resolve_variables(self, query_terms: List[str]) -> List[Dict[str, str]]:
        """
        Discovers canonical StatisticalVariable DCIDs matching natural language concepts
        (e.g. 'carbon emissions', 'unemployment rate', 'gdp').
        """
        if not query_terms:
            return []
        try:
            res = self._client.resolve.fetch_indicators(queries=query_terms)
            variables = []
            for item in getattr(res, "entities", []):
                candidates = getattr(item, "candidates", []) or (item.get("candidates") if isinstance(item, dict) else [])
                for cand in candidates:
                    type_of = getattr(cand, "typeOf", []) or (cand.get("typeOf") if isinstance(cand, dict) else [])
                    dcid = getattr(cand, "dcid", None) or (cand.get("dcid") if isinstance(cand, dict) else None)
                    metadata = getattr(cand, "metadata", {}) or (cand.get("metadata") if isinstance(cand, dict) else {})
                    sentence = metadata.get("sentence", dcid) if isinstance(metadata, dict) else dcid

                    # Filter for actual StatisticalVariable nodes (skip broad Topic nodes)
                    if "StatisticalVariable" in type_of and dcid:
                        variables.append({
                            "dcid": dcid,
                            "name": sentence,
                            "score": float(metadata.get("score", 0.0)) if isinstance(metadata, dict) else 0.0
                        })
                        break
            return variables
        except Exception as e:
            logger.warning(f"Data Commons indicator resolution failed for {query_terms}: {e}")
            return []

    def fetch_observations(
        self,
        variable_dcids: List[str],
        entity_dcids: List[str],
        date: str = "all",
    ) -> pd.DataFrame:
        """
        Fetches official observations from Data Commons as a pandas DataFrame.
        """
        if not variable_dcids or not entity_dcids:
            return pd.DataFrame()

        try:
            df = self._client.observations_dataframe(
                variable_dcids=variable_dcids,
                date=date,
                entity_dcids=entity_dcids,
            )
            return df
        except Exception as e:
            logger.error(f"Data Commons observation fetch error: {e}")
            return pd.DataFrame()

    def ground_query(
        self,
        query: str,
        places: Optional[List[str]] = None,
        variables: Optional[List[str]] = None,
        date: str = "all",
        slug: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        End-to-end data grounding:
        1. Identifies places and statistical indicators from query if not explicitly passed.
        2. Resolves DCIDs.
        3. Fetches time-series data from Data Commons.
        4. Writes a clean grounded CSV file to vault/scratch/.
        5. Returns metadata, DataFrame preview, and provenance links.
        """
        # Dynamic entity detection if places not provided
        if not places:
            known_places = [
                "United States", "USA", "US", "China", "India", "Germany", "United Kingdom",
                "UK", "France", "Japan", "Canada", "California", "Texas", "New York",
                "Florida", "Brazil", "Australia", "Italy", "Spain", "Russia", "Mexico",
                "South Korea", "Indonesia", "Saudi Arabia", "South Africa", "Nigeria",
                "Argentina", "Netherlands", "Switzerland", "Sweden", "Norway", "Poland",
                "Turkey", "Egypt", "Singapore", "New Zealand", "Vietnam", "Thailand",
                "Philippines", "Pakistan", "Bangladesh", "Colombia", "Chile", "Finland",
                "Denmark", "Ireland", "Portugal", "Greece", "Austria", "Belgium", "Israel"
            ]
            matched = [p for p in known_places if re.search(rf"\b{re.escape(p)}\b", query, flags=re.IGNORECASE)]

            # Also discover any capitalized words that might be countries, states, or cities
            cap_candidates = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", query)
            ignore_caps = {
                "Compare", "Plot", "Graph", "Chart", "Show", "Analyze", "What", "How",
                "Which", "Tell", "Give", "Google", "Data", "Commons", "World", "Bank",
                "Rate", "Emissions", "Growth", "History", "Per", "Capita", "Average",
                "Total", "Year", "Month", "Trend", "Trajectory", "Dataset", "Table"
            }
            extra_candidates = [c for c in cap_candidates if c not in ignore_caps and c not in matched]

            all_candidates = list(dict.fromkeys(matched + extra_candidates))
            resolved_places = self.resolve_places(all_candidates) if all_candidates else {}
        else:
            resolved_places = self.resolve_places(places)

        # Heuristic indicator detection if variables not provided
        resolved_vars = []
        if variables:
            # If explicit DCIDs are passed
            resolved_vars = [{"dcid": v, "name": v} for v in variables]
        else:
            term_map = {
                "population": "Count_Person",
                "people": "Count_Person",
                "residents": "Count_Person",
                "life expectancy": "LifeExpectancy_Person",
                "lifespan": "LifeExpectancy_Person",
                "gdp": "Amount_EconomicActivity_GrossDomesticProduction_RealValue",
                "gdp per capita": "Amount_EconomicActivity_GrossDomesticProduct_Nominal_PerCapita",
                "unemployment": "UnemploymentRate_Person",
                "unemployment rate": "UnemploymentRate_Person",
                "emissions": "Amount_Emissions_CarbonDioxide_PerCapita",
                "carbon emissions": "Amount_Emissions_CarbonDioxide_PerCapita",
                "co2": "Amount_Emissions_CarbonDioxide_PerCapita",
                "fertility rate": "FertilityRate_Person_Female",
                "birth rate": "FertilityRate_Person_Female",
                "inflation": "InflationRate_EconomicActivity",
                "cpi": "ConsumerPriceIndex_Person",
                "energy consumption": "Amount_Consumption_Energy",
            }
            lowered = query.lower()
            for k, v in term_map.items():
                if k in lowered:
                    resolved_vars.append({"dcid": v, "name": k.title()})

            # If no direct map, attempt semantic indicator resolution
            if not resolved_vars:
                words = [w for w in re.findall(r"\b[a-zA-Z]{3,}\b", lowered) if w not in {"what", "which", "how", "show", "plot", "graph", "chart", "analyze", "the", "and", "between", "over", "from", "for"}]
                if words:
                    resolved_vars = self.resolve_variables([" ".join(words[:3])])

        if not resolved_places or not resolved_vars:
            return {
                "success": False,
                "reason": f"Could not resolve places ({places}) or variables ({resolved_vars})",
                "df": pd.DataFrame(),
                "csv_path": None,
            }

        var_dcids = list(dict.fromkeys([v["dcid"] for v in resolved_vars]))
        place_dcids = list(resolved_places.values())

        df = self.fetch_observations(variable_dcids=var_dcids, entity_dcids=place_dcids, date=date)

        if df.empty:
            return {
                "success": False,
                "reason": "Data Commons returned 0 records for the requested indicators and places.",
                "df": pd.DataFrame(),
                "csv_path": None,
            }

        # Filter and sort
        clean_df = df.copy()
        if "date" in clean_df.columns:
            clean_df = clean_df.sort_values(by=["entity", "variable", "date"], ascending=[True, True, True])

        # Write clean CSV to vault/scratch
        safe_slug = slug or re.sub(r"[^a-zA-Z0-9_-]", "_", query.lower()[:30]).strip("_")
        csv_file = self.scratch_dir / f"{safe_slug}_datacommons.csv"
        clean_df.to_csv(csv_file, index=False)

        # Collect official data provenances
        provenances = []
        if "provenanceUrl" in clean_df.columns:
            provenances = [str(u) for u in clean_df["provenanceUrl"].dropna().unique() if str(u).startswith("http")]

        return {
            "success": True,
            "csv_path": str(csv_file),
            "csv_filename": csv_file.name,
            "rows": len(clean_df),
            "columns": list(clean_df.columns),
            "entities": resolved_places,
            "variables": [v["dcid"] for v in resolved_vars],
            "provenances": provenances,
            "df": clean_df,
            "summary_md": clean_df.head(10)[["date", "entity", "variable", "value"]].to_markdown(index=False) if {"date", "entity", "variable", "value"}.issubset(clean_df.columns) else clean_df.head(10).to_markdown(index=False)
        }
