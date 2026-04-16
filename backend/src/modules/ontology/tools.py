"""Neo4j-backed ontology tools and repository helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from neo4j import GraphDatabase, READ_ACCESS

from ...shared.kernel.settings import get_settings

_driver = None
_READONLY_CYPHER_FORBIDDEN = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|CALL)\b|LOAD\s+CSV",
    re.IGNORECASE,
)
_CYPHER_COMMENT_PATTERN = re.compile(r"//.*?$|/\*.*?\*/", re.MULTILINE | re.DOTALL)


def get_driver():
    """Return a singleton Neo4j driver."""

    global _driver
    if _driver is None:
        settings = get_settings()
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
    return _driver


def _run_query(query: str, params: dict | None = None) -> list[dict]:
    """Execute a Cypher query and return row dictionaries."""

    driver = get_driver()
    with driver.session() as session:
        result = session.run(query, params or {})
        return [record.data() for record in result]


def _run_readonly_query(query: str, params: dict | None = None) -> list[dict]:
    """Execute a read-only Cypher query and return row dictionaries."""

    driver = get_driver()
    with driver.session(default_access_mode=READ_ACCESS) as session:
        result = session.run(query, params or {})
        return [record.data() for record in result]


def _node_to_dict(node) -> dict:
    """Convert a Neo4j node into a serializable dictionary."""

    if node is None:
        return {}
    return {"id": node.element_id, "labels": list(node.labels), **dict(node)}


def _serialize_rows(rows: list[dict]) -> str:
    """Serialize Neo4j rows into JSON-friendly dictionaries."""

    serialized = []
    for row in rows:
        serialized_row = {}
        for key, value in row.items():
            if hasattr(value, "element_id"):
                serialized_row[key] = _node_to_dict(value)
            elif hasattr(value, "type"):
                serialized_row[key] = {"type": value.type, **dict(value)}
            else:
                serialized_row[key] = value
        serialized.append(serialized_row)
    return json.dumps(serialized, ensure_ascii=False, default=str)


def _is_readonly_cypher(query: str) -> bool:
    """Return True when the Cypher query avoids mutating clauses."""

    sanitized = re.sub(_CYPHER_COMMENT_PATTERN, " ", query or "")
    return _READONLY_CYPHER_FORBIDDEN.search(sanitized) is None


def neo4j_cypher(query: str, params: str = "{}") -> str:
    """Execute an arbitrary Cypher query against Neo4j."""

    try:
        parsed_params = json.loads(params) if params else {}
        rows = _run_query(query, parsed_params)
        return _serialize_rows(rows)
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def neo4j_cypher_readonly(query: str, params: str = "{}") -> str:
    """Execute a read-only Cypher query against Neo4j."""

    try:
        if not query.strip():
            return json.dumps({"error": "query is required"}, ensure_ascii=False)
        if not _is_readonly_cypher(query):
            return json.dumps(
                {
                    "error": (
                        "읽기 전용 조회만 허용됩니다. "
                        "MATCH, OPTIONAL MATCH, WHERE, WITH, RETURN 중심의 Cypher만 사용하세요."
                    )
                },
                ensure_ascii=False,
            )
        parsed_params = json.loads(params) if params else {}
        rows = _run_readonly_query(query, parsed_params)
        return _serialize_rows(rows)
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def schema_create_class(
    name: str,
    description: str = "",
    properties: str = "[]",
) -> str:
    """Create or update an ontology class."""

    try:
        props = json.loads(properties) if properties else []
        rows = _run_query(
            """
            MERGE (c:_OntologyClass {name: $name})
            SET c.description = $description,
                c.properties = $properties,
                c.updated_at = datetime()
            RETURN c
            """,
            {
                "name": name,
                "description": description,
                "properties": json.dumps(props, ensure_ascii=False),
            },
        )
        if rows:
            node = rows[0]["c"]
            return json.dumps(
                {"status": "ok", "class": _node_to_dict(node)},
                ensure_ascii=False,
                default=str,
            )
        return json.dumps({"status": "ok", "class": name}, ensure_ascii=False)
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def schema_create_relationship_type(
    name: str,
    from_class: str,
    to_class: str,
    description: str = "",
    properties: str = "[]",
) -> str:
    """Define an ontology relationship type."""

    try:
        props = json.loads(properties) if properties else []
        rows = _run_query(
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
            RETURN r
            """,
            {
                "name": name,
                "from_class": from_class,
                "to_class": to_class,
                "description": description,
                "properties": json.dumps(props, ensure_ascii=False),
            },
        )
        if rows:
            node = rows[0]["r"]
            return json.dumps(
                {"status": "ok", "relationship_type": _node_to_dict(node)},
                ensure_ascii=False,
                default=str,
            )
        return json.dumps(
            {
                "error": (
                    f"클래스 '{from_class}' 또는 '{to_class}'를 찾을 수 없습니다. "
                    "먼저 schema_create_class로 클래스를 생성하세요."
                )
            },
            ensure_ascii=False,
        )
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def schema_get() -> str:
    """Return the full ontology schema."""

    try:
        classes = _run_readonly_query(
            """
            MATCH (c)
            WHERE $class_label IN labels(c)
            RETURN c
            """,
            {"class_label": "_OntologyClass"},
        )
        relationships = _run_readonly_query(
            """
            MATCH (r)
            WHERE $relationship_label IN labels(r)
            OPTIONAL MATCH (r)-[from_rel]->(fc)
            WHERE type(from_rel) = $from_rel_type
            OPTIONAL MATCH (r)-[to_rel]->(tc)
            WHERE type(to_rel) = $to_rel_type
            RETURN r, fc, tc
            """,
            {
                "relationship_label": "_OntologyRelationshipType",
                "from_rel_type": "FROM_CLASS",
                "to_rel_type": "TO_CLASS",
            },
        )

        schema_classes = []
        for row in classes:
            node = row["c"]
            node_dict = _node_to_dict(node) if hasattr(node, "element_id") else node
            raw_props = node_dict.get("properties", "[]")
            try:
                props = json.loads(raw_props) if isinstance(raw_props, str) else raw_props
            except (json.JSONDecodeError, TypeError):
                props = []
            schema_classes.append(
                {
                    "name": node_dict.get("name", ""),
                    "description": node_dict.get("description", ""),
                    "properties": props,
                }
            )
        schema_classes.sort(key=lambda item: item["name"])

        schema_relationships = []
        for row in relationships:
            node = row["r"]
            node_dict = _node_to_dict(node) if hasattr(node, "element_id") else node
            from_node = row.get("fc")
            from_node_dict = (
                _node_to_dict(from_node) if hasattr(from_node, "element_id") else from_node or {}
            )
            to_node = row.get("tc")
            to_node_dict = (
                _node_to_dict(to_node) if hasattr(to_node, "element_id") else to_node or {}
            )
            raw_props = node_dict.get("properties", "[]")
            try:
                props = json.loads(raw_props) if isinstance(raw_props, str) else raw_props
            except (json.JSONDecodeError, TypeError):
                props = []
            schema_relationships.append(
                {
                    "name": node_dict.get("name", ""),
                    "from_class": from_node_dict.get("name") or node_dict.get("from_class", ""),
                    "to_class": to_node_dict.get("name") or node_dict.get("to_class", ""),
                    "description": node_dict.get("description", ""),
                    "properties": props,
                }
            )
        schema_relationships.sort(key=lambda item: item["name"])

        return json.dumps(
            {
                "classes": schema_classes,
                "relationships": schema_relationships,
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return json.dumps(
            {"classes": [], "relationships": [], "error": str(exc)},
            ensure_ascii=False,
        )


def entity_search(class_name: str, search_criteria: str) -> str:
    """Search existing entities by class and property filters."""

    try:
        criteria = json.loads(search_criteria) if search_criteria else {}
        if not criteria:
            return json.dumps([], ensure_ascii=False)

        conditions = [f"n.{key} = ${key}" for key in criteria]
        query = f"""
        MATCH (n:_Entity:{class_name})
        WHERE {' AND '.join(conditions)}
        RETURN n
        LIMIT 10
        """
        rows = _run_query(query, criteria)
        entities = []
        for row in rows:
            node = row["n"]
            entities.append(_node_to_dict(node))
        return json.dumps(entities, ensure_ascii=False, default=str)
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def entity_create(
    class_name: str,
    properties: str,
    match_keys: str = "[]",
) -> str:
    """Create or merge an entity instance."""

    try:
        props = json.loads(properties) if properties else {}
        keys = json.loads(match_keys) if match_keys else []

        merge_props = {key: props.get(key) for key in keys if key in props}
        if not merge_props:
            return json.dumps(
                {
                    "error": "match_keys에 해당하는 속성이 없습니다. "
                    "중복 방지를 위해 최소 1개 이상의 키를 지정하세요."
                },
                ensure_ascii=False,
            )

        set_clause = ", ".join(f"n.{key} = ${key}" for key in props.keys())
        merge_clause = ", ".join(f"{key}: $merge_{key}" for key in merge_props.keys())

        query = f"""
        MERGE (n:_Entity:{class_name} {{ {merge_clause} }})
        SET {set_clause},
            n.updated_at = datetime()
        ON CREATE SET n.created_at = datetime()
        RETURN n
        """

        params: dict[str, Any] = dict(props)
        params.update({f"merge_{key}": value for key, value in merge_props.items()})

        rows = _run_query(query, params)
        if rows:
            node = rows[0]["n"]
            return json.dumps(
                {"status": "ok", "entity": _node_to_dict(node)},
                ensure_ascii=False,
                default=str,
            )
        return json.dumps({"status": "ok"}, ensure_ascii=False)
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def relationship_create(
    from_entity_id: str,
    to_entity_id: str,
    relationship_type: str,
    properties: str = "{}",
) -> str:
    """Create a relationship between two entities."""

    try:
        props = json.loads(properties) if properties else {}
        set_props = ""
        if props:
            assignments = [f"r.{key} = ${key}" for key in props.keys()]
            set_props = ", " + ", ".join(assignments)

        query = f"""
        MATCH (from_n:_Entity) WHERE elementId(from_n) = $from_entity_id
        MATCH (to_n:_Entity) WHERE elementId(to_n) = $to_entity_id
        MERGE (from_n)-[r:{relationship_type}]->(to_n)
        SET r.updated_at = datetime(){set_props}
        ON CREATE SET r.created_at = datetime()
        RETURN from_n, r, to_n
        """
        params = {
            "from_entity_id": from_entity_id,
            "to_entity_id": to_entity_id,
            **props,
        }

        rows = _run_query(query, params)
        if rows:
            rel = rows[0]["r"]
            return json.dumps(
                {"status": "ok", "relationship": {"type": rel.type, **dict(rel)}},
                ensure_ascii=False,
                default=str,
            )
        return json.dumps({"status": "ok"}, ensure_ascii=False)
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
