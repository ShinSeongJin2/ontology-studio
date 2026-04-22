"""HTTP API for ontology schema and graph queries."""

from __future__ import annotations

import json

from fastapi import APIRouter

from .tools import get_driver, schema_get, _run_query

router = APIRouter(tags=["ontology"])


def _normalize_graph_value(value):
    """Convert Neo4j values into JSON-friendly scalars/collections."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_normalize_graph_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_graph_value(item) for key, item in value.items()}
    return str(value)


def _normalize_graph_properties(entity) -> dict:
    """Normalize graph entity properties for API responses."""

    return {
        key: _normalize_graph_value(value)
        for key, value in dict(entity).items()
    }


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


@router.get("/api/schemas")
async def list_schemas_endpoint():
    """Return all ontology schema groups with class lists and entity counts."""

    from ..agent_session.session_store import list_schemas

    try:
        schemas = list_schemas()
        # Enrich with Neo4j entity counts
        driver = get_driver()
        for schema in schemas:
            total = 0
            for cls in schema.get("classes", []):
                try:
                    with driver.session() as sess:
                        result = sess.run(
                            f"MATCH (n:_Entity:{cls['class_name']}) RETURN count(n) AS cnt"
                        ).single()
                        cnt = result["cnt"] if result else 0
                        cls["entity_count"] = cnt
                        total += cnt
                except Exception:
                    cls["entity_count"] = 0
            schema["total_entity_count"] = total
        return {"schemas": schemas}
    except Exception as exc:
        return {"schemas": [], "error": str(exc)}


@router.get("/api/schemas/{schema_id}")
async def get_schema_detail(schema_id: str):
    """Return a single schema group detail."""

    from ..agent_session.session_store import get_schema

    schema = get_schema(schema_id)
    if not schema:
        return {"error": "스키마를 찾을 수 없습니다."}
    return schema


@router.delete("/api/schemas/{schema_id}")
async def delete_schema_endpoint(schema_id: str):
    """Delete a schema group (metadata only, entities preserved)."""

    from ..agent_session.session_store import delete_schema

    try:
        delete_schema(schema_id)
        return {"status": "ok"}
    except Exception as exc:
        return {"error": str(exc)}


@router.delete("/api/schemas/{schema_id}/entities")
async def delete_schema_entities(schema_id: str):
    """Delete all Neo4j entities belonging to a schema's classes."""

    from ..agent_session.session_store import get_schema

    try:
        schema = get_schema(schema_id)
        if not schema:
            return {"error": "스키마를 찾을 수 없습니다."}

        class_names = [c["class_name"] for c in schema.get("classes", [])]
        if not class_names:
            return {"status": "ok", "deleted": 0}

        total_deleted = 0
        for cls_name in class_names:
            rows = _run_query(
                f"MATCH (n:_Entity:{cls_name}) DETACH DELETE n RETURN count(n) AS cnt"
            )
            total_deleted += rows[0]["cnt"] if rows else 0

        return {"status": "ok", "deleted": total_deleted}
    except Exception as exc:
        return {"error": str(exc)}


@router.post("/api/schemas/{schema_id}/rebuild")
async def rebuild_schema_in_neo4j(schema_id: str):
    """Rebuild Neo4j _OntologyClass and _OntologyRelationshipType nodes from SQLite schema."""

    from ..agent_session.session_store import get_schema

    try:
        schema = get_schema(schema_id)
        if not schema:
            return {"error": "스키마를 찾을 수 없습니다."}

        classes_created = 0
        rels_created = 0

        for cls in schema.get("classes", []):
            _run_query(
                """
                MERGE (c:_OntologyClass {name: $name})
                SET c.description = $description,
                    c.properties = $properties,
                    c.updated_at = datetime()
                """,
                {
                    "name": cls["class_name"],
                    "description": cls.get("description", ""),
                    "properties": json.dumps(cls.get("properties", []), ensure_ascii=False),
                },
            )
            classes_created += 1

        for rel in schema.get("relationships", []):
            _run_query(
                """
                MATCH (from_c:_OntologyClass {name: $from_class})
                MATCH (to_c:_OntologyClass {name: $to_class})
                MERGE (r:_OntologyRelationshipType {name: $name})
                SET r.description = $description,
                    r.from_class = $from_class,
                    r.to_class = $to_class,
                    r.properties = $properties,
                    r.updated_at = datetime()
                MERGE (r)-[:FROM_CLASS]->(from_c)
                MERGE (r)-[:TO_CLASS]->(to_c)
                """,
                {
                    "name": rel["name"],
                    "from_class": rel["from_class"],
                    "to_class": rel["to_class"],
                    "description": rel.get("description", ""),
                    "properties": json.dumps(rel.get("properties", []), ensure_ascii=False),
                },
            )
            rels_created += 1

        return {
            "status": "ok",
            "classes_created": classes_created,
            "relationships_created": rels_created,
        }
    except Exception as exc:
        return {"error": str(exc)}


@router.get("/api/graph")
async def get_graph(class_name: str = "", schema_name: str = "", limit: int = 100):
    """Return graph nodes and edges, optionally filtered by class."""

    try:
        # Resolve schema_name to class list if provided
        filter_classes = []
        if schema_name:
            from ..agent_session.session_store import get_schema_by_name
            schema = get_schema_by_name(schema_name)
            if schema:
                filter_classes = [c["class_name"] for c in schema.get("classes", [])]
            if not filter_classes:
                return {"nodes": [], "edges": []}

        driver = get_driver()
        with driver.session() as session:
            if class_name:
                result = session.run(
                    "MATCH (n:_Entity) WHERE $class IN labels(n) "
                    "OPTIONAL MATCH (n)-[r]->(m:_Entity) "
                    "RETURN n, r, m LIMIT $limit",
                    {"class": class_name, "limit": limit},
                )
            elif filter_classes:
                # Filter by schema's classes
                result = session.run(
                    "MATCH (n:_Entity) WHERE any(lbl IN labels(n) WHERE lbl IN $classes) "
                    "OPTIONAL MATCH (n)-[r]->(m:_Entity) "
                    "RETURN n, r, m LIMIT $limit",
                    {"classes": filter_classes, "limit": limit},
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
                        "properties": _normalize_graph_properties(node),
                    }

                matched_node = record["m"]
                if matched_node and matched_node.element_id not in nodes:
                    nodes[matched_node.element_id] = {
                        "id": matched_node.element_id,
                        "label": dict(matched_node).get("name", ""),
                        "labels": list(matched_node.labels),
                        "properties": _normalize_graph_properties(matched_node),
                    }

                relationship = record["r"]
                if relationship:
                    edges.append(
                        {
                            "from": relationship.start_node.element_id,
                            "to": relationship.end_node.element_id,
                            "type": relationship.type,
                            "properties": _normalize_graph_properties(relationship),
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
                    "properties": _normalize_graph_properties(sn),
                }

            for record in result:
                for key in ["m", "o", "p"]:
                    nd = record[key]
                    if nd and nd.element_id not in nodes:
                        nodes[nd.element_id] = {
                            "id": nd.element_id,
                            "label": dict(nd).get("name", ""),
                            "labels": list(nd.labels),
                            "properties": _normalize_graph_properties(nd),
                        }
                for key in ["r", "r2"]:
                    rel = record[key]
                    if rel:
                        edge = {
                            "from": rel.start_node.element_id,
                            "to": rel.end_node.element_id,
                            "type": rel.type,
                            "properties": _normalize_graph_properties(rel),
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
                                "properties": _normalize_graph_properties(nd),
                            }
                    rel = record["r"]
                    if rel:
                        edges.append(
                            {
                                "from": rel.start_node.element_id,
                                "to": rel.end_node.element_id,
                                "type": rel.type,
                                "properties": _normalize_graph_properties(rel),
                            }
                        )

            return {"nodes": list(nodes.values()), "edges": edges}
        except Exception as inner_exc:  # pragma: no cover
            return {"nodes": [], "edges": [], "error": str(inner_exc)}
