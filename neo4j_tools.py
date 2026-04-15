"""
Neo4j 도구 모듈 - Ontology Studio
온톨로지 스키마 관리, 엔티티/관계 CRUD, Cypher 쿼리 실행
"""

import json
import os
from typing import Any

from neo4j import GraphDatabase

# ─── Neo4j 드라이버 싱글톤 ───

_driver = None


def get_driver():
    """Neo4j 드라이버 싱글톤 반환"""
    global _driver
    if _driver is None:
        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "12345678")
        _driver = GraphDatabase.driver(uri, auth=(user, password))
    return _driver


def _run_query(query: str, params: dict | None = None) -> list[dict]:
    """Cypher 쿼리 실행 헬퍼"""
    driver = get_driver()
    with driver.session() as session:
        result = session.run(query, params or {})
        return [record.data() for record in result]


def _node_to_dict(node) -> dict:
    """Neo4j Node → dict 변환"""
    if node is None:
        return {}
    return {"id": node.element_id, "labels": list(node.labels), **dict(node)}


# ─── 도구 함수들 (create_deep_agent에 등록) ───


def neo4j_cypher(query: str, params: str = "{}") -> str:
    """Neo4j에서 임의의 Cypher 쿼리를 실행합니다.
    복잡한 쿼리나 데이터 조회에 사용하세요.

    Args:
        query: 실행할 Cypher 쿼리 문자열. 예: "MATCH (n) RETURN n LIMIT 10"
        params: JSON 형태의 쿼리 파라미터. 예: '{"name": "홍길동"}'

    Returns:
        쿼리 결과를 JSON 문자열로 반환
    """
    try:
        p = json.loads(params) if params else {}
        rows = _run_query(query, p)
        # Node/Relationship 객체를 직렬화
        serialized = []
        for row in rows:
            sr = {}
            for k, v in row.items():
                if hasattr(v, "element_id"):
                    sr[k] = _node_to_dict(v)
                elif hasattr(v, "type"):  # Relationship
                    sr[k] = {"type": v.type, **dict(v)}
                else:
                    sr[k] = v
            serialized.append(sr)
        return json.dumps(serialized, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def schema_create_class(name: str, description: str = "", properties: str = "[]") -> str:
    """온톨로지 클래스(엔티티 유형)를 생성하거나 업데이트합니다.
    예: Person, Company, Location 등의 엔티티 유형을 정의할 때 사용합니다.

    Args:
        name: 클래스 이름 (예: "Person", "Company"). 영문 PascalCase 권장.
        description: 클래스 설명 (예: "사람을 나타내는 엔티티")
        properties: 속성 정의 JSON 배열. 예: '[{"name": "이름", "type": "string", "required": true}, {"name": "나이", "type": "integer"}]'

    Returns:
        생성된 클래스 정보 JSON
    """
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
            {"name": name, "description": description, "properties": json.dumps(props, ensure_ascii=False)},
        )
        if rows:
            node = rows[0]["c"]
            return json.dumps({"status": "ok", "class": _node_to_dict(node)}, ensure_ascii=False, default=str)
        return json.dumps({"status": "ok", "class": name}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def schema_create_relationship_type(
    name: str,
    from_class: str,
    to_class: str,
    description: str = "",
    properties: str = "[]",
) -> str:
    """온톨로지 관계 유형을 정의합니다.
    두 클래스 간의 관계를 정의할 때 사용합니다. 예: Person -[WORKS_AT]-> Company

    Args:
        name: 관계 유형 이름 (예: "WORKS_AT", "LOCATED_IN"). 영문 UPPER_SNAKE_CASE 권장.
        from_class: 출발 클래스 이름 (예: "Person")
        to_class: 도착 클래스 이름 (예: "Company")
        description: 관계 설명 (예: "사람이 회사에서 근무하는 관계")
        properties: 관계 속성 정의 JSON 배열. 예: '[{"name": "since", "type": "date"}]'

    Returns:
        생성된 관계 유형 정보 JSON
    """
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
            {"error": f"클래스 '{from_class}' 또는 '{to_class}'를 찾을 수 없습니다. 먼저 schema_create_class로 클래스를 생성하세요."},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def schema_get() -> str:
    """현재 온톨로지 스키마 전체를 조회합니다.
    정의된 모든 클래스와 관계 유형을 반환합니다.

    Returns:
        스키마 정보 JSON: {"classes": [...], "relationships": [...]}
    """
    try:
        classes = _run_query(
            "MATCH (c:_OntologyClass) RETURN c ORDER BY c.name"
        )
        rels = _run_query(
            """
            MATCH (r:_OntologyRelationshipType)
            OPTIONAL MATCH (r)-[:FROM_CLASS]->(fc:_OntologyClass)
            OPTIONAL MATCH (r)-[:TO_CLASS]->(tc:_OntologyClass)
            RETURN r, fc.name AS from_class, tc.name AS to_class
            ORDER BY r.name
            """
        )

        schema_classes = []
        for row in classes:
            node = row["c"]
            nd = _node_to_dict(node) if hasattr(node, "element_id") else node
            props_raw = nd.get("properties", "[]")
            try:
                props = json.loads(props_raw) if isinstance(props_raw, str) else props_raw
            except (json.JSONDecodeError, TypeError):
                props = []
            schema_classes.append({
                "name": nd.get("name", ""),
                "description": nd.get("description", ""),
                "properties": props,
            })

        schema_rels = []
        for row in rels:
            node = row["r"]
            nd = _node_to_dict(node) if hasattr(node, "element_id") else node
            props_raw = nd.get("properties", "[]")
            try:
                props = json.loads(props_raw) if isinstance(props_raw, str) else props_raw
            except (json.JSONDecodeError, TypeError):
                props = []
            schema_rels.append({
                "name": nd.get("name", ""),
                "from_class": row.get("from_class") or nd.get("from_class", ""),
                "to_class": row.get("to_class") or nd.get("to_class", ""),
                "description": nd.get("description", ""),
                "properties": props,
            })

        return json.dumps(
            {"classes": schema_classes, "relationships": schema_rels},
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:
        return json.dumps({"error": str(e), "classes": [], "relationships": []}, ensure_ascii=False)


def entity_create(class_name: str, properties: str, match_keys: str = "") -> str:
    """엔티티 인스턴스를 생성합니다. MERGE를 사용하여 동일 엔티티가 이미 있으면 업데이트합니다.
    문서에서 추출한 엔티티를 Neo4j에 저장할 때 사용합니다.

    Args:
        class_name: 엔티티의 클래스 이름 (예: "Person", "Company"). schema_create_class로 미리 정의된 클래스여야 합니다.
        properties: 엔티티 속성 JSON 객체. 예: '{"name": "홍길동", "age": 30, "role": "엔지니어"}'
        match_keys: MERGE 시 매칭에 사용할 키 목록 (쉼표 구분). 비어있으면 "name" 사용. 예: "name,company"

    Returns:
        생성/업데이트된 엔티티 정보 JSON (_uuid 포함)
    """
    try:
        props = json.loads(properties)
        keys = [k.strip() for k in match_keys.split(",") if k.strip()] if match_keys else ["name"]

        # MERGE 조건 생성
        merge_conditions = ", ".join(f"{k}: ${k}" for k in keys if k in props)
        if not merge_conditions:
            # fallback: 모든 속성으로 MERGE
            merge_conditions = ", ".join(f"{k}: ${k}" for k in props.keys())

        # SET 절 생성 (MERGE 키 제외한 나머지 속성)
        set_parts = []
        for k, v in props.items():
            if k not in keys:
                set_parts.append(f"e.{k} = ${k}")

        set_clause = ", ".join(set_parts)
        on_create_set = f", {set_clause}" if set_clause else ""
        on_match_set = f"ON MATCH SET {set_clause}, e._updated_at = datetime()" if set_clause else "ON MATCH SET e._updated_at = datetime()"

        query = f"""
            MERGE (e:_Entity:`{class_name}` {{{merge_conditions}}})
            ON CREATE SET e._uuid = randomUUID(), e._created_at = datetime(){on_create_set}
            {on_match_set}
            RETURN e
        """

        rows = _run_query(query, props)
        if rows:
            node = rows[0]["e"]
            return json.dumps(
                {"status": "ok", "entity": _node_to_dict(node)},
                ensure_ascii=False,
                default=str,
            )
        return json.dumps({"status": "ok", "class": class_name}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def entity_search(class_name: str, search_criteria: str = "{}") -> str:
    """기존 엔티티를 검색합니다. 새 엔티티를 생성하기 전에 중복 확인용으로 사용하세요.

    Args:
        class_name: 검색할 클래스 이름 (예: "Person")
        search_criteria: 검색 조건 JSON. 예: '{"name": "홍길동"}' 또는 '{"_search": "홍길동"}' (이름 부분 일치 검색)

    Returns:
        매칭된 엔티티 목록 JSON
    """
    try:
        criteria = json.loads(search_criteria) if search_criteria else {}

        if "_search" in criteria:
            # 부분 일치 검색 (name 필드에 대해)
            search_term = criteria["_search"]
            query = f"""
                MATCH (e:_Entity:`{class_name}`)
                WHERE e.name CONTAINS $search
                RETURN e LIMIT 20
            """
            rows = _run_query(query, {"search": search_term})
        elif criteria:
            # 정확한 조건 검색
            conditions = " AND ".join(f"e.{k} = ${k}" for k in criteria.keys())
            query = f"""
                MATCH (e:_Entity:`{class_name}`)
                WHERE {conditions}
                RETURN e LIMIT 20
            """
            rows = _run_query(query, criteria)
        else:
            # 전체 조회
            query = f"""
                MATCH (e:_Entity:`{class_name}`)
                RETURN e LIMIT 50
            """
            rows = _run_query(query)

        entities = []
        for row in rows:
            node = row["e"]
            entities.append(_node_to_dict(node))

        return json.dumps(
            {"count": len(entities), "entities": entities},
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def relationship_create(
    from_entity_id: str,
    to_entity_id: str,
    relationship_type: str,
    properties: str = "{}",
) -> str:
    """두 엔티티 간의 관계를 생성합니다. MERGE를 사용하여 중복 관계를 방지합니다.
    entity_create로 생성된 엔티티의 _uuid를 사용하세요.

    Args:
        from_entity_id: 출발 엔티티의 _uuid (entity_create 결과에서 확인)
        to_entity_id: 도착 엔티티의 _uuid
        relationship_type: 관계 유형 이름 (예: "WORKS_AT"). schema_create_relationship_type으로 미리 정의된 유형이어야 합니다.
        properties: 관계 속성 JSON 객체. 예: '{"since": "2020-01-01"}'

    Returns:
        생성된 관계 정보 JSON
    """
    try:
        props = json.loads(properties) if properties else {}

        set_clause = ""
        if props:
            set_parts = [f"r.{k} = ${k}" for k in props.keys()]
            set_clause = "SET " + ", ".join(set_parts)

        query = f"""
            MATCH (a:_Entity {{_uuid: $from_id}})
            MATCH (b:_Entity {{_uuid: $to_id}})
            MERGE (a)-[r:`{relationship_type}`]->(b)
            {set_clause}
            RETURN a.name AS from_name, type(r) AS rel_type, b.name AS to_name
        """

        params = {"from_id": from_entity_id, "to_id": to_entity_id, **props}
        rows = _run_query(query, params)

        if rows:
            return json.dumps(
                {"status": "ok", "relationship": rows[0]},
                ensure_ascii=False,
                default=str,
            )
        return json.dumps(
            {"error": f"엔티티를 찾을 수 없습니다. from_id={from_entity_id}, to_id={to_entity_id}"},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
