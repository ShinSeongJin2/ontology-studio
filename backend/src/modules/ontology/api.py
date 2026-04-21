"""HTTP API for ontology schema and graph queries."""

from __future__ import annotations

import json

from fastapi import APIRouter

from .tools import get_driver, schema_get

router = APIRouter(tags=["ontology"])


@router.get("/api/neo4j/status")
async def neo4j_status():
    """Report Neo4j connectivity status."""

    try:
        driver = get_driver()
        driver.verify_connectivity()
        return {"status": "connected"}
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return {"status": "disconnected", "error": str(exc)}


@router.get("/api/schema")
async def get_schema_endpoint():
    """Return the current ontology schema."""

    try:
        result = schema_get()
        return json.loads(result)
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return {"classes": [], "relationships": [], "error": str(exc)}


@router.post("/api/neo4j/clear-all")
async def clear_all_neo4j():
    """Delete all ontology schema, entities, relationships, documents, and chunks."""

    try:
        driver = get_driver()
        with driver.session() as session:
            # Delete all relationships first, then all nodes
            session.run("MATCH ()-[r]->() DELETE r")
            session.run("MATCH (n) DETACH DELETE n")
        return {"status": "ok", "message": "Neo4j 데이터가 모두 삭제되었습니다."}
    except Exception as exc:  # pragma: no cover
        return {"status": "error", "error": str(exc)}


@router.get("/api/graph")
async def get_graph(class_name: str = "", limit: int = 100):
    """Return graph nodes and edges, optionally filtered by class."""

    try:
        driver = get_driver()
        with driver.session() as session:
            if class_name:
                result = session.run(
                    "MATCH (n:_Entity) WHERE $class IN labels(n) "
                    "OPTIONAL MATCH (n)-[r]->(m:_Entity) "
                    "RETURN n, r, m LIMIT $limit",
                    {"class": class_name, "limit": limit},
                )
            else:
                result = session.run(
                    "MATCH (n:_Entity) "
                    "OPTIONAL MATCH (n)-[r]->(m:_Entity) "
                    "RETURN n, r, m LIMIT $limit",
                    {"limit": limit},
                )

            nodes = {}
            edges = []
            for record in result:
                node = record["n"]
                if node and node.element_id not in nodes:
                    nodes[node.element_id] = {
                        "id": node.element_id,
                        "label": dict(node).get("name", ""),
                        "labels": list(node.labels),
                        "properties": dict(node),
                    }

                matched_node = record["m"]
                if matched_node and matched_node.element_id not in nodes:
                    nodes[matched_node.element_id] = {
                        "id": matched_node.element_id,
                        "label": dict(matched_node).get("name", ""),
                        "labels": list(matched_node.labels),
                        "properties": dict(matched_node),
                    }

                relationship = record["r"]
                if relationship:
                    edges.append(
                        {
                            "from": relationship.start_node.element_id,
                            "to": relationship.end_node.element_id,
                            "type": relationship.type,
                            "properties": dict(relationship),
                        }
                    )

        return {"nodes": list(nodes.values()), "edges": edges}
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return {"nodes": [], "edges": [], "error": str(exc)}


@router.get("/api/graph/neighbors")
async def get_neighbors(node_id: str, depth: int = 1):
    """Return neighbors of a specific node up to given depth."""

    try:
        driver = get_driver()
        with driver.session() as session:
            result = session.run(
                "MATCH (n:_Entity) WHERE elementId(n) = $node_id "
                "CALL apoc.neighbors.tohop(n, '>', $depth) YIELD node AS m "
                "WITH n, m "
                "OPTIONAL MATCH (m)-[r]->(o:_Entity) "
                "OPTIONAL MATCH (p:_Entity)-[r2]->(m) "
                "RETURN m, r, o, r2, p",
                {"node_id": node_id, "depth": depth},
            )

            nodes = {}
            edges = []

            # Include the source node itself
            src = session.run(
                "MATCH (n:_Entity) WHERE elementId(n) = $node_id RETURN n",
                {"node_id": node_id},
            ).single()
            if src:
                sn = src["n"]
                nodes[sn.element_id] = {
                    "id": sn.element_id,
                    "label": dict(sn).get("name", ""),
                    "labels": list(sn.labels),
                    "properties": dict(sn),
                }

            for record in result:
                for key in ["m", "o", "p"]:
                    nd = record[key]
                    if nd and nd.element_id not in nodes:
                        nodes[nd.element_id] = {
                            "id": nd.element_id,
                            "label": dict(nd).get("name", ""),
                            "labels": list(nd.labels),
                            "properties": dict(nd),
                        }
                for key in ["r", "r2"]:
                    rel = record[key]
                    if rel:
                        edge = {
                            "from": rel.start_node.element_id,
                            "to": rel.end_node.element_id,
                            "type": rel.type,
                            "properties": dict(rel),
                        }
                        if not any(
                            e["from"] == edge["from"]
                            and e["to"] == edge["to"]
                            and e["type"] == edge["type"]
                            for e in edges
                        ):
                            edges.append(edge)

        return {"nodes": list(nodes.values()), "edges": edges}
    except Exception as exc:  # pragma: no cover
        # Fallback: APOC not installed — use plain Cypher
        try:
            driver = get_driver()
            with driver.session() as session:
                result = session.run(
                    "MATCH (n:_Entity) WHERE elementId(n) = $node_id "
                    "OPTIONAL MATCH (n)-[r]-(m:_Entity) "
                    "RETURN n, r, m",
                    {"node_id": node_id},
                )

                nodes = {}
                edges = []
                for record in result:
                    for key in ["n", "m"]:
                        nd = record[key]
                        if nd and nd.element_id not in nodes:
                            nodes[nd.element_id] = {
                                "id": nd.element_id,
                                "label": dict(nd).get("name", ""),
                                "labels": list(nd.labels),
                                "properties": dict(nd),
                            }
                    rel = record["r"]
                    if rel:
                        edges.append(
                            {
                                "from": rel.start_node.element_id,
                                "to": rel.end_node.element_id,
                                "type": rel.type,
                                "properties": dict(rel),
                            }
                        )

            return {"nodes": list(nodes.values()), "edges": edges}
        except Exception as inner_exc:  # pragma: no cover
            return {"nodes": [], "edges": [], "error": str(inner_exc)}
