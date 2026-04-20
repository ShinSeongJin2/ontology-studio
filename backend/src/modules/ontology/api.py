"""HTTP API for ontology schema and graph queries."""

from __future__ import annotations

import json

from fastapi import APIRouter, Body, HTTPException

from .schema_models import OntologySchemaModel
from .schema_store import (
    delete_schema,
    get_active_schema,
    get_schema_by_id,
    list_saved_schemas,
    rename_schema,
    save_schema_snapshot,
    set_active_schema,
)
from .tools import get_driver, project_graph_to_schema, schema_get

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


def _load_projected_schema_or_raise(
    *,
    name: str = "Layered Ontology Snapshot",
    description: str = "",
    domain: str = "",
) -> OntologySchemaModel:
    projected = project_graph_to_schema(name=name, description=description, domain=domain)
    try:
        payload = json.loads(projected)
    except json.JSONDecodeError as exc:  # pragma: no cover - passthrough error handling
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise HTTPException(status_code=500, detail=str(payload["error"]))
    return OntologySchemaModel.model_validate(payload)


@router.get("/api/ontology/schema")
async def get_active_ontology_schema():
    """Return the active saved ontology schema or project the current graph."""

    schema = get_active_schema()
    if schema is not None:
        return schema.model_dump()
    projected = _load_projected_schema_or_raise()
    return projected.model_dump()


@router.post("/api/ontology/schema")
async def save_ontology_schema(schema: OntologySchemaModel | None = Body(default=None)):
    """Persist the provided schema snapshot or project and save the active graph."""

    target_schema = schema or _load_projected_schema_or_raise()
    return save_schema_snapshot(target_schema)


@router.get("/api/ontology/schemas")
async def list_ontology_schemas():
    """List saved ontology schema snapshots."""

    return list_saved_schemas()


@router.get("/api/ontology/schemas/{schema_id}")
async def get_ontology_schema(schema_id: str):
    """Return a stored schema snapshot by id."""

    schema = get_schema_by_id(schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="schema not found")
    return schema.model_dump()


@router.post("/api/ontology/schemas/{schema_id}/activate")
async def activate_ontology_schema(schema_id: str):
    """Activate a stored ontology schema snapshot."""

    try:
        return set_active_schema(schema_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/api/ontology/schemas/{schema_id}")
async def patch_ontology_schema(schema_id: str, payload: dict = Body(default_factory=dict)):
    """Rename or update the metadata of a saved schema."""

    try:
        return rename_schema(
            schema_id,
            name=payload.get("name"),
            description=payload.get("description"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/api/ontology/schemas/{schema_id}")
async def delete_ontology_schema(schema_id: str):
    """Delete a stored schema snapshot."""

    try:
        return delete_schema(schema_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
