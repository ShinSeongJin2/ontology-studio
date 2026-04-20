"""Neo4j-backed ontology tools and repository helpers."""

from __future__ import annotations

import json
import re
import ast
from hashlib import sha1
from typing import Any

from neo4j import GraphDatabase, READ_ACCESS

from ...shared.kernel.settings import get_settings
from .layered_schema import (
    ALL_LAYERS,
    ALLOWED_PROPERTY_TYPES,
    get_class_property_templates,
    get_layer_class_definitions,
    get_layer_labels,
    get_layer_prefix,
    get_relationship_definitions,
    normalize_layer_name,
    normalize_relationship_type,
    validate_layer_class_name,
    validate_relationship_rule,
)
from .schema_models import (
    OntologyLabelModel,
    OntologyNodeModel,
    OntologyNodePropertyModel,
    OntologyRelationshipModel,
    OntologySchemaModel,
)

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
    payload = {"element_id": node.element_id, "labels": list(node.labels), **dict(node)}
    payload["id"] = dict(node).get("id") or node.element_id
    return payload


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


def _slugify_identifier(value: str) -> str:
    """Convert an arbitrary string into a safe snake_case identifier."""

    normalized = re.sub(r"[^0-9A-Za-z가-힣]+", "_", str(value or "").strip().lower())
    normalized = normalized.strip("_")
    return normalized or "unnamed"


def _relationship_definition_key(name: str, from_class: str, to_class: str) -> str:
    """Return a stable composite key for relationship type definitions."""

    return f"{normalize_relationship_type(name)}::{validate_layer_class_name(from_class)}::{validate_layer_class_name(to_class)}"


def _build_stable_entity_id(layer_name: str, name: str) -> str:
    """Return a stable fixed-layer entity id based on name."""

    normalized_layer = validate_layer_class_name(layer_name)
    prefix = get_layer_prefix(normalized_layer)
    slug = _slugify_identifier(name)
    if slug.startswith(prefix):
        return slug
    return f"{prefix}{slug}"


def _resolve_layer_from_node(node) -> str:
    """Resolve a fixed layer from a Neo4j node or dictionary."""

    if node is None:
        return ""
    if hasattr(node, "labels"):
        labels = list(node.labels)
        for label in labels:
            normalized = normalize_layer_name(label)
            if normalized:
                return normalized
        return normalize_layer_name(dict(node).get("layer"))
    if isinstance(node, dict):
        for label in node.get("labels", []):
            normalized = normalize_layer_name(label)
            if normalized:
                return normalized
        return normalize_layer_name(node.get("layer"))
    return ""


def _normalize_schema_property_definitions(properties: list[dict] | None) -> list[dict]:
    """Normalize schema property definitions into the supported property type set."""

    normalized_props = []
    for prop in properties or []:
        prop_name = str(prop.get("name") or "").strip()
        if not prop_name:
            continue
        prop_type = str(prop.get("type") or "").strip().lower()
        if prop_type not in ALLOWED_PROPERTY_TYPES:
            prop_type = "string"
        normalized_props.append(
            {
                "name": prop_name,
                "type": prop_type,
                "required": bool(prop.get("required")),
                "description": str(prop.get("description") or "").strip(),
            }
        )
    return normalized_props


def _prepare_neo4j_property_map(raw_props: dict[str, Any]) -> dict[str, Any]:
    """Convert nested Python values into Neo4j-storable properties."""

    sanitized: dict[str, Any] = {}
    for key, value in raw_props.items():
        if value is None:
            continue
        if key == "position" and isinstance(value, dict):
            x_value = value.get("x")
            y_value = value.get("y")
            if isinstance(x_value, (int, float)) and isinstance(y_value, (int, float)):
                sanitized["position_x"] = float(x_value)
                sanitized["position_y"] = float(y_value)
            continue
        if isinstance(value, dict):
            sanitized[key] = json.dumps(value, ensure_ascii=False)
            continue
        if isinstance(value, list):
            if all(isinstance(item, (str, int, float, bool)) or item is None for item in value):
                sanitized[key] = value
            else:
                sanitized[key] = json.dumps(value, ensure_ascii=False)
            continue
        sanitized[key] = value
    return sanitized


def _parse_json_string_if_possible(value: Any) -> Any:
    """Best-effort JSON decode for serialized structured properties."""

    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _make_json_safe(value: Any) -> Any:
    """Convert Neo4j-specific values into JSON-serializable structures."""

    if isinstance(value, dict):
        return {str(key): _make_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_make_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _lookup_entity_element_id(class_name: str, stable_id: str) -> str:
    """Resolve an entity's Neo4j element id by fixed layer and stable id."""

    normalized_class_name = validate_layer_class_name(class_name)
    rows = _run_readonly_query(
        f"""
        MATCH (n:_Entity:{normalized_class_name} {{id: $stable_id}})
        RETURN elementId(n) AS element_id
        LIMIT 1
        """,
        {"stable_id": stable_id},
    )
    if not rows:
        return ""
    return str(rows[0].get("element_id") or "")


def seed_layered_ontology_schema() -> str:
    """Seed the fixed 5-layer ontology classes and relationship definitions."""

    try:
        class_definitions = get_layer_class_definitions()
        relationship_definitions = get_relationship_definitions()
        driver = get_driver()
        with driver.session() as session:
            for class_item in class_definitions:
                session.run(
                    """
                    MERGE (c:_OntologyClass {name: $name})
                    SET c.description = $description,
                        c.layer = $name,
                        c.properties = $properties,
                        c.updated_at = datetime()
                    """,
                    {
                        "name": class_item["name"],
                        "description": class_item["description"],
                        "properties": json.dumps(
                            _normalize_schema_property_definitions(class_item["properties"]),
                            ensure_ascii=False,
                        ),
                    },
                )
            for rel in relationship_definitions:
                session.run(
                    """
                    MATCH (from_c:_OntologyClass {name: $from_class})
                    MATCH (to_c:_OntologyClass {name: $to_class})
                    MERGE (r:_OntologyRelationshipType {key: $key})
                    SET r.name = $name,
                        r.description = $description,
                        r.from_class = $from_class,
                        r.to_class = $to_class,
                        r.properties = $properties,
                        r.updated_at = datetime()
                    MERGE (r)-[:FROM_CLASS]->(from_c)
                    MERGE (r)-[:TO_CLASS]->(to_c)
                    """,
                    {
                        "key": _relationship_definition_key(rel.name, rel.from_class, rel.to_class),
                        "name": rel.name,
                        "description": rel.description,
                        "from_class": rel.from_class,
                        "to_class": rel.to_class,
                        "properties": "[]",
                    },
                )
        return json.dumps(
            {
                "status": "ok",
                "classes": [item["name"] for item in class_definitions],
                "relationship_type_count": len(relationship_definitions),
            },
            ensure_ascii=False,
        )
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def reset_ontology_graph(include_documents: bool = False) -> str:
    """Delete existing ontology graph data and optionally document chunks."""

    try:
        driver = get_driver()
        with driver.session() as session:
            session.run("MATCH (n:_Entity) DETACH DELETE n")
            session.run("MATCH (n:_OntologyRelationshipType) DETACH DELETE n")
            session.run("MATCH (n:_OntologyClass) DETACH DELETE n")
            if include_documents:
                session.run("MATCH (d:Document) DETACH DELETE d")
                session.run("MATCH (c:Chunk) DETACH DELETE c")
        return json.dumps(
            {"status": "ok", "include_documents": include_documents},
            ensure_ascii=False,
        )
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def _convert_neo4j_value_to_property_type(value: Any) -> str:
    """Map a Neo4j property value to a frontend schema property type."""

    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list) or isinstance(value, dict):
        return "json"
    return "string"


def project_graph_to_schema(
    name: str = "Layered Ontology Snapshot",
    description: str = "",
    domain: str = "",
) -> str:
    """Project the current Neo4j ontology graph into OntologySchema JSON."""

    try:
        driver = get_driver()
        with driver.session(default_access_mode=READ_ACCESS) as session:
            result = session.run(
                """
                MATCH (n:_Entity)
                OPTIONAL MATCH (n)-[r]->(m:_Entity)
                RETURN n, r, m
                """
            )
            nodes_by_id: dict[str, OntologyNodeModel] = {}
            relationships: dict[str, OntologyRelationshipModel] = {}
            for record in result:
                node = record["n"]
                target = record["m"]
                rel = record["r"]
                for candidate in (node, target):
                    if candidate is None:
                        continue
                    props = dict(candidate)
                    stable_id = str(props.get("id") or getattr(candidate, "element_id", ""))
                    if not stable_id or stable_id in nodes_by_id:
                        continue
                    layer_name = _resolve_layer_from_node(candidate)
                    property_defs = [
                        OntologyNodePropertyModel(
                            name=key,
                            type=_convert_neo4j_value_to_property_type(value),
                            description="",
                            required=key in {"id", "name", "description"},
                        )
                        for key, value in props.items()
                        if key
                        not in {
                            "created_at",
                            "updated_at",
                            "position_x",
                            "position_y",
                        }
                    ]
                    schema_node = OntologyNodeModel(
                        id=stable_id,
                        name=str(props.get("name") or stable_id),
                        label=layer_name or str(props.get("layer") or ""),
                        layer=layer_name or str(props.get("layer") or ""),
                        description=str(props.get("description") or "") or None,
                        properties=property_defs,
                        aliases=[str(item) for item in props.get("aliases", []) if str(item).strip()],
                        embeddingTerms=[
                            str(item).strip().lower()
                            for item in props.get("embeddingTerms", [])
                            if str(item).strip()
                        ],
                        unit=str(props.get("unit") or "") or None,
                        formula=str(props.get("formula") or "") or None,
                        targetValue=props.get("targetValue"),
                        thresholds=(
                            _parse_json_string_if_possible(props.get("thresholds"))
                            if isinstance(_parse_json_string_if_possible(props.get("thresholds")), dict)
                            else {}
                        ),
                        source_text=str(props.get("source_text") or "") or None,
                        chunk_ref=str(props.get("chunk_ref") or "") or None,
                        source_page=props.get("source_page"),
                        document_id=str(props.get("document_id") or "") or None,
                        dataSource=str(props.get("dataSource") or "") or None,
                        dataSourceSchema=_parse_json_string_if_possible(props.get("dataSourceSchema")),
                        materializedView=str(props.get("materializedView") or "") or None,
                        instanceCount=1,
                        position=(
                            {
                                "x": float(props["position_x"]),
                                "y": float(props["position_y"]),
                            }
                            if props.get("position_x") is not None and props.get("position_y") is not None
                            else None
                        ),
                    )
                    nodes_by_id[stable_id] = schema_node

                if rel is not None and node is not None and target is not None:
                    source_id = str(dict(node).get("id") or node.element_id)
                    target_id = str(dict(target).get("id") or target.element_id)
                    rel_props = dict(rel)
                    rel_id = str(rel_props.get("id") or getattr(rel, "element_id", ""))
                    if not rel_id:
                        rel_hash = sha1(
                            f"{source_id}:{rel.type}:{target_id}".encode("utf-8", errors="ignore")
                        ).hexdigest()[:12]
                        rel_id = f"rel_{rel_hash}"
                    relationships[rel_id] = OntologyRelationshipModel(
                        id=rel_id,
                        source=source_id,
                        target=target_id,
                        type=rel.type,
                        description=str(rel_props.get("description") or "") or None,
                        properties=_make_json_safe(rel_props),
                    )

        projected_schema = OntologySchemaModel(
            name=name,
            description=description or None,
            domain=domain or None,
            nodes=sorted(nodes_by_id.values(), key=lambda item: (item.layer, item.name, item.id)),
            relationships=sorted(
                relationships.values(),
                key=lambda item: (item.type, item.source, item.target),
            ),
            labels=[OntologyLabelModel(**item) for item in get_layer_labels()],
        )
        return projected_schema.model_dump_json(indent=2)
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def _extract_model_text(content: Any) -> str:
    """Extract text content from LangChain/OpenAI response payloads."""

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_extract_model_text(item) for item in content)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if "content" in content:
            return _extract_model_text(content["content"])
    return str(content)


def _extract_json_object(text: str) -> dict[str, Any]:
    """Best-effort JSON object extraction from a model response."""

    stripped = text.strip()
    if stripped.startswith("```"):
        fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
        if fence_match:
            stripped = fence_match.group(1)
    nodes_match = re.search(r"\{\s*[\"']nodes[\"']\s*:", stripped, re.DOTALL)
    if nodes_match:
        stripped = stripped[nodes_match.start() :]
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        object_match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if object_match:
            candidate = object_match.group(0)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    pass
        parsed = ast.literal_eval(stripped)
        if isinstance(parsed, dict):
            return parsed
        raise


def bootstrap_layered_ontology(
    query: str,
    top_k: int = 5,
    document_ids: str = "[]",
) -> str:
    """Search evidence chunks, extract fixed-layer ontology JSON, and store it."""

    try:
        normalized_query = (query or "").strip()
        if not normalized_query:
            return json.dumps({"error": "query is required"}, ensure_ascii=False)

        from ...shared.kernel.model_profiles import (
            resolve_model_profile,
            should_use_openai_responses_api,
        )
        from ..document_indexing.service import DocumentIndexingService

        seed_payload = json.loads(seed_layered_ontology_schema())
        if seed_payload.get("error"):
            return json.dumps(seed_payload, ensure_ascii=False)

        settings = get_settings()
        profile = resolve_model_profile(
            purpose="minor",
            model_name=settings.minor_model,
            reasoning_effort=settings.minor_model_reasoning_effort,
            openai_base_url=settings.openai_base_url,
            openai_api_key=settings.openai_api_key,
        )
        if not profile.is_openai:
            return json.dumps(
                {"error": f"unsupported extractor provider: {profile.provider}"},
                ensure_ascii=False,
            )

        from langchain_openai import ChatOpenAI

        search_service = DocumentIndexingService()
        document_filter = json.loads(document_ids) if document_ids else []
        chunk_hits = search_service.hybrid_search(
            query=normalized_query,
            top_k=max(1, int(top_k)),
            document_ids=document_filter,
        )
        if not chunk_hits:
            return json.dumps(
                {"error": "해당 질의에 대한 근거 청크를 찾지 못했습니다."},
                ensure_ascii=False,
            )

        chunk_blocks = []
        for index, chunk in enumerate(chunk_hits[: max(3, min(8, int(top_k)))], start=1):
            chunk_blocks.append(
                "\n".join(
                    [
                        f"[chunk {index}]",
                        f"document_id: {chunk.get('document_id')}",
                        f"chunk_ref: {chunk.get('chunk_ref')}",
                        f"source_page: {chunk.get('source_page')}",
                        f"source_text:",
                        str(chunk.get("source_text") or ""),
                    ]
                )
            )

        system_prompt = """당신은 고정 5계층 온톨로지 추출기입니다.
다음 레이어만 사용하세요: KPI, Measure, Driver, Process, Resource.
새 클래스를 만들지 말고 각 개념을 반드시 5계층 중 하나로만 분류하세요.
허용 관계:
- Driver -> Measure: CAUSES, INFLUENCES
- Driver -> Process: INFLUENCES
- Measure -> KPI: MEASURED_AS
- Process -> Measure: PRODUCES
- Resource -> Process: EXECUTES, USED_WHEN
- Process -> Resource: USED_WHEN
- Resource -> Measure: AFFECTS
- KPI -> KPI: EFFECTS
- Measure -> Measure: CAUSES
- Driver -> Driver: CORRELATES_WITH
- Process -> Process: NEXT
- Resource -> Resource: DEPENDS_ON

출력은 JSON object 하나만 사용하고 형식은 반드시 아래와 같아야 합니다.
{
  "nodes": [
    {
      "id": "measure_example",
      "class_name": "Measure",
      "name": "개념 이름",
      "description": "문서 근거 기반 설명",
      "aliases": ["별칭"],
      "embeddingTerms": ["english term 1", "english term 2", "english term 3", "english term 4", "english term 5"],
      "properties": {"domain_type": "세부 도메인 타입", "domain_role": "세부 역할"},
      "unit": "선택",
      "formula": "선택",
      "targetValue": 0,
      "thresholds": {"warning": 0},
      "source_chunk_ref": "원문 chunk_ref",
      "source_page": 1,
      "document_id": "문서 id",
      "source_text": "원문 일부"
    }
  ],
  "relationships": [
    {
      "source_id": "measure_example",
      "target_id": "kpi_example",
      "relationship_type": "MEASURED_AS",
      "description": "근거 기반 관계 설명"
    }
  ]
}

규칙:
- nodes는 query에 직접 필요한 핵심 개념만 4~12개 정도 추출
- 같은 개념의 파생 표현은 하나로 합치고 domain_type/properties로 보존
- 수위/용량/빈도/비율/기간/정량값은 보통 Measure
- 조건/시기/운영 기준/제약은 보통 Driver
- 기관/사람/설비/댐/하천/제방/수문은 보통 Resource
- 요청/승인/분석/방류/점검/모니터링은 보통 Process
- 목표/효과/성과 평가는 KPI
- 각 node는 반드시 source_chunk_ref, source_page, document_id, source_text를 포함
- relationships는 nodes에 있는 id만 참조
- 문서 근거 없는 관계는 만들지 말 것
- JSON 외 다른 텍스트 금지"""

        user_prompt = "\n\n".join(
            [
                f"질문/목표: {normalized_query}",
                "아래 evidence chunks만 사용해서 5계층 ontology를 만들어 주세요.",
                *chunk_blocks,
            ]
        )

        model = ChatOpenAI(
            model=profile.model_name,
            api_key=profile.api_key,
            temperature=0,
            reasoning_effort=profile.reasoning_effort,
            use_responses_api=should_use_openai_responses_api(profile),
            streaming=False,
            **({"base_url": profile.base_url} if profile.base_url else {}),
        )
        response = model.invoke(
            [
                ("system", system_prompt),
                ("human", user_prompt),
            ]
        )
        raw_response_text = _extract_model_text(response.content)
        try:
            payload = _extract_json_object(raw_response_text)
        except Exception as exc:
            return json.dumps(
                {
                    "error": str(exc),
                    "raw_response": raw_response_text[:4000],
                },
                ensure_ascii=False,
            )
        raw_nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
        raw_relationships = (
            payload.get("relationships") if isinstance(payload.get("relationships"), list) else []
        )

        created_nodes: dict[str, dict[str, str]] = {}
        name_index: dict[str, str] = {}
        creation_errors: list[dict[str, str]] = []

        for raw_node in raw_nodes:
            if not isinstance(raw_node, dict):
                continue
            class_name = validate_layer_class_name(
                str(raw_node.get("class_name") or raw_node.get("layer") or "")
            )
            stable_id = str(raw_node.get("id") or "").strip() or _build_stable_entity_id(
                class_name,
                str(raw_node.get("name") or ""),
            )
            node_props = dict(raw_node.get("properties") or {})
            node_props.update(
                {
                    "id": stable_id,
                    "name": str(raw_node.get("name") or "").strip(),
                    "description": str(raw_node.get("description") or "").strip(),
                    "aliases": raw_node.get("aliases") or [],
                    "embeddingTerms": raw_node.get("embeddingTerms") or [],
                    "unit": raw_node.get("unit"),
                    "formula": raw_node.get("formula"),
                    "targetValue": raw_node.get("targetValue"),
                    "thresholds": raw_node.get("thresholds") or {},
                    "chunk_ref": raw_node.get("source_chunk_ref"),
                    "source_page": raw_node.get("source_page"),
                    "document_id": raw_node.get("document_id"),
                    "source_text": raw_node.get("source_text"),
                    "domain_type": node_props.get("domain_type") or class_name,
                }
            )
            create_result = json.loads(
                entity_create(
                    class_name=class_name,
                    properties=json.dumps(node_props, ensure_ascii=False),
                    match_keys=json.dumps(["id"], ensure_ascii=False),
                )
            )
            if create_result.get("error"):
                creation_errors.append(
                    {"node_id": stable_id, "error": str(create_result["error"])}
                )
                continue
            entity_payload = create_result.get("entity") or {}
            element_id = str(entity_payload.get("element_id") or "").strip()
            if not element_id:
                element_id = _lookup_entity_element_id(class_name, stable_id)
            created_nodes[stable_id] = {
                "element_id": element_id,
                "stable_id": str(entity_payload.get("id") or stable_id),
            }
            if node_props["name"]:
                name_index[str(node_props["name"]).strip()] = stable_id

        created_relationships = []
        for raw_relationship in raw_relationships:
            if not isinstance(raw_relationship, dict):
                continue
            source_key = str(raw_relationship.get("source_id") or "").strip()
            target_key = str(raw_relationship.get("target_id") or "").strip()
            if not source_key and raw_relationship.get("source_name"):
                source_key = name_index.get(str(raw_relationship["source_name"]).strip(), "")
            if not target_key and raw_relationship.get("target_name"):
                target_key = name_index.get(str(raw_relationship["target_name"]).strip(), "")
            source_entity = created_nodes.get(source_key)
            target_entity = created_nodes.get(target_key)
            if not source_entity or not target_entity:
                continue
            rel_result = json.loads(
                relationship_create(
                    from_entity_id=source_entity["element_id"],
                    to_entity_id=target_entity["element_id"],
                    relationship_type=str(raw_relationship.get("relationship_type") or ""),
                    properties=json.dumps(
                        {"description": str(raw_relationship.get("description") or "").strip()},
                        ensure_ascii=False,
                    ),
                )
            )
            if rel_result.get("error"):
                creation_errors.append(
                    {
                        "relationship": f"{source_key}->{target_key}",
                        "error": str(rel_result["error"]),
                    }
                )
                continue
            created_relationships.append(rel_result.get("relationship") or {})

        return json.dumps(
            {
                "status": "ok",
                "query": normalized_query,
                "chunk_count": len(chunk_hits),
                "created_node_count": len(created_nodes),
                "created_relationship_count": len(created_relationships),
                "created_node_ids": sorted(created_nodes.keys()),
                "errors": creation_errors,
            },
            ensure_ascii=False,
        )
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


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
        normalized_name = validate_layer_class_name(name)
        props = (
            json.loads(properties)
            if properties and properties not in ("[]", "")
            else get_class_property_templates(normalized_name)
        )
        props = _normalize_schema_property_definitions(props)
        rows = _run_query(
            """
            MERGE (c:_OntologyClass {name: $normalized_name})
            SET c.description = $description,
                c.layer = $normalized_name,
                c.properties = $properties,
                c.updated_at = datetime()
            RETURN c
            """,
            {
                "normalized_name": normalized_name,
                "description": description or f"{normalized_name} 계층 클래스",
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
        return json.dumps({"status": "ok", "class": normalized_name}, ensure_ascii=False)
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
        normalized_from = validate_layer_class_name(from_class)
        normalized_to = validate_layer_class_name(to_class)
        normalized_name = validate_relationship_rule(normalized_from, normalized_to, name)
        props = json.loads(properties) if properties else []
        rows = _run_query(
            """
            MATCH (from_c:_OntologyClass {name: $from_class})
            MATCH (to_c:_OntologyClass {name: $to_class})
            MERGE (r:_OntologyRelationshipType {key: $key})
            SET r.description = $description,
                r.from_class = $from_class,
                r.to_class = $to_class,
                r.name = $name,
                r.properties = $properties,
                r.updated_at = datetime()
            MERGE (r)-[:FROM_CLASS]->(from_c)
            MERGE (r)-[:TO_CLASS]->(to_c)
            RETURN r
            """,
            {
                "key": _relationship_definition_key(normalized_name, normalized_from, normalized_to),
                "name": normalized_name,
                "from_class": normalized_from,
                "to_class": normalized_to,
                "description": description or f"{normalized_from} -> {normalized_to} {normalized_name}",
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
                    f"클래스 '{normalized_from}' 또는 '{normalized_to}'를 찾을 수 없습니다. "
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
        schema_relationships.sort(
            key=lambda item: (item["from_class"], item["to_class"], item["name"])
        )

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
        normalized_class_name = validate_layer_class_name(class_name)
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
        MATCH (n:_Entity:{normalized_class_name})
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
        normalized_class_name = validate_layer_class_name(class_name)
        props = json.loads(properties) if properties else {}
        keys = json.loads(match_keys) if match_keys else []
        if not isinstance(props, dict):
            return json.dumps({"error": "properties는 JSON object여야 합니다."}, ensure_ascii=False)
        if not props.get("name"):
            return json.dumps({"error": "entity_create에는 name이 필요합니다."}, ensure_ascii=False)
        if not props.get("description"):
            return json.dumps({"error": "entity_create에는 description이 필요합니다."}, ensure_ascii=False)
        props["layer"] = normalized_class_name
        props["id"] = str(props.get("id") or "").strip() or _build_stable_entity_id(
            normalized_class_name,
            str(props.get("name") or ""),
        )
        props = _prepare_neo4j_property_map(props)
        if not keys:
            keys = ["id"]

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
        MERGE (n:_Entity:{normalized_class_name} {{ {merge_clause} }})
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
        if not isinstance(props, dict):
            return json.dumps({"error": "properties는 JSON object여야 합니다."}, ensure_ascii=False)
        props = _prepare_neo4j_property_map(props)
        driver = get_driver()
        with driver.session(default_access_mode=READ_ACCESS) as session:
            rows = list(
                session.run(
                    """
                    MATCH (from_n:_Entity) WHERE elementId(from_n) = $from_entity_id
                    MATCH (to_n:_Entity) WHERE elementId(to_n) = $to_entity_id
                    RETURN from_n, to_n
                    """,
                    {
                        "from_entity_id": from_entity_id,
                        "to_entity_id": to_entity_id,
                    },
                )
            )
        if not rows:
            return json.dumps(
                {"error": "source 또는 target 엔티티를 찾을 수 없습니다."},
                ensure_ascii=False,
            )
        from_node = rows[0]["from_n"]
        to_node = rows[0]["to_n"]
        source_layer = _resolve_layer_from_node(from_node)
        target_layer = _resolve_layer_from_node(to_node)
        normalized_relationship_type = validate_relationship_rule(
            source_layer,
            target_layer,
            relationship_type,
        )
        set_props = ""
        if props:
            assignments = [f"r.{key} = ${key}" for key in props.keys()]
            set_props = ", " + ", ".join(assignments)

        query = f"""
        MATCH (from_n:_Entity) WHERE elementId(from_n) = $from_entity_id
        MATCH (to_n:_Entity) WHERE elementId(to_n) = $to_entity_id
        MERGE (from_n)-[r:{normalized_relationship_type}]->(to_n)
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
            return json.dumps(
                {
                    "status": "ok",
                    "relationship": {
                        "type": normalized_relationship_type,
                        "source_layer": source_layer,
                        "target_layer": target_layer,
                        **_make_json_safe(props),
                    },
                },
                ensure_ascii=False,
                default=str,
            )
        return json.dumps({"status": "ok"}, ensure_ascii=False)
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
