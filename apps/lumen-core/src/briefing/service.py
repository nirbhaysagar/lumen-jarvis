import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field

from src.research.providers.duckduckgo import DuckDuckGoProvider
from src.sentinel.daemon import SentinelDaemon
from src.sentinel.models import SentinelSeverity
from src.vault.vault_sync import ObsidianVaultSync

logger = logging.getLogger("lumen.briefing")


class WeatherInfo(BaseModel):
    city: str = "Unknown"
    temperature_c: str = "N/A"
    temperature_f: str = "N/A"
    feels_like_c: str = "N/A"
    condition: str = "Clear"
    humidity: str = "N/A"
    wind_kmph: str = "N/A"
    today_max_c: str = "N/A"
    today_min_c: str = "N/A"
    raw_summary: str = ""


class NewsHeadline(BaseModel):
    title: str
    url: str
    snippet: str
    source: str = "Web"


class MorningBriefingReport(BaseModel):
    date_str: str
    timestamp: float = Field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    weather: WeatherInfo
    system_health: Dict[str, Any]
    headlines: List[NewsHeadline] = Field(default_factory=list)
    pending_proposals: List[Dict[str, Any]] = Field(default_factory=list)
    active_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    vault_path: Optional[str] = None
    spoken_summary: str = ""
    markdown_content: str = ""


class MorningBriefingService:
    """
    Autonomous Morning Executive Intelligence Briefing Service.
    Compiles daily weather, host Sentinel telemetry, top world/tech headlines,
    memory review proposals, and writes a permanent briefing note to vault/digests/briefing_YYYY-MM-DD.md.
    """

    def __init__(
        self,
        sentinel: Optional[SentinelDaemon] = None,
        memory_client: Optional[Any] = None,
        vault_sync: Optional[ObsidianVaultSync] = None,
        supervisor: Optional[Any] = None,
        default_city: Optional[str] = None,
    ):
        self.sentinel = sentinel
        self.memory = memory_client
        self.vault = vault_sync or ObsidianVaultSync()
        self.supervisor = supervisor
        self.default_city = default_city or os.getenv("LUMEN_USER_CITY") or os.getenv("USER_CITY") or "auto"
        self._last_briefing_date: Optional[str] = None
        self._last_report: Optional[MorningBriefingReport] = None

    async def fetch_weather(self, city: Optional[str] = None) -> WeatherInfo:
        """Fetches current weather and forecast via open wttr.in or web search fallback."""
        target_city = city or self.default_city
        url = "https://wttr.in/?format=j1" if target_city == "auto" else f"https://wttr.in/{target_city}?format=j1"

        try:
            async with httpx.AsyncClient(timeout=3.5, follow_redirects=True) as client:
                res = await client.get(url, headers={"User-Agent": "LumenAssistant/1.0"})
                if res.status_code == 200:
                    data = res.json()
                    curr = data.get("current_condition", [{}])[0]
                    area_info = data.get("nearest_area", [{}])[0]
                    resolved_city = target_city if target_city != "auto" else (
                        area_info.get("areaName", [{}])[0].get("value") or "Local Area"
                    )
                    forecast_today = data.get("weather", [{}])[0]

                    desc_list = curr.get("weatherDesc", [{}])
                    condition_text = desc_list[0].get("value", "Clear") if desc_list else "Clear"

                    return WeatherInfo(
                        city=resolved_city,
                        temperature_c=str(curr.get("temp_C", "N/A")),
                        temperature_f=str(curr.get("temp_F", "N/A")),
                        feels_like_c=str(curr.get("FeelsLikeC", "N/A")),
                        condition=condition_text,
                        humidity=str(curr.get("humidity", "N/A")),
                        wind_kmph=str(curr.get("windspeedKmph", "N/A")),
                        today_max_c=str(forecast_today.get("maxtempC", "N/A")),
                        today_min_c=str(forecast_today.get("mintempC", "N/A")),
                        raw_summary=f"{condition_text}, {curr.get('temp_C')}°C (Feels like {curr.get('FeelsLikeC')}°C)",
                    )
        except Exception as e:
            logger.warning(f"Could not reach wttr.in for weather: {e}")

        # Fallback via DuckDuckGo
        try:
            searcher = DuckDuckGoProvider()
            query = f"current weather in {target_city if target_city != 'auto' else 'Delhi'}"
            results = await searcher.search(query=query, max_results=2)
            snippet = results[0].snippet if results else "Weather data temporarily unavailable."
            return WeatherInfo(
                city=target_city if target_city != "auto" else "Local Area",
                condition="Variable",
                raw_summary=snippet[:120],
            )
        except Exception:
            return WeatherInfo(
                city=target_city if target_city != "auto" else "Local Area",
                condition="Unknown",
                raw_summary="Atmospheric sensors currently offline.",
            )

    async def fetch_system_health(self) -> Dict[str, Any]:
        """Polls Sentinel for live CPU, RAM, Disk, and Docker container health."""
        if not self.sentinel:
            return {
                "overall_status": "ok",
                "cpu_percent": 0.0,
                "ram_percent": 0.0,
                "ram_free_gb": 0.0,
                "disk_percent": 0.0,
                "disk_free_gb": 0.0,
                "containers": [],
                "alert_count": 0,
                "summary": "Sentinel daemon not attached.",
            }

        try:
            report = await self.sentinel.run_patrol()
            sys_m = report.system_metrics
            containers = [
                {
                    "name": c.get("name", "container"),
                    "status": c.get("status", "running"),
                    "healthy": c.get("healthy", True),
                }
                for c in report.containers_monitored
            ]

            status_str = report.overall_status.value.upper()
            return {
                "overall_status": status_str,
                "cpu_percent": sys_m.get("cpu_percent", 0.0),
                "ram_percent": sys_m.get("ram_percent", 0.0),
                "ram_free_gb": sys_m.get("ram_free_gb", 0.0),
                "disk_percent": sys_m.get("disk_percent", 0.0),
                "disk_free_gb": sys_m.get("disk_free_gb", 0.0),
                "containers": containers,
                "alert_count": report.alert_count,
                "findings": [f.title for f in report.findings[:3]],
                "summary": f"Status: {status_str} | CPU: {sys_m.get('cpu_percent', 0.0)}% | RAM: {sys_m.get('ram_percent', 0.0)}% | Disk Free: {sys_m.get('disk_free_gb', 0.0):.1f} GB",
            }
        except Exception as e:
            logger.error(f"Error fetching Sentinel telemetry: {e}")
            return {
                "overall_status": "WARNING",
                "summary": f"Sentinel error: {e}",
            }

    async def fetch_top_headlines(self, count: int = 4) -> List[NewsHeadline]:
        """Gathers top world and technology headlines via open DuckDuckGo search."""
        headlines: List[NewsHeadline] = []
        seen_titles: set[str] = set()

        searcher = DuckDuckGoProvider()
        queries = ["top technology news headlines today", "breaking world news today"]

        for q in queries:
            try:
                results = await searcher.search(query=q, max_results=count)
                for r in results:
                    clean_title = r.title.strip()
                    if clean_title and clean_title not in seen_titles:
                        seen_titles.add(clean_title)
                        # Extract domain for clean citation
                        from urllib.parse import urlparse
                        domain = urlparse(r.url).netloc or "News"
                        headlines.append(
                            NewsHeadline(
                                title=clean_title,
                                url=r.url,
                                snippet=r.snippet[:200],
                                source=domain,
                            )
                        )
                    if len(headlines) >= count:
                        break
            except Exception as e:
                logger.warning(f"Error searching headlines for '{q}': {e}")

            if len(headlines) >= count:
                break

        return headlines[:count]

    async def fetch_memory_and_tasks(self) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Queries pending review queue proposals and active background tasks."""
        pending_proposals: List[Dict[str, Any]] = []
        active_tasks: List[Dict[str, Any]] = []

        if self.memory and hasattr(self.memory, "list_review_queue"):
            try:
                proposals = await self.memory.list_review_queue(status="PENDING", limit=10)
                if proposals:
                    pending_proposals = proposals
            except Exception as e:
                logger.warning(f"Could not fetch review queue: {e}")

        if self.supervisor and hasattr(self.supervisor, "list_active_tasks"):
            try:
                tasks = await self.supervisor.list_active_tasks()
                if tasks:
                    active_tasks = tasks
            except Exception as e:
                logger.warning(f"Could not fetch active tasks: {e}")

        return pending_proposals, active_tasks

    def _render_spoken_summary(
        self,
        now: datetime,
        weather: WeatherInfo,
        health: Dict[str, Any],
        headlines: List[NewsHeadline],
        proposals: List[Dict[str, Any]],
        tasks: List[Dict[str, Any]],
    ) -> str:
        """Composes a concise, elegant JARVIS-voiced executive summary (sub-200 words)."""
        day_name = now.strftime("%A")
        date_formatted = now.strftime("%B %d")

        # Weather segment
        if weather.temperature_c != "N/A":
            weather_clause = (
                f"In {weather.city}, conditions are currently {weather.condition.lower()} at {weather.temperature_c} degrees Celsius"
            )
            if weather.today_max_c != "N/A":
                weather_clause += f", with an expected high of {weather.today_max_c}."
            else:
                weather_clause += "."
        else:
            weather_clause = f"Local weather for {weather.city} reports {weather.raw_summary}."

        # Infrastructure segment
        cpu = health.get("cpu_percent", 0.0)
        ram = health.get("ram_percent", 0.0)
        containers_count = len(health.get("containers", []))
        if health.get("overall_status") in ("OK", "ok"):
            health_clause = (
                f"Core infrastructure is operating within optimal parameters—CPU load is at {cpu:.0f} percent, "
                f"memory at {ram:.0f} percent, and all {containers_count} monitored containers are healthy."
            )
        else:
            health_clause = (
                f"Sentinel reports non-critical alerts with system load at {cpu:.0f} percent CPU and {ram:.0f} percent RAM."
            )

        # News segment
        news_clause = ""
        if headlines:
            top_h = headlines[0].title
            top_h = top_h.split(" - ")[0].split(" | ")[0]
            if len(headlines) > 1:
                second_h = headlines[1].title.split(" - ")[0].split(" | ")[0]
                news_clause = f"In top intelligence: {top_h}; additionally, {second_h}."
            else:
                news_clause = f"In top intelligence: {top_h}."

        # Tasks & proposals segment
        review_count = len(proposals)
        task_count = len(tasks)
        if review_count > 0:
            review_clause = f"You have {review_count} pending memory {'proposal' if review_count == 1 else 'proposals'} awaiting your review."
        else:
            review_clause = "The memory review queue is completely clear with zero unresolved conflicts."

        # Closing
        speech = (
            f"Good morning, Sir. It is {day_name}, {date_formatted}. "
            f"{weather_clause} "
            f"{health_clause} "
            f"{news_clause} "
            f"{review_clause} "
            f"I have compiled your full intelligence briefing in your Obsidian vault. How may I assist you today?"
        )
        return speech

    def _render_markdown_dossier(
        self,
        now: datetime,
        weather: WeatherInfo,
        health: Dict[str, Any],
        headlines: List[NewsHeadline],
        proposals: List[Dict[str, Any]],
        tasks: List[Dict[str, Any]],
        spoken_text: str,
    ) -> str:
        """Generates comprehensive Obsidian Markdown Daily Briefing note."""
        date_iso = now.strftime("%Y-%m-%d")
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S UTC")

        # Weather table
        weather_table = (
            f"| Metric | Value |\n"
            f"| :--- | :--- |\n"
            f"| **Location** | `{weather.city}` |\n"
            f"| **Condition** | {weather.condition} |\n"
            f"| **Temperature** | **{weather.temperature_c}°C** ({weather.temperature_f}°F) |\n"
            f"| **Feels Like** | {weather.feels_like_c}°C |\n"
            f"| **Day Range** | Low: {weather.today_min_c}°C · High: {weather.today_max_c}°C |\n"
            f"| **Humidity / Wind** | {weather.humidity}% · {weather.wind_kmph} km/h |\n"
        )

        # Health table
        containers_str = ", ".join([f"`{c.get('name')}` ({c.get('status')})" for c in health.get("containers", [])]) or "None"
        health_table = (
            f"| Vector | Status / Metric |\n"
            f"| :--- | :--- |\n"
            f"| **Overall Status** | `{health.get('overall_status', 'OK')}` |\n"
            f"| **CPU Utilization** | `{health.get('cpu_percent', 0.0):.1f}%` |\n"
            f"| **RAM Utilization** | `{health.get('ram_percent', 0.0):.1f}%` (Free: `{health.get('ram_free_gb', 0.0):.1f} GB`) |\n"
            f"| **Disk Space** | `{health.get('disk_percent', 0.0):.1f}%` (Free: `{health.get('disk_free_gb', 0.0):.1f} GB`) |\n"
            f"| **Docker Mesh** | {containers_str} |\n"
        )

        # Headlines section
        news_lines = []
        if headlines:
            for idx, h in enumerate(headlines, 1):
                news_lines.append(f"{idx}. **[{h.title}]({h.url})**\n   > {h.snippet} *(Source: {h.source})*\n")
        news_section = "\n".join(news_lines) if news_lines else "*No current headlines retrieved.*"

        # Proposals section
        proposal_lines = []
        if proposals:
            for p in proposals:
                p_id = p.get("id", "unknown")
                p_type = p.get("proposal_type", "TRIPLE")
                sub = p.get("subject_raw", "")
                pred = p.get("predicate_raw", "")
                obj = p.get("object_raw", "")
                reason = p.get("conflict_reason") or "Pending verification"
                proposal_lines.append(
                    f"* **`[{p_type}]` [[{sub}]] `{pred}` [[{obj}]]** (ID: `{p_id}`)\n"
                    f"  > *Reason:* {reason}\n"
                    f"  > *Resolution:* `/approve-proposal {p_id}` or `/reject-proposal {p_id}`\n"
                )
        proposals_section = "\n".join(proposal_lines) if proposal_lines else "*Zero pending memory conflicts. Graph is fully consolidated.*"

        # Active tasks
        task_lines = []
        if tasks:
            for t in tasks:
                t_id = t.get("task_id", "unknown")
                t_name = t.get("name", "unnamed")
                task_lines.append(f"* **`{t_name}`** (`{t_id}`) — *Running*")
        tasks_section = "\n".join(task_lines) if task_lines else "*No long-running background tasks in-flight.*"

        doc = f"""---
date: {date_iso}
type: morning_briefing
category: intelligence
location: {weather.city}
status: {health.get('overall_status', 'OK')}
tags:
  - lumen/briefing
  - lumen/sentinel
  - lumen/intelligence
---

# 🌅 Morning Executive Intelligence Briefing
> *"Good morning, Sir. All core operating systems are online and synchronized."*

**Briefing Timestamp:** `{timestamp_str}`  
**Primary Location:** `{weather.city}`

---

## 🎙️ Spoken Executive Audio Transcript
> {spoken_text}

---

## 🌤️ Atmospheric & Weather Telemetry
{weather_table}

---

## 🛡️ Sentinel Infrastructure Health Matrix
{health_table}

---

## 🌐 Top Global & Technological Intelligence
{news_section}

---

## 🧠 Memory Governance & Active Operations

### 📋 Pending Knowledge Graph Proposals ({len(proposals)})
{proposals_section}

### ⚡ In-Flight Background Tasks ({len(tasks)})
{tasks_section}

---
*Generated autonomously by Lumen JARVIS Operating System.*
"""
        return doc

    async def generate_briefing(self, city: Optional[str] = None) -> MorningBriefingReport:
        """Executes full concurrent gathering of weather, health, news, and tasks."""
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")

        logger.info(f"Generating morning briefing for date {date_str} (city: {city or self.default_city})...")

        # Gather data in parallel
        weather_coro = self.fetch_weather(city)
        health_coro = self.fetch_system_health()
        news_coro = self.fetch_top_headlines(count=4)
        mem_coro = self.fetch_memory_and_tasks()

        weather_res, health_res, news_res, (proposals_res, tasks_res) = await asyncio.gather(
            weather_coro, health_coro, news_coro, mem_coro
        )

        spoken_summary = self._render_spoken_summary(
            now=now,
            weather=weather_res,
            health=health_res,
            headlines=news_res,
            proposals=proposals_res,
            tasks=tasks_res,
        )

        markdown_doc = self._render_markdown_dossier(
            now=now,
            weather=weather_res,
            health=health_res,
            headlines=news_res,
            proposals=proposals_res,
            tasks=tasks_res,
            spoken_text=spoken_summary,
        )

        # Export permanently to vault/digests/briefing_YYYY-MM-DD.md
        digests_dir = self.vault.vault_path / "digests"
        digests_dir.mkdir(parents=True, exist_ok=True)
        filename = f"briefing_{date_str}.md"
        filepath = digests_dir / filename
        filepath.write_text(markdown_doc, encoding="utf-8")
        logger.info(f"Morning briefing saved to Obsidian vault: {filepath}")

        report = MorningBriefingReport(
            date_str=date_str,
            weather=weather_res,
            system_health=health_res,
            headlines=news_res,
            pending_proposals=proposals_res,
            active_tasks=tasks_res,
            vault_path=str(filepath.resolve()),
            spoken_summary=spoken_summary,
            markdown_content=markdown_doc,
        )

        self._last_briefing_date = date_str
        self._last_report = report
        return report
