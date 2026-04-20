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
    if isinstance(node, dict):
        return dict(node)
    if not hasattr(node, "element_id"):
        try:
            return dict(node)
        except Exception:
            return {"value": node}
    return {"id": node.element_id, "labels": list(node.labels), **dict(node)}


_MAX_TOOL_RESULT_CHARS = 3000  # max chars for serialized tool results


def _serialize_rows(rows: list[dict], max_chars: int = _MAX_TOOL_RESULT_CHARS) -> str:
    """Serialize Neo4j rows into JSON-friendly dictionaries, truncating if needed."""

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
    result = json.dumps(serialized, ensure_ascii=False, default=str)
    if len(result) > max_chars:
        return result[:max_chars] + '..."truncated"]'
    return result


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
        raw_criteria = (search_criteria or "").strip()
        use_contains_for_name = False
        if not raw_criteria:
            return json.dumps([], ensure_ascii=False)

        try:
            criteria = json.loads(raw_criteria)
        except json.JSONDecodeError:
            parsed_pairs = {}
            for part in raw_criteria.split(","):
                if ":" not in part:
                    continue
                key, value = part.split(":", maxsplit=1)
                key = key.strip().strip("\"'")
                value = value.strip().strip("\"'")
                if key and value:
                    parsed_pairs[key] = value
            if parsed_pairs:
                criteria = parsed_pairs
            else:
                criteria = {"name": raw_criteria}
                use_contains_for_name = True

        if not criteria:
            return json.dumps([], ensure_ascii=False)

        conditions = []
        for key in criteria:
            if use_contains_for_name and key == "name":
                conditions.append(f"n.{key} CONTAINS ${key}")
            else:
                conditions.append(f"n.{key} = ${key}")
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
        ON CREATE SET n.created_at = datetime()
        SET {set_clause},
            n.updated_at = datetime()
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
        ON CREATE SET r.created_at = datetime()
        SET r.updated_at = datetime(){set_props}
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


def batch_ingest(nodes_json: str) -> str:
    """Batch-ingest nodes and relationships from a parsed JSON file path or JSON string containing nodes and relationships arrays."""

    import os
    from pathlib import Path

    try:
        data_str = nodes_json.strip()

        # If it looks like a file path, read the file
        if not data_str.startswith("{") and not data_str.startswith("["):
            file_path = Path(data_str)
            if not file_path.exists():
                return json.dumps({"error": f"파일을 찾을 수 없습니다: {data_str}"}, ensure_ascii=False)
            data_str = file_path.read_text(encoding="utf-8")

        data = json.loads(data_str)
        nodes = data.get("nodes", [])
        relationships = data.get("relationships", [])

        node_count = 0
        rel_count = 0
        errors = []

        # Ingest nodes
        for node in nodes:
            node_id = node.get("id", "")
            class_name = node.get("class", "")
            props = node.get("properties", {})
            parent_id = node.get("parent_id")

            if not class_name:
                errors.append(f"노드에 class가 없습니다: {node_id}")
                continue

            # Truncate content fields
            for key in list(props.keys()):
                if isinstance(props[key], str) and len(props[key]) > 2000:
                    props[key] = props[key][:2000] + "..."

            props["_source_id"] = node_id
            if parent_id:
                props["_parent_source_id"] = parent_id

            merge_clause = "_source_id: $_source_id"
            set_parts = ", ".join(f"n.{k} = ${k}" for k in props.keys())

            query = f"""
            MERGE (n:_Entity:{class_name} {{ {merge_clause} }})
            ON CREATE SET n.created_at = datetime()
            SET {set_parts}, n.updated_at = datetime()
            RETURN n
            """
            try:
                _run_query(query, props)
                node_count += 1
            except Exception as exc:
                errors.append(f"노드 {node_id}: {exc}")

        # Ingest parent-child CONTAINS relationships
        for node in nodes:
            parent_id = node.get("parent_id")
            if not parent_id:
                continue
            try:
                _run_query(
                    """
                    MATCH (parent:_Entity {_source_id: $parent_id})
                    MATCH (child:_Entity {_source_id: $child_id})
                    MERGE (parent)-[r:CONTAINS]->(child)
                    ON CREATE SET r.created_at = datetime()
                    SET r.updated_at = datetime()
                    """,
                    {"parent_id": parent_id, "child_id": node["id"]},
                )
                rel_count += 1
            except Exception as exc:
                errors.append(f"CONTAINS {parent_id}->{node['id']}: {exc}")

        # Ingest explicit relationships
        for rel in relationships:
            from_id = rel.get("from_id", "")
            to_id = rel.get("to_id", "")
            rel_type = rel.get("type", "RELATED_TO")
            rel_props = rel.get("properties", {})

            set_clause = ""
            if rel_props:
                assignments = [f"r.{k} = ${k}" for k in rel_props.keys()]
                set_clause = ", " + ", ".join(assignments)

            try:
                _run_query(
                    f"""
                    MATCH (a:_Entity {{_source_id: $from_id}})
                    MATCH (b:_Entity {{_source_id: $to_id}})
                    MERGE (a)-[r:{rel_type}]->(b)
                    ON CREATE SET r.created_at = datetime()
                    SET r.updated_at = datetime(){set_clause}
                    """,
                    {"from_id": from_id, "to_id": to_id, **rel_props},
                )
                rel_count += 1
            except Exception as exc:
                errors.append(f"관계 {from_id}-[{rel_type}]->{to_id}: {exc}")

        result = {
            "status": "ok",
            "nodes_created": node_count,
            "relationships_created": rel_count,
        }
        if errors:
            result["errors"] = errors[:20]  # limit error output
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def graph_stats() -> str:
    """Return graph-wide statistics: node counts by class, relationship counts by type."""

    try:
        node_stats = _run_readonly_query("""
            MATCH (n:_Entity)
            WITH [l IN labels(n) WHERE l <> '_Entity'] AS entity_labels
            UNWIND entity_labels AS label
            RETURN label AS class, count(*) AS count
            ORDER BY count DESC
        """)
        rel_stats = _run_readonly_query("""
            MATCH (:_Entity)-[r]->(:_Entity)
            RETURN type(r) AS type, count(*) AS count
            ORDER BY count DESC
        """)
        total_nodes = _run_readonly_query("MATCH (n:_Entity) RETURN count(n) AS total")
        total_rels = _run_readonly_query("MATCH (:_Entity)-[r]->(:_Entity) RETURN count(r) AS total")

        return json.dumps({
            "total_nodes": total_nodes[0]["total"] if total_nodes else 0,
            "total_relationships": total_rels[0]["total"] if total_rels else 0,
            "nodes_by_class": [{"class": r["class"], "count": r["count"]} for r in node_stats],
            "relationships_by_type": [{"type": r["type"], "count": r["count"]} for r in rel_stats],
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
