"""Fixed layered ontology definitions and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass

ALL_LAYERS = ("KPI", "Measure", "Driver", "Process", "Resource")

LAYER_PREFIXES = {
    "KPI": "kpi_",
    "Measure": "measure_",
    "Driver": "driver_",
    "Process": "process_",
    "Resource": "resource_",
}

LAYER_DESCRIPTIONS = {
    "KPI": "정량 목표와 성과 판단 기준을 표현하는 계층",
    "Measure": "시간에 따라 변하는 측정값과 상태값을 표현하는 계층",
    "Driver": "조절 가능 요인과 외부 영향 요인을 표현하는 계층",
    "Process": "입력에서 출력을 만드는 활동과 절차를 표현하는 계층",
    "Resource": "프로세스 수행에 필요한 사람, 시스템, 설비, 자원을 표현하는 계층",
}

LAYER_ICONS = {
    "KPI": "🎯",
    "Measure": "📏",
    "Driver": "🕹️",
    "Process": "⚙️",
    "Resource": "🧰",
}

LAYER_COLORS = {
    "KPI": "#f87171",
    "Measure": "#38bdf8",
    "Driver": "#f59e0b",
    "Process": "#34d399",
    "Resource": "#a78bfa",
}

ALLOWED_PROPERTY_TYPES = {"string", "number", "boolean", "date", "datetime", "json"}

ALLOWED_INTRA_LAYER_RELATIONS = {
    "KPI": {"EFFECTS"},
    "Measure": {"CAUSES"},
    "Driver": {"CORRELATES_WITH"},
    "Process": {"NEXT"},
    "Resource": {"DEPENDS_ON"},
}

ALLOWED_CROSS_LAYER_RELATIONS = {
    ("Driver", "Measure"): {"CAUSES", "INFLUENCES"},
    ("Driver", "Process"): {"INFLUENCES"},
    ("Measure", "KPI"): {"MEASURED_AS"},
    ("Process", "Measure"): {"PRODUCES"},
    ("Resource", "Process"): {"EXECUTES", "USED_WHEN"},
    ("Process", "Resource"): {"USED_WHEN"},
    ("Resource", "Measure"): {"AFFECTS"},
}


@dataclass(frozen=True)
class LayerRelationshipDefinition:
    """Serializable schema relationship definition for a fixed layered graph."""

    name: str
    from_class: str
    to_class: str
    description: str


def normalize_layer_name(value: str | None) -> str:
    """Return a canonical layer name or an empty string."""

    if value is None:
        return ""
    normalized = str(value).strip()
    for layer_name in ALL_LAYERS:
        if normalized.lower() == layer_name.lower():
            return layer_name
    return ""


def normalize_relationship_type(value: str | None) -> str:
    """Return an upper snake case relationship type."""

    if value is None:
        return ""
    return str(value).strip().upper()


def is_valid_layer_name(value: str | None) -> bool:
    """Return True when the value maps to a supported fixed layer."""

    return bool(normalize_layer_name(value))


def get_layer_prefix(layer_name: str) -> str:
    """Return the stable identifier prefix for a layer."""

    normalized = normalize_layer_name(layer_name)
    if not normalized:
        raise ValueError(f"지원하지 않는 레이어입니다: {layer_name}")
    return LAYER_PREFIXES[normalized]


def get_layer_label_definition(layer_name: str) -> dict[str, str]:
    """Return the frontend-facing label metadata for a layer."""

    normalized = normalize_layer_name(layer_name)
    if not normalized:
        raise ValueError(f"지원하지 않는 레이어입니다: {layer_name}")
    return {
        "name": normalized,
        "description": LAYER_DESCRIPTIONS[normalized],
        "icon": LAYER_ICONS[normalized],
        "color": LAYER_COLORS[normalized],
    }


def get_layer_labels() -> list[dict[str, str]]:
    """Return all fixed layer labels for frontend schema rendering."""

    return [get_layer_label_definition(layer_name) for layer_name in ALL_LAYERS]


def get_class_property_templates(layer_name: str) -> list[dict[str, object]]:
    """Return stable property templates for each layer class."""

    normalized = normalize_layer_name(layer_name)
    if not normalized:
        raise ValueError(f"지원하지 않는 레이어입니다: {layer_name}")

    common_props = [
        {"name": "id", "type": "string", "required": True, "description": "안정 식별자"},
        {"name": "name", "type": "string", "required": True, "description": "표시 이름"},
        {
            "name": "description",
            "type": "string",
            "required": True,
            "description": "문서 근거를 반영한 설명",
        },
        {
            "name": "embeddingTerms",
            "type": "json",
            "required": False,
            "description": "영문 검색/병합을 위한 embedding terms",
        },
        {
            "name": "source_text",
            "type": "string",
            "required": False,
            "description": "문서 원문 근거",
        },
        {
            "name": "chunk_ref",
            "type": "string",
            "required": False,
            "description": "청크 참조 문자열",
        },
        {
            "name": "source_page",
            "type": "number",
            "required": False,
            "description": "원문 페이지 번호",
        },
        {
            "name": "document_id",
            "type": "string",
            "required": False,
            "description": "문서 식별자",
        },
    ]

    layer_specific_props = {
        "KPI": [
            {"name": "unit", "type": "string", "required": False, "description": "지표 단위"},
            {
                "name": "targetValue",
                "type": "number",
                "required": False,
                "description": "목표값",
            },
            {
                "name": "thresholds",
                "type": "json",
                "required": False,
                "description": "임계값 맵",
            },
            {"name": "formula", "type": "string", "required": False, "description": "산식"},
        ],
        "Measure": [
            {"name": "unit", "type": "string", "required": False, "description": "측정 단위"},
            {
                "name": "measurement_point",
                "type": "string",
                "required": False,
                "description": "측정 위치",
            },
            {"name": "formula", "type": "string", "required": False, "description": "산식"},
        ],
        "Driver": [
            {
                "name": "driver_type",
                "type": "string",
                "required": False,
                "description": "원인 또는 제어 요인 구분",
            },
            {
                "name": "controllable",
                "type": "boolean",
                "required": False,
                "description": "제어 가능 여부",
            },
        ],
        "Process": [
            {"name": "order", "type": "number", "required": False, "description": "단계 순서"},
            {
                "name": "processType",
                "type": "string",
                "required": False,
                "description": "프로세스 유형",
            },
        ],
        "Resource": [
            {
                "name": "resourceType",
                "type": "string",
                "required": False,
                "description": "자원 유형",
            },
            {"name": "provider", "type": "string", "required": False, "description": "제공 주체"},
        ],
    }
    return [*common_props, *layer_specific_props.get(normalized, [])]


def get_layer_class_definitions() -> list[dict[str, object]]:
    """Return the fixed ontology class definitions."""

    return [
        {
            "name": layer_name,
            "description": LAYER_DESCRIPTIONS[layer_name],
            "properties": get_class_property_templates(layer_name),
        }
        for layer_name in ALL_LAYERS
    ]


def get_relationship_definitions() -> list[LayerRelationshipDefinition]:
    """Return fixed relationship definitions for all supported layer pairs."""

    definitions: list[LayerRelationshipDefinition] = []
    for layer_name, relation_types in ALLOWED_INTRA_LAYER_RELATIONS.items():
        for relation_type in sorted(relation_types):
            definitions.append(
                LayerRelationshipDefinition(
                    name=relation_type,
                    from_class=layer_name,
                    to_class=layer_name,
                    description=f"{layer_name} 계층 내부의 {relation_type} 관계",
                )
            )
    for (source_layer, target_layer), relation_types in ALLOWED_CROSS_LAYER_RELATIONS.items():
        for relation_type in sorted(relation_types):
            definitions.append(
                LayerRelationshipDefinition(
                    name=relation_type,
                    from_class=source_layer,
                    to_class=target_layer,
                    description=f"{source_layer}에서 {target_layer}로 향하는 {relation_type} 관계",
                )
            )
    return definitions


def validate_layer_class_name(class_name: str) -> str:
    """Return the canonical layer class name or raise."""

    normalized = normalize_layer_name(class_name)
    if not normalized:
        allowed_names = ", ".join(ALL_LAYERS)
        raise ValueError(
            f"지원하지 않는 클래스입니다: {class_name}. 5계층 클래스({allowed_names})만 사용할 수 있습니다."
        )
    return normalized


def get_allowed_relationship_types(source_layer: str, target_layer: str) -> set[str]:
    """Return the allowed relationship types for a source/target layer pair."""

    source_normalized = validate_layer_class_name(source_layer)
    target_normalized = validate_layer_class_name(target_layer)
    if source_normalized == target_normalized:
        return set(ALLOWED_INTRA_LAYER_RELATIONS.get(source_normalized, set()))
    return set(ALLOWED_CROSS_LAYER_RELATIONS.get((source_normalized, target_normalized), set()))


def validate_relationship_rule(
    source_layer: str,
    target_layer: str,
    relationship_type: str,
) -> str:
    """Validate a fixed layered relationship and return its normalized type."""

    normalized_type = normalize_relationship_type(relationship_type)
    allowed_types = get_allowed_relationship_types(source_layer, target_layer)
    if normalized_type not in allowed_types:
        allowed_description = ", ".join(sorted(allowed_types)) or "없음"
        raise ValueError(
            "허용되지 않은 관계입니다. "
            f"{source_layer} -> {target_layer} 에서는 {allowed_description}만 사용할 수 있습니다."
        )
    return normalized_type
