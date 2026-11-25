"""
Graph and Timeline API Endpoints (Milestone O3).
Provides graph traversal over entities and memory relations, bi-temporal as_of filtering,
and versioned timeline inspection for the in-built Knowledge Vault and Live Viewer.
Prefix: /v1
"""
from __future__ import annotations
from datetime import datetime
import json
import logging
from typing import Any, List, Optional
import uuid
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

logger = logging.getLogger("lumen.api.graph")

router = APIRouter(prefix="/v1", tags=["graph", "timeline"])

MAX_GRAPH_NODES = 300
MAX_GRAPH_DEPTH = 3


def get_db_pool(request: Request) -> asyncpg.Pool:
    storage = getattr(request.app.state, "storage", None)
    pool = getattr(request.app.state, "db_pool", None) or (storage.pool if storage else None)
    if not pool:
        raise HTTPException(status_code=500, detail="Database pool not available")
    return pool


@router.get("/graph")
async def get_knowledge_graph(
    entity: Optional[str] = Query(default=None, description="Entity canonical name, alias, or UUID to center on"),
    depth: int = Query(default=2, ge=1, le=MAX_GRAPH_DEPTH, description="Recursive expansion depth (1..3)"),
    lane: Optional[str] = Query(default=None, description="Filter memories by lane"),
    as_of: Optional[str] = Query(default=None, description="Bi-temporal transaction-time filter ISO-8601"),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """
    Returns an entity-centric knowledge graph with bi-temporal transaction-time filtering,
    relation typing, and degree calculation.
    """
    parsed_as_of: Optional[datetime] = None
    if as_of:
        try:
            cleaned_as_of = as_of.strip().replace(" ", "+")
            parsed_as_of = datetime.fromisoformat(cleaned_as_of)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid as_of timestamp: {e}")

    async with pool.acquire() as conn:
        # 1. Resolve seed entity if specified
        seed_entity_id: Optional[uuid.UUID] = None
        if entity:
            clean_entity = entity.strip()
            # Try UUID first
            try:
                seed_entity_id = uuid.UUID(clean_entity)
                seed_row = await conn.fetchrow(
                    "SELECT id FROM entities WHERE id = $1", seed_entity_id
                )
                if not seed_row:
                    seed_entity_id = None
            except ValueError:
                seed_entity_id = None

            if not seed_entity_id:
                # Match canonical_name or aliases
                seed_row = await conn.fetchrow(
                    """
                    SELECT id FROM entities
                    WHERE canonical_name ILIKE $1
                       OR $1 ILIKE ANY(aliases)
                    LIMIT 1
                    """,
                    clean_entity,
                )
                if seed_row:
                    seed_entity_id = seed_row["id"]
                else:
                    return {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0}

        # 2. Build Memory filter conditions
        mem_conditions = []
        mem_params: list[Any] = []
        idx = 1

        if parsed_as_of is not None:
            mem_conditions.append(f"m.recorded_at <= ${idx} AND (m.superseded_at IS NULL OR m.superseded_at > ${idx})")
            mem_params.append(parsed_as_of)
            idx += 1
        else:
            mem_conditions.append("m.status = 'active' AND m.superseded_at IS NULL")

        if lane is not None:
            mem_conditions.append(f"m.lane = ${idx}")
            mem_params.append(lane)
            idx += 1

        mem_filter = " AND ".join(mem_conditions)

        # 3. Fetch candidate edges
        # Edge Source 1: Direct Memory edges (subject_id -> object_id)
        query_direct = f"""
            SELECT
                m.id AS memory_id,
                m.subject_id AS source,
                m.object_id AS target,
                COALESCE(m.predicate, 'RELATED_TO') AS type,
                m.valid_from,
                m.valid_to,
                m.recorded_at,
                m.superseded_at
            FROM memories m
            WHERE m.subject_id IS NOT NULL
              AND m.object_id IS NOT NULL
              AND m.subject_id != m.object_id
              AND {mem_filter}
        """
        direct_rows = await conn.fetch(query_direct, *mem_params)

        # Edge Source 2: Memory Relations to Entity (subject_id -> to_entity)
        query_mr_entity = f"""
            SELECT
                m.id AS memory_id,
                m.subject_id AS source,
                mr.to_entity AS target,
                mr.type AS type,
                m.valid_from,
                m.valid_to,
                m.recorded_at,
                m.superseded_at
            FROM memory_relations mr
            JOIN memories m ON mr.from_memory = m.id
            WHERE mr.to_entity IS NOT NULL
              AND m.subject_id IS NOT NULL
              AND m.subject_id != mr.to_entity
              AND {mem_filter}
        """
        mr_entity_rows = await conn.fetch(query_mr_entity, *mem_params)

        # Edge Source 3: Memory Relations between Memories (m1.subject_id -> m2.subject_id)
        query_mr_mem = f"""
            SELECT
                m1.id AS memory_id,
                m1.subject_id AS source,
                m2.subject_id AS target,
                mr.type AS type,
                m1.valid_from,
                m1.valid_to,
                m1.recorded_at,
                m1.superseded_at
            FROM memory_relations mr
            JOIN memories m1 ON mr.from_memory = m1.id
            JOIN memories m2 ON mr.to_memory = m2.id
            WHERE mr.to_memory IS NOT NULL
              AND m1.subject_id IS NOT NULL
              AND m2.subject_id IS NOT NULL
              AND m1.subject_id != m2.subject_id
              AND ({mem_filter.replace('m.', 'm1.')})
              AND ({mem_filter.replace('m.', 'm2.')})
        """
        mr_mem_rows = await conn.fetch(query_mr_mem, *mem_params)

        all_edges_raw = []
        for r in direct_rows:
            all_edges_raw.append(dict(r))
        for r in mr_entity_rows:
            all_edges_raw.append(dict(r))
        for r in mr_mem_rows:
            all_edges_raw.append(dict(r))

        # 4. Filter by seed entity and depth if entity specified
        selected_node_ids: set[uuid.UUID] = set()
        selected_edges = []

        if seed_entity_id:
            # BFS up to depth
            current_layer = {seed_entity_id}
            selected_node_ids.add(seed_entity_id)

            for _ in range(depth):
                next_layer = set()
                for edge in all_edges_raw:
                    s = edge["source"]
                    t = edge["target"]
                    if s in current_layer and t not in selected_node_ids:
                        next_layer.add(t)
                        selected_node_ids.add(t)
                    elif t in current_layer and s not in selected_node_ids:
                        next_layer.add(s)
                        selected_node_ids.add(s)
                    if len(selected_node_ids) >= MAX_GRAPH_NODES:
                        break
                current_layer = next_layer
                if not current_layer or len(selected_node_ids) >= MAX_GRAPH_NODES:
                    break

            # Filter edges where both endpoints are in selected_node_ids
            for edge in all_edges_raw:
                if edge["source"] in selected_node_ids and edge["target"] in selected_node_ids:
                    selected_edges.append(edge)
        else:
            # No seed entity: take all edges within cap
            for edge in all_edges_raw:
                s = edge["source"]
                t = edge["target"]
                if len(selected_node_ids) < MAX_GRAPH_NODES:
                    selected_node_ids.add(s)
                    selected_node_ids.add(t)
                    selected_edges.append(edge)
                elif s in selected_node_ids and t in selected_node_ids:
                    selected_edges.append(edge)

        if not selected_node_ids:
            return {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0}

        # 5. Fetch Entity details for selected node IDs
        entity_rows = await conn.fetch(
            """
            SELECT id, canonical_name, kind, description
            FROM entities
            WHERE id = ANY($1::uuid[])
            LIMIT $2
            """,
            list(selected_node_ids),
            MAX_GRAPH_NODES,
        )

        entity_lookup = {r["id"]: dict(r) for r in entity_rows}

        # 6. Calculate degree for each node
        degree_map: dict[uuid.UUID, int] = {nid: 0 for nid in selected_node_ids}
        seen_edges = set()
        deduped_edges = []

        for e in selected_edges:
            s = e["source"]
            t = e["target"]
            # Deduplicate parallel edges with same type
            edge_key = (s, t, e["type"])
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)

            degree_map[s] = degree_map.get(s, 0) + 1
            degree_map[t] = degree_map.get(t, 0) + 1

            deduped_edges.append({
                "source": str(s),
                "target": str(t),
                "type": e["type"],
                "memory_id": str(e["memory_id"]),
                "valid_from": e["valid_from"].isoformat() if e.get("valid_from") else None,
                "valid_to": e["valid_to"].isoformat() if e.get("valid_to") else None,
            })

        nodes = []
        for nid in selected_node_ids:
            ent = entity_lookup.get(nid)
            label = ent["canonical_name"] if ent else str(nid)[:8]
            kind = ent["kind"] if ent else "concept"
            nodes.append({
                "id": str(nid),
                "label": label,
                "kind": kind,
                "degree": degree_map.get(nid, 0),
            })

        # Sort nodes by degree descending
        nodes.sort(key=lambda n: n["degree"], reverse=True)

        return {
            "nodes": nodes,
            "edges": deduped_edges,
            "node_count": len(nodes),
            "edge_count": len(deduped_edges),
        }


@router.get("/timeline")
async def get_timeline(
    entity: Optional[str] = Query(default=None, description="Entity name or UUID"),
    lineage_id: Optional[uuid.UUID] = Query(default=None, description="Lineage UUID"),
    from_date: Optional[str] = Query(default=None, alias="from", description="Start timestamp ISO-8601"),
    to_date: Optional[str] = Query(default=None, alias="to", description="End timestamp ISO-8601"),
    lane: Optional[str] = Query(default=None, description="Lane filter"),
    limit: int = Query(default=100, ge=1, le=500, description="Max versions to return"),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """
    Returns versioned memory timeline with bi-temporal markers, supersede links,
    and entity relations.
    """
    parsed_from: Optional[datetime] = None
    if from_date:
        try:
            parsed_from = datetime.fromisoformat(from_date.strip().replace(" ", "+"))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid 'from' timestamp: {e}")

    parsed_to: Optional[datetime] = None
    if to_date:
        try:
            parsed_to = datetime.fromisoformat(to_date.strip().replace(" ", "+"))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid 'to' timestamp: {e}")

    async with pool.acquire() as conn:
        conditions = []
        params: list[Any] = []
        idx = 1

        if lineage_id:
            conditions.append(f"m.lineage_id = ${idx}")
            params.append(lineage_id)
            idx += 1

        if entity:
            clean_entity = entity.strip()
            # Try UUID
            try:
                ent_uuid = uuid.UUID(clean_entity)
                conditions.append(f"(m.subject_id = ${idx} OR m.object_id = ${idx})")
                params.append(ent_uuid)
                idx += 1
            except ValueError:
                conditions.append(
                    f"(sub.canonical_name ILIKE ${idx} OR obj.canonical_name ILIKE ${idx} OR m.statement ILIKE '%' || ${idx} || '%')"
                )
                params.append(clean_entity)
                idx += 1

        if parsed_from:
            conditions.append(f"COALESCE(m.valid_from, m.recorded_at) >= ${idx}")
            params.append(parsed_from)
            idx += 1

        if parsed_to:
            conditions.append(f"COALESCE(m.valid_from, m.recorded_at) <= ${idx}")
            params.append(parsed_to)
            idx += 1

        if lane:
            conditions.append(f"m.lane = ${idx}")
            params.append(lane)
            idx += 1

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT
                m.lineage_id,
                m.id AS memory_id,
                m.version,
                m.statement,
                m.status,
                m.type,
                m.epistemic,
                m.lane,
                m.confidence,
                m.valid_from,
                m.valid_to,
                m.recorded_at,
                m.superseded_at,
                m.superseded_by,
                sub.canonical_name AS subject_name,
                obj.canonical_name AS object_name
            FROM memories m
            LEFT JOIN entities sub ON m.subject_id = sub.id
            LEFT JOIN entities obj ON m.object_id = obj.id
            {where_clause}
            ORDER BY m.lineage_id ASC, m.version ASC, m.recorded_at ASC
            LIMIT ${idx}
        """
        params.append(limit)

        rows = await conn.fetch(query, *params)

        timeline = []
        for r in rows:
            timeline.append({
                "lineage_id": str(r["lineage_id"]),
                "memory_id": str(r["memory_id"]),
                "version": r["version"],
                "statement": r["statement"],
                "status": r["status"],
                "type": r["type"],
                "epistemic": r["epistemic"],
                "lane": r["lane"],
                "confidence": float(r["confidence"]) if r["confidence"] is not None else 1.0,
                "valid_from": r["valid_from"].isoformat() if r.get("valid_from") else None,
                "valid_to": r["valid_to"].isoformat() if r.get("valid_to") else None,
                "recorded_at": r["recorded_at"].isoformat() if r.get("recorded_at") else None,
                "superseded_at": r["superseded_at"].isoformat() if r.get("superseded_at") else None,
                "superseded_by": str(r["superseded_by"]) if r.get("superseded_by") else None,
                "subject_name": r["subject_name"],
                "object_name": r["object_name"],
            })

        return {
            "timeline": timeline,
            "count": len(timeline),
        }
