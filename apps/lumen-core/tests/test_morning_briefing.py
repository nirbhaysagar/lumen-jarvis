import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.briefing.service import MorningBriefingService, WeatherInfo, NewsHeadline
from src.runtime.session import ConversationSession
from src.sentinel.models import SentinelReport, SentinelSeverity, SentinelFinding, SentinelCategory
from src.vault.vault_sync import ObsidianVaultSync


@pytest.fixture
def mock_vault(tmp_path: Path):
    vault = ObsidianVaultSync(vault_path=tmp_path / "vault")
    return vault


@pytest.fixture
def mock_sentinel():
    sentinel = MagicMock()
    report = SentinelReport(
        patrol_id="patrol_test_01",
        overall_status=SentinelSeverity.OK,
        system_metrics={
            "cpu_percent": 12.5,
            "ram_percent": 45.0,
            "ram_free_gb": 8.5,
            "disk_percent": 55.0,
            "disk_free_gb": 120.0,
        },
        containers_monitored=[
            {"name": "lumen-postgres", "status": "running", "healthy": True},
            {"name": "lumen-redis", "status": "running", "healthy": True},
        ],
        findings=[],
    )
    sentinel.run_patrol = AsyncMock(return_value=report)
    sentinel.last_report = report
    return sentinel


@pytest.fixture
def mock_memory():
    mem = MagicMock()
    mem.list_review_queue = AsyncMock(return_value=[
        {
            "id": "prop_01",
            "proposal_type": "TRIPLE",
            "subject_raw": "Lumen",
            "predicate_raw": "PREFERS",
            "object_raw": "PostgreSQL",
            "conflict_reason": "Contradicts past belief",
        }
    ])
    return mem


@pytest.fixture
def mock_supervisor():
    sup = MagicMock()
    sup.list_active_tasks = AsyncMock(return_value=[
        {"task_id": "task_101", "name": "DocumentIndexWorker"}
    ])
    return sup


@pytest.mark.asyncio
async def test_morning_briefing_generation(mock_vault, mock_sentinel, mock_memory, mock_supervisor):
    service = MorningBriefingService(
        sentinel=mock_sentinel,
        memory_client=mock_memory,
        vault_sync=mock_vault,
        supervisor=mock_supervisor,
        default_city="New Delhi",
    )

    mock_headlines = [
        NewsHeadline(title="Quantum Computing Breakthrough Announced", url="https://example.com/qc", snippet="Researchers achieved 99.9% gate fidelity.", source="example.com"),
        NewsHeadline(title="New Open-Source AI Model Released", url="https://example.com/ai", snippet="Weights made available for local execution.", source="example.com"),
    ]

    mock_weather = WeatherInfo(
        city="New Delhi",
        temperature_c="22",
        temperature_f="72",
        feels_like_c="21",
        condition="Clear",
        humidity="50",
        wind_kmph="10",
        today_max_c="28",
        today_min_c="16",
        raw_summary="Clear, 22°C",
    )

    with patch.object(service, "fetch_weather", AsyncMock(return_value=mock_weather)), \
         patch.object(service, "fetch_top_headlines", AsyncMock(return_value=mock_headlines)):

        report = await service.generate_briefing(city="New Delhi")

        # Verify returned data
        assert report.date_str == datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert report.weather.city == "New Delhi"
        assert report.weather.temperature_c == "22"
        assert len(report.headlines) == 2
        assert len(report.pending_proposals) == 1
        assert len(report.active_tasks) == 1

        # Verify spoken narrative
        assert "Good morning, Sir" in report.spoken_summary
        assert "New Delhi" in report.spoken_summary
        assert "22 degrees Celsius" in report.spoken_summary
        assert "optimal parameters" in report.spoken_summary
        assert "Quantum Computing Breakthrough" in report.spoken_summary
        assert "1 pending memory proposal" in report.spoken_summary

        # Verify Obsidian Markdown note creation
        vault_file = Path(report.vault_path)
        assert vault_file.exists()
        content = vault_file.read_text(encoding="utf-8")
        assert "# 🌅 Morning Executive Intelligence Briefing" in content
        assert "Quantum Computing Breakthrough Announced" in content
        assert "lumen-postgres" in content
        assert "prop_01" in content
        assert "DocumentIndexWorker" in content


@pytest.mark.asyncio
async def test_session_briefing_slash_command(mock_vault, mock_sentinel, mock_memory, mock_supervisor):
    service = MorningBriefingService(
        sentinel=mock_sentinel,
        memory_client=mock_memory,
        vault_sync=mock_vault,
        supervisor=mock_supervisor,
        default_city="London",
    )

    mock_weather = WeatherInfo(
        city="London",
        temperature_c="14",
        temperature_f="57",
        feels_like_c="13",
        condition="Rain",
        raw_summary="Rain, 14°C",
    )

    with patch.object(service, "fetch_weather", AsyncMock(return_value=mock_weather)), \
         patch.object(service, "fetch_top_headlines", AsyncMock(return_value=[])):

        session = ConversationSession(
            vault_sync=mock_vault,
            sentinel_daemon=mock_sentinel,
            morning_briefing_service=service,
        )

        # Trigger via /briefing
        output_tokens = []
        async for chunk in session.process_turn("/briefing London"):
            output_tokens.append(chunk)

        full_output = "".join(output_tokens)
        assert "🌅 Compiling your Morning Executive Intelligence Briefing..." in full_output
        assert "Good morning, Sir" in full_output
        assert "London" in full_output
        assert "briefing_" in full_output


@pytest.mark.asyncio
async def test_session_natural_good_morning_greeting(mock_vault, mock_sentinel, mock_memory, mock_supervisor):
    service = MorningBriefingService(
        sentinel=mock_sentinel,
        memory_client=mock_memory,
        vault_sync=mock_vault,
        supervisor=mock_supervisor,
        default_city="Tokyo",
    )

    mock_weather = WeatherInfo(
        city="Tokyo",
        temperature_c="19",
        temperature_f="66",
        feels_like_c="19",
        condition="Sunny",
        raw_summary="Sunny, 19°C",
    )

    with patch.object(service, "fetch_weather", AsyncMock(return_value=mock_weather)), \
         patch.object(service, "fetch_top_headlines", AsyncMock(return_value=[])):

        session = ConversationSession(
            vault_sync=mock_vault,
            sentinel_daemon=mock_sentinel,
            morning_briefing_service=service,
        )

        # Trigger via natural voice greeting "Good morning, Lumen!"
        output_tokens = []
        async for chunk in session.process_turn("Good morning, Lumen!"):
            output_tokens.append(chunk)

        full_output = "".join(output_tokens)
        assert "🌅 Compiling your Morning Executive Intelligence Briefing..." in full_output
        assert "Good morning, Sir" in full_output
        assert "Tokyo" in full_output
