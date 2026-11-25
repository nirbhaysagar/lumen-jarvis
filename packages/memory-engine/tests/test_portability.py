"""
Tests for Export, Import & Replay Portability (Milestone X1).
Verifies:
1. Byte-identical round-trip: export -> truncate -> import -> export produces identical JSONL bytes.
2. Refusing import into non-empty database.
3. Secret exclusion (--exclude-secret) strips secret memories & quotes completely.
4. Fernet encryption & decryption round-trip with passphrase.
5. Event hash chain verification on import.
"""
from pathlib import Path
import uuid
import pytest

from app.main import app
from app.portability.export import export_bundle, EXPORT_TABLES
from app.portability.import_ import import_bundle


@pytest.mark.asyncio
async def test_portability_round_trip_and_encryption(tmp_path: Path):
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool

        # 1. Seed deterministic data
        async with pool.acquire() as conn:
            ctx_id = await conn.fetchval(
                """
                INSERT INTO contexts (id, slug, name)
                VALUES ($1, $2, $3)
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                uuid.uuid4(), f"port-ctx-{uuid.uuid4().hex[:8]}", "Portability Context"
            )
            ev_id = uuid.uuid4()
            await conn.execute(
                "INSERT INTO events (id, context_id, kind, actor, payload) VALUES ($1, $2, 'CAPTURE', 'user', '{\"text\": \"Lumen uses Postgres\"}')",
                ev_id, ctx_id
            )
            m_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO memories (id, lineage_id, context_id, created_event, version, status, statement,
                                      type, epistemic, asserted_by, confidence, sensitivity)
                VALUES ($1, $1, $2, $3, 1, 'active', 'Lumen uses Postgres 17', 'fact', 'fact', 'user', 1.0, 'normal')
                """,
                m_id, ctx_id, ev_id
            )
            # Also seed a secret memory to test --exclude-secret
            sec_ev = uuid.uuid4()
            await conn.execute(
                "INSERT INTO events (id, context_id, kind, actor, payload) VALUES ($1, $2, 'CAPTURE', 'user', '{\"text\": \"My secret OTP is 998877\"}')",
                sec_ev, ctx_id
            )
            sec_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO memories (id, lineage_id, context_id, created_event, version, status, statement,
                                      type, epistemic, asserted_by, confidence, sensitivity)
                VALUES ($1, $1, $2, $3, 1, 'active', 'My secret OTP is 998877', 'fact', 'fact', 'user', 1.0, 'secret')
                """,
                sec_id, ctx_id, sec_ev
            )

        # 2. Export Bundle 1 (unencrypted)
        bundle1 = tmp_path / "bundle1"
        manifest1 = await export_bundle(pool, bundle1)
        assert manifest1["counts"]["memories"] >= 2

        # 3. Export with --exclude-secret and verify secret text is absent
        bundle_no_sec = tmp_path / "bundle_no_sec"
        await export_bundle(pool, bundle_no_sec, exclude_secret=True)
        for f in bundle_no_sec.glob("*.jsonl"):
            txt = f.read_text(encoding="utf-8")
            assert "998877" not in txt, f"Secret leaked into {f.name}!"

        # 4. Importing into non-empty DB MUST be refused
        with pytest.raises(ValueError, match="non-empty database"):
            await import_bundle(pool, bundle1)

        # 5. Export encrypted bundle
        bundle_enc = tmp_path / "bundle_enc"
        manifest_enc = await export_bundle(pool, bundle_enc, passphrase="my-strong-passphrase")
        assert manifest_enc["encrypted"] is True
        assert (bundle_enc / "memories.jsonl.enc").exists()

        # 6. Truncate tables in test DB to simulate empty DB, then import Bundle 1
        # Note: events is append-only via trigger, so TRUNCATE TABLE is allowed in Postgres (TRUNCATE doesn't fire row triggers)
        table_names = ", ".join(t for t, _ in reversed(EXPORT_TABLES))
        async with pool.acquire() as conn:
            await conn.execute(f"TRUNCATE TABLE {table_names} CASCADE")

        imp_res = await import_bundle(pool, bundle1)
        assert imp_res["status"] == "imported"
        assert imp_res["chain_verification"]["valid"] is True

        # 7. Export Bundle 2 and verify BYTE-IDENTICAL jsonl files!
        bundle2 = tmp_path / "bundle2"
        await export_bundle(pool, bundle2)

        for tname, _ in EXPORT_TABLES:
            b1_bytes = (bundle1 / f"{tname}.jsonl").read_bytes()
            b2_bytes = (bundle2 / f"{tname}.jsonl").read_bytes()
            assert b1_bytes == b2_bytes, f"Round-trip mismatch on {tname}.jsonl!"

        # 8. Truncate again and test encrypted import
        async with pool.acquire() as conn:
            await conn.execute(f"TRUNCATE TABLE {table_names} CASCADE")

        imp_enc_res = await import_bundle(pool, bundle_enc, passphrase="my-strong-passphrase")
        assert imp_enc_res["status"] == "imported"
