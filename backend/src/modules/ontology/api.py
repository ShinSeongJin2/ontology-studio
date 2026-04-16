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
