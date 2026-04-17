"""Agent lifecycle and SSE streaming service."""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from ..document_indexing.service import DocumentIndexingService
from ..document_indexing.tools import hybrid_search_chunks
from ..files.service import ensure_workspace_dirs, list_output_filenames
from ..ontology.tools import (
    entity_create,
    entity_search,
    neo4j_cypher,
    neo4j_cypher_readonly,
    relationship_create,
    schema_create_class,
    schema_create_relationship_type,
    schema_get,
)
from ...shared.logging import log_agent_event
from ...shared.kernel.model_profiles import (
    resolve_model_profile,
    should_use_openai_responses_api,
)
from ...shared.kernel.settings import get_settings
from ...shared.sandbox.docker_backend import DockerSandboxBackend

AgentMode = Literal["build", "answer"]

_agents: dict[AgentMode, object] = {}
_sessions: dict[str, list] = {}

ONTOLOGY_BUILD_SYSTEM_PROMPT = """\
한국어로 응답하세요. 당신은 Ontology Studio 에이전트입니다.

## 역할
사용자가 온톨로지 스키마를 설계하고, OCR + 하이브리드 검색으로 준비된 문서 청크를 근거로 엔티티와 관계를 추출하여 Neo4j에 저장하도록 돕습니다.

## 작업 흐름
1. **스키마 설계**: schema_create_class, schema_create_relationship_type 도구로 온톨로지 스키마를 정의
2. **그래프 범위 파악**: schema_get, entity_search, neo4j_cypher로 현재 스키마/엔티티/관계를 파악
3. **근거 검색**: hybrid_search_chunks로 OCR 청크를 검색하고, 필요하면 graph 탐색 결과의 node id를 target_node_ids로 넘겨 범위를 좁힘
4. **중복 해결(Disambiguation)**: entity_search로 기존 엔티티를 검색하여 동일 엔티티는 재사용
5. **관계 추출**: 엔티티 간 관계를 식별하고 relationship_create로 저장
6. **출처 보존**: 엔티티/관계를 만들 때 가능한 한 source_text, chunk_ref, source_page, document_id 같은 근거 속성을 함께 남김

## 도구 사용 가이드
- schema_get(): 현재 온톨로지 스키마 전체 조회
- schema_create_class(name, description, properties): 새 클래스 정의
- schema_create_relationship_type(name, from_class, to_class, description, properties): 관계 유형 정의
- entity_search(class_name, search_criteria): 중복 확인을 위한 엔티티 검색 (반드시 생성 전에 호출!)
- entity_create(class_name, properties, match_keys): 엔티티 인스턴스 생성 (MERGE로 중복 방지)
- relationship_create(from_entity_id, to_entity_id, relationship_type, properties): 엔티티 간 관계 생성
- neo4j_cypher(query, params): 고급 Cypher 쿼리 직접 실행
- hybrid_search_chunks(query, top_k, target_node_ids, document_ids, chunk_refs, source_pages): OCR 청크에서 BM25 + 벡터 검색을 RRF로 결합한 근거 검색

## 중요 규칙
- 엔티티 생성 전 항상 entity_search로 기존 엔티티 확인하세요
- 업로드 문서는 이미 OCR, 청킹, 임베딩, Document/Chunk 적재가 끝난 상태라고 가정하세요
- execute나 파일 시스템 탐색으로 문서를 다시 파싱하지 마세요. 문서 탐색은 반드시 hybrid_search_chunks와 Neo4j 조회만 사용하세요
- 스키마 클래스명은 영문 PascalCase (예: Person, Company, Accident)
- 관계 유형명은 영문 UPPER_SNAKE_CASE (예: WORKS_AT, OCCURRED_AT)
- entity_create 시 match_keys로 중복 방지 키를 지정하세요
- 각 도구 호출 전에 한국어로 간단히 설명하세요
- 멀티홉 질문이나 구축 의도가 보이면 먼저 그래프를 좁히고, 그 다음 관련 leaf에 대해 hybrid_search_chunks를 호출하세요
- entity나 relationship를 생성할 때는 가능하면 chunk_ref, source_page, source_text를 속성으로 저장해 나중에 다시 검색 범위를 좁힐 수 있게 하세요
- 사용자가 제공한 구축 의도(intent)와 Golden Question을 최우선 목표로 삼으세요
- 온톨로지 구축 완료 기준은 "현재 그래프와 스키마만으로 Golden Question에 답할 수 있는가" 입니다
- Golden Question에 충분히 답하지 못하면 스키마와 추출 결과를 추가로 보강하세요
- 사용자가 이전 결과에 대해 틀렸다고 표시한 항목과 피드백을 받으면, 그 이유를 해결하도록 스키마와 추출 전략을 조정하세요

## 최종 응답 형식
- 사람이 읽는 한국어 요약을 먼저 작성하세요
- 마지막에는 아래 형식의 JSON 코드 블록을 반드시 포함하세요

```ontology_build_report
{
  "intent": "사용자 구축 의도",
  "summary": "이번 구축/개선으로 무엇을 맞췄는지 한 줄 요약",
  "next_action": "사용자에게 요청할 다음 검토 액션 또는 남은 한계",
  "golden_questions": [
    {
      "question": "Golden Question 원문",
      "answer": "현재 온톨로지가 제공하도록 만든 답변 또는 답변 초안",
      "status": "answerable | partially_answerable | not_yet_answerable",
      "confidence": "high | medium | low"
    }
  ]
}
```
"""

ONTOLOGY_ANSWER_SYSTEM_PROMPT = """\
한국어로 응답하세요. 당신은 Ontology Studio 질의 응답 에이전트입니다.

## 역할
이미 구축된 온톨로지와 그래프를 조회하여, 사용자의 구체적인 질문에 정확하게 답변합니다.

## 핵심 원칙
- 이 모드는 조회 전용입니다. 스키마, 엔티티, 관계를 생성/수정/삭제하지 마세요.
- 문서를 다시 파싱하거나 파일 시스템을 탐색하려고 하지 마세요.
- 답변 전에 필요한 경우 schema_get, entity_search, neo4j_cypher_readonly, hybrid_search_chunks 도구로 근거를 확인하세요.
- 온톨로지에 없는 내용은 추측하지 말고, 정보가 부족하다고 명확히 말하세요.
- 사용자가 그래프를 바꾸거나 새 온톨로지를 만들고 싶다면 온톨로지 구축 모드로 전환하라고 안내하세요.

## 도구 사용 가이드
- schema_get(): 현재 온톨로지 스키마와 관계 정의를 확인
- entity_search(class_name, search_criteria): 특정 클래스 엔티티를 조건으로 조회
- neo4j_cypher_readonly(query, params): 읽기 전용 Cypher 조회. MATCH/OPTIONAL MATCH/WHERE/WITH/RETURN 중심으로 사용
- hybrid_search_chunks(query, top_k, target_node_ids, document_ids, chunk_refs, source_pages): OCR 청크 기반 하이브리드 검색

## 답변 방식
- 먼저 질문 의도를 짧게 정리한 뒤 필요한 조회를 수행하세요.
- 멀티홉 질문이면 먼저 그래프를 좁히고, 필요한 경우 관련 node id를 target_node_ids로 넘겨 hybrid_search_chunks를 호출하세요.
- 조회 결과를 바탕으로 간결하고 검증 가능한 답변을 작성하세요.
- 필요한 경우 어떤 엔티티/관계/스키마 근거를 사용했는지 함께 설명하세요.
"""

_SENTINEL = object()
_NEO4J_TOOL_NAMES = {
    "schema_create_class",
    "schema_create_relationship_type",
    "entity_create",
    "entity_search",
    "relationship_create",
    "neo4j_cypher",
}

_BUILD_MODE_TOOLS = [
    neo4j_cypher,
    schema_create_class,
    schema_create_relationship_type,
    schema_get,
    entity_create,
    entity_search,
    relationship_create,
    hybrid_search_chunks,
]

_ANSWER_MODE_TOOLS = [
    schema_get,
    entity_search,
    neo4j_cypher_readonly,
    hybrid_search_chunks,
]

_BUILD_REPORT_PATTERN = re.compile(
    r"```ontology_build_report\s*(\{.*?\})\s*```",
    re.DOTALL,
)
_TEXT_CONTENT_BLOCK_TYPES = {"text", "output_text"}
_document_indexing_service = DocumentIndexingService()


def _parse_json_if_possible(value: str) -> Any | None:
    """Best-effort JSON parsing for tool outputs."""

    normalized = value.strip()
    if not normalized or normalized[0] not in "{[":
        return None
    try:
        return json.loads(normalized)
    except json.JSONDecodeError:
        return None


def _detect_tool_failure(content: str) -> str | None:
    """Infer likely tool failures from raw tool output."""

    parsed = _parse_json_if_possible(content)
    if isinstance(parsed, dict):
        error_value = parsed.get("error")
        if error_value:
            return _clean_text(error_value)
        exit_code = parsed.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            return f"exit_code={exit_code}"
        status_value = _clean_text(parsed.get("status")).lower()
        if status_value in {"error", "failed", "failure", "timeout"}:
            return _clean_text(parsed.get("message")) or status_value

    exit_code_match = re.search(r"\bexit[_ ]code\b[^0-9-]*(-?\d+)", content, re.IGNORECASE)
    if exit_code_match:
        exit_code = int(exit_code_match.group(1))
        if exit_code != 0:
            return f"exit_code={exit_code}"

    lowered = content.lower()
    if "traceback" in lowered:
        return "traceback_detected"
    if "timed out" in lowered:
        return "timeout_detected"
    return None


def _summarize_tool_runs(tool_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate tool calls by tool name for end-of-run summaries."""

    aggregated: dict[str, dict[str, Any]] = {}
    for tool_run in tool_runs:
        tool_name = _clean_text(tool_run.get("name")) or "unknown"
        current = aggregated.setdefault(
            tool_name,
            {
                "name": tool_name,
                "count": 0,
                "failure_count": 0,
                "total_duration_ms": 0,
                "max_duration_ms": 0,
            },
        )
        current["count"] += 1
        if tool_run.get("failed"):
            current["failure_count"] += 1
        duration_ms = tool_run.get("duration_ms")
        if isinstance(duration_ms, int):
            current["total_duration_ms"] += duration_ms
            current["max_duration_ms"] = max(current["max_duration_ms"], duration_ms)

    summaries = []
    for item in aggregated.values():
        count = item["count"] or 1
        item["avg_duration_ms"] = int(item["total_duration_ms"] / count)
        summaries.append(item)
    summaries.sort(key=lambda item: item["name"])
    return summaries


def _clean_text(value: Any) -> str:
    """Normalize arbitrary values into stripped strings."""

    if value is None:
        return ""
    return str(value).strip()


def _parse_build_context(raw_context: str | None) -> dict[str, Any]:
    """Parse optional build context provided by the frontend."""

    default_context = {
        "intent": "",
        "golden_questions": [],
        "review_feedback": [],
    }
    if not raw_context:
        return default_context

    try:
        payload = json.loads(raw_context)
    except json.JSONDecodeError:
        return default_context

    golden_questions = [
        _clean_text(item)
        for item in payload.get("golden_questions", [])
        if _clean_text(item)
    ]
    review_feedback = []
    for item in payload.get("review_feedback", []):
        if not isinstance(item, dict):
            continue
        question = _clean_text(item.get("question"))
        if not question:
            continue
        review_feedback.append(
            {
                "question": question,
                "answer": _clean_text(item.get("answer")),
                "status": _clean_text(item.get("status")) or "answerable",
                "verdict": _clean_text(item.get("verdict")),
                "feedback": _clean_text(item.get("feedback")),
            }
        )

    return {
        "intent": _clean_text(payload.get("intent")),
        "golden_questions": golden_questions,
        "review_feedback": review_feedback,
    }


def _compose_build_prompt(prompt: str, build_context: dict[str, Any]) -> str:
    """Inject build intent, golden questions, and review feedback into the prompt."""

    sections = [f"[사용자 작업 요청]\n{prompt.strip()}"]

    if build_context["intent"]:
        sections.append(f"[구축 의도]\n{build_context['intent']}")

    if build_context["golden_questions"]:
        golden_question_lines = "\n".join(
            f"{index}. {question}"
            for index, question in enumerate(build_context["golden_questions"], start=1)
        )
        sections.append(
            "[Golden Question]\n"
            "아래 질문들에 답할 수 있는 온톨로지 스키마와 그래프를 구축하세요.\n"
            f"{golden_question_lines}"
        )

    if build_context["review_feedback"]:
        feedback_lines = []
        for index, item in enumerate(build_context["review_feedback"], start=1):
            feedback_lines.append(
                "\n".join(
                    [
                        f"{index}. 질문: {item['question']}",
                        f"   이전 판정: {item['verdict'] or '미지정'}",
                        f"   이전 상태: {item['status']}",
                        f"   기존 답변: {item['answer'] or '없음'}",
                        f"   사용자 피드백: {item['feedback'] or '없음'}",
                    ]
                )
            )
        sections.append(
            "[이전 결과 검토 피드백]\n"
            "아래 항목에서 사용자가 문제를 지적했습니다. 이를 해결하도록 온톨로지를 개선하세요.\n"
            + "\n".join(feedback_lines)
        )

    sections.append(
        "[완료 기준]\n"
        "- 현재 온톨로지 스키마와 그래프만으로 Golden Question에 답할 수 있어야 합니다.\n"
        "- 최종 응답 마지막에는 ontology_build_report JSON 코드 블록을 반드시 포함하세요.\n"
        "- report의 question은 사용자가 입력한 Golden Question과 동일한 문장을 유지하세요."
    )
    return "\n\n".join(section for section in sections if section.strip())


def _normalize_build_report_question(
    item: dict[str, Any],
    fallback_question: str,
) -> dict[str, str]:
    """Normalize a single build report question entry."""

    return {
        "question": _clean_text(item.get("question")) or fallback_question,
        "answer": _clean_text(item.get("answer")),
        "status": _clean_text(item.get("status")) or "answerable",
        "confidence": _clean_text(item.get("confidence")) or "medium",
    }


def _extract_build_report(
    text: str,
    build_context: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    """Split the visible assistant text from the structured build report."""

    if not text:
        return "", None

    match = _BUILD_REPORT_PATTERN.search(text)
    if not match:
        return text.strip(), None

    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        cleaned_text = _BUILD_REPORT_PATTERN.sub("", text).strip()
        return cleaned_text, None

    raw_items = payload.get("golden_questions", [])
    normalized_items = []
    fallback_questions = build_context.get("golden_questions", [])
    if isinstance(raw_items, list):
        for index, raw_item in enumerate(raw_items):
            if not isinstance(raw_item, dict):
                continue
            fallback_question = (
                fallback_questions[index] if index < len(fallback_questions) else ""
            )
            normalized_items.append(
                _normalize_build_report_question(raw_item, fallback_question)
            )

    if not normalized_items and fallback_questions:
        normalized_items = [
            {
                "question": question,
                "answer": "",
                "status": "not_yet_answerable",
                "confidence": "low",
            }
            for question in fallback_questions
        ]

    cleaned_text = _BUILD_REPORT_PATTERN.sub("", text).strip()
    return cleaned_text, {
        "intent": _clean_text(payload.get("intent")) or build_context.get("intent", ""),
        "summary": _clean_text(payload.get("summary")),
        "next_action": _clean_text(payload.get("next_action")),
        "golden_questions": normalized_items,
    }


def _extract_text_from_content(content: Any) -> str:
    """Extract assistant-visible text from LangChain/OpenAI content blocks."""

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_extract_text_from_content(item) for item in content)
    if isinstance(content, dict):
        block_type = _clean_text(content.get("type"))
        if block_type in _TEXT_CONTENT_BLOCK_TYPES:
            return _extract_text_from_content(content.get("text"))
        if "content" in content:
            nested = _extract_text_from_content(content.get("content"))
            if nested:
                return nested
        if "output" in content:
            nested = _extract_text_from_content(content.get("output"))
            if nested:
                return nested
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("text"), dict):
            return _extract_text_from_content(content["text"])
    return ""


def _merge_stream_text(current_text: str, incoming_text: str) -> tuple[str, str]:
    """Append only the new delta when updates repeat the full assistant text."""

    if not incoming_text:
        return current_text, ""
    if not current_text:
        return incoming_text, incoming_text
    if incoming_text == current_text or current_text.endswith(incoming_text):
        return current_text, ""
    if incoming_text.startswith(current_text):
        delta = incoming_text[len(current_text) :]
        return incoming_text, delta

    max_overlap = min(len(current_text), len(incoming_text))
    for overlap in range(max_overlap, 0, -1):
        if current_text.endswith(incoming_text[:overlap]):
            delta = incoming_text[overlap:]
            return current_text + delta, delta

    return current_text + incoming_text, incoming_text


def _resolve_agent_model_profile(mode: AgentMode):
    """Resolve the configured model profile for a given agent mode."""

    settings = get_settings()
    if mode == "build":
        return resolve_model_profile(
            purpose="major",
            model_name=settings.major_model,
            reasoning_effort=settings.major_model_reasoning_effort,
            openai_base_url=settings.openai_base_url,
            openai_api_key=settings.openai_api_key,
        )
    return resolve_model_profile(
        purpose="minor",
        model_name=settings.minor_model,
        reasoning_effort=settings.minor_model_reasoning_effort,
        openai_base_url=settings.openai_base_url,
        openai_api_key=settings.openai_api_key,
    )


def _init_model(mode: AgentMode):
    """Create the configured model instance or provider string."""

    profile = _resolve_agent_model_profile(mode)
    if profile.is_openai:
        from langchain_openai import ChatOpenAI

        kwargs = {
            "model": profile.model_name,
            "api_key": profile.api_key,
            "temperature": 0,
            "reasoning_effort": profile.reasoning_effort,
            "use_responses_api": should_use_openai_responses_api(profile),
            "streaming": False,
        }
        if profile.base_url:
            kwargs["base_url"] = profile.base_url
        return ChatOpenAI(**kwargs)
    return profile.raw_name


def _create_build_agent():
    """Create the ontology construction agent with sandbox access."""

    from deepagents import create_deep_agent

    settings = get_settings()
    backend = DockerSandboxBackend(
        container_name=settings.container_name,
        workdir=settings.sandbox_workdir,
    )
    return create_deep_agent(
        model=_init_model("build"),
        backend=backend,
        tools=_BUILD_MODE_TOOLS,
        system_prompt=ONTOLOGY_BUILD_SYSTEM_PROMPT,
    )


def _create_answer_agent():
    """Create the question-answering agent with read-only ontology tools."""

    from deepagents import create_deep_agent

    return create_deep_agent(
        model=_init_model("answer"),
        tools=_ANSWER_MODE_TOOLS,
        system_prompt=ONTOLOGY_ANSWER_SYSTEM_PROMPT,
    )


def get_agent(mode: AgentMode = "build"):
    """Return the singleton deep agent instance for the requested mode."""

    if mode not in _agents:
        if mode == "build":
            _agents[mode] = _create_build_agent()
        else:
            _agents[mode] = _create_answer_agent()
    return _agents[mode]


def _session_key(session_id: str, mode: AgentMode) -> str:
    """Return the in-memory session key for the mode-specific conversation."""

    return f"{session_id}:{mode}"


def warm_up_agent() -> None:
    """Initialize agent dependencies eagerly on app startup."""

    get_agent("build")
    get_agent("answer")
    ensure_workspace_dirs()


def clear_session(session_id: str) -> None:
    """Drop in-memory history for a single session."""

    for mode in ("build", "answer"):
        _sessions.pop(_session_key(session_id, mode), None)


def _format_sse(event: str, data: dict) -> str:
    encoded = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {encoded}\n\n"


def _build_preprocessing_todos(current_stage: str) -> list[dict[str, str]]:
    stages = [
        ("ocr", "PDF OCR 처리"),
        ("embedding", "청크 임베딩 생성"),
        ("neo4j_upsert", "Neo4j 문서 그래프 업로드"),
        ("agent_build", "온톨로지 구축 에이전트 실행"),
    ]
    completed = True
    items = []
    for stage_id, label in stages:
        if stage_id == current_stage:
            items.append({"id": stage_id, "content": label, "status": "in_progress"})
            completed = False
            continue
        if completed:
            items.append({"id": stage_id, "content": label, "status": "completed"})
        else:
            items.append({"id": stage_id, "content": label, "status": "pending"})
    return items


def _resolve_preprocess_stage(detail: dict[str, Any] | None) -> str:
    if not detail:
        return "ocr"
    stage = _clean_text(detail.get("stage"))
    if stage in {"ocr", "embedding", "neo4j_upsert"}:
        return stage
    return "ocr"


def _emit_tool_side_effects(
    tool_name: str,
    content: str,
    generated_files: list[str],
    seen_refs: set[str],
) -> list[str]:
    events = []

    if tool_name == "write_todos":
        todos = []
        try:
            import ast

            bracket_start = content.find("[")
            if bracket_start >= 0:
                todos = ast.literal_eval(content[bracket_start:])
        except Exception:
            todos = []
        if todos:
            events.append(_format_sse("todos", {"items": todos}))

    if tool_name in ("ls", "read_file", "glob"):
        file_matches = re.findall(r"/workspace/uploads/[^'\"\]\n]+", content)
        for match in file_matches:
            cleaned = match.rstrip(" ,")
            filename = cleaned.split("/workspace/uploads/")[-1]
            if filename and filename not in seen_refs:
                seen_refs.add(filename)
                events.append(_format_sse("ref_file", {"name": filename, "path": cleaned}))

    if "/workspace/output/" in content:
        found = re.findall(r"/workspace/output/[\w\-\.]+", content)
        for file_path in found:
            filename = Path(file_path).name
            if filename not in generated_files:
                generated_files.append(filename)

    if tool_name in _NEO4J_TOOL_NAMES:
        events.append(_format_sse("neo4j_update", {"tool": tool_name}))

    return events


def _run_agent_in_thread(
    prompt: str,
    session_id: str,
    mode: AgentMode,
    run_id: str,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Run the synchronous agent stream on a background thread."""

    try:
        agent = get_agent(mode)
        history_key = _session_key(session_id, mode)
        if history_key not in _sessions:
            _sessions[history_key] = []
        history = _sessions[history_key]
        history_length_before = len(history)
        history.append(HumanMessage(content=prompt))

        model_profile = _resolve_agent_model_profile(mode)
        stream_modes = ["messages", "updates"] if not model_profile.uses_custom_base_url else ["updates"]
        log_agent_event(
            "INFO",
            "agent_stream_thread_started",
            run_id=run_id,
            session_id=session_id,
            mode=mode,
            history_length_before=history_length_before,
            history_length_after=len(history),
            stream_modes=stream_modes,
        )
        for event_mode, payload in agent.stream(
            {"messages": list(history)},
            stream_mode=stream_modes,
        ):
            loop.call_soon_threadsafe(queue.put_nowait, (event_mode, payload))
    except Exception as exc:  # pragma: no cover - passthrough error handling
        log_agent_event(
            "ERROR",
            "agent_stream_thread_error",
            run_id=run_id,
            session_id=session_id,
            mode=mode,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        loop.call_soon_threadsafe(queue.put_nowait, ("__error__", str(exc)))
    finally:
        log_agent_event(
            "INFO",
            "agent_stream_thread_finished",
            run_id=run_id,
            session_id=session_id,
            mode=mode,
        )
        loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)


async def generate_sse(
    prompt: str,
    session_id: str,
    mode: AgentMode = "build",
    raw_build_context: str | None = None,
):
    """Yield streaming agent events as SSE messages."""

    build_context = _parse_build_context(raw_build_context if mode == "build" else "")
    effective_prompt = (
        _compose_build_prompt(prompt, build_context) if mode == "build" else prompt
    )
    settings = get_settings()
    model_profile = _resolve_agent_model_profile(mode)
    stream_modes = ["messages", "updates"] if not model_profile.uses_custom_base_url else ["updates"]
    run_id = uuid.uuid4().hex
    run_started_at = time.perf_counter()
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    log_agent_event(
        "INFO",
        "agent_run_started",
        run_id=run_id,
        session_id=session_id,
        mode=mode,
        user_prompt=prompt,
        raw_build_context=raw_build_context if mode == "build" else None,
        parsed_build_context=build_context if mode == "build" else None,
        effective_prompt=effective_prompt,
        model_name=model_profile.raw_name,
        using_custom_base_url=model_profile.uses_custom_base_url,
        sandbox_container_name=settings.container_name,
        sandbox_workdir=settings.sandbox_workdir,
        stream_modes=stream_modes,
    )
    generated_files: list[str] = []
    seen_skills: set[str] = set()
    seen_refs: set[str] = set()
    pending_tool_names: dict[str, str] = {}
    emitted_tool_starts: set[str] = set()
    emitted_tool_results: set[str] = set()
    tool_started_at: dict[str, float] = {}
    tool_runs: list[dict[str, Any]] = []
    tool_runs_by_id: dict[str, dict[str, Any]] = {}
    node_update_counts: dict[str, int] = {}
    stream_event_counts = {"messages": 0, "updates": 0}
    last_logged_node = ""
    run_failed = False
    error_message = ""
    assistant_text = ""
    mode_label = "온톨로지 구축" if mode == "build" else "질문 응답"
    if mode == "build":
        status_message = "온톨로지 구축 전처리 시작..."
        if build_context["golden_questions"]:
            status_message = (
                "온톨로지 구축 전처리 시작... "
                f"{len(build_context['golden_questions'])}개의 Golden Question 기준으로 준비합니다."
            )
        yield _format_sse("status", {"message": status_message})
        current_preprocess_stage = "ocr"
        yield _format_sse("todos", {"items": _build_preprocessing_todos(current_preprocess_stage)})

        preprocess_queue: asyncio.Queue = asyncio.Queue()

        async def _on_preprocess_progress(
            progress: int,
            message: str,
            detail: dict[str, Any] | None,
        ) -> None:
            await preprocess_queue.put(
                {
                    "progress": progress,
                    "message": message,
                    "detail": detail or {},
                }
            )

        preprocess_task = asyncio.create_task(
            _document_indexing_service.ingest_uploaded_pdfs(
                on_progress=_on_preprocess_progress,
            )
        )

        while True:
            if preprocess_task.done() and preprocess_queue.empty():
                break
            try:
                event = await asyncio.wait_for(preprocess_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            current_preprocess_stage = _resolve_preprocess_stage(event["detail"])
            yield _format_sse("status", {"message": event["message"]})
            yield _format_sse(
                "preprocess_progress",
                {
                    "progress": event["progress"],
                    "message": event["message"],
                    **event["detail"],
                },
            )
            yield _format_sse("todos", {"items": _build_preprocessing_todos(current_preprocess_stage)})

        try:
            ingestion_summary = await preprocess_task
        except Exception as exc:
            run_failed = True
            error_message = str(exc)
            log_agent_event(
                "ERROR",
                "document_preprocessing_failed",
                run_id=run_id,
                session_id=session_id,
                mode=mode,
                error=error_message,
            )
            yield _format_sse("error_event", {"message": str(exc)})
            return

        yield _format_sse(
            "preprocess_complete",
            {"summary": ingestion_summary},
        )
        yield _format_sse("todos", {"items": _build_preprocessing_todos("agent_build")})
        yield _format_sse("status", {"message": "전처리 완료. 온톨로지 구축 에이전트를 시작합니다."})
    else:
        yield _format_sse("status", {"message": f"{mode_label} 모드 실행 시작..."})

    thread = threading.Thread(
        target=_run_agent_in_thread,
        args=(effective_prompt, session_id, mode, run_id, queue, loop),
        daemon=True,
    )
    thread.start()

    def _register_tool_start(
        tool_call_id: str,
        tool_name: str,
        node: str,
        tool_call: Any,
    ) -> bool:
        if tool_call_id and tool_call_id in emitted_tool_starts:
            return False
        if tool_call_id:
            emitted_tool_starts.add(tool_call_id)
            tool_started_at[tool_call_id] = time.perf_counter()

        tool_run = {
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "node": node,
            "started_at_ms": int(time.time() * 1000),
            "duration_ms": None,
            "failed": False,
        }
        tool_runs.append(tool_run)
        if tool_call_id:
            tool_runs_by_id[tool_call_id] = tool_run

        log_agent_event(
            "INFO",
            "tool_start",
            run_id=run_id,
            session_id=session_id,
            mode=mode,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            node=node,
            tool_call=tool_call,
        )
        return True

    def _register_tool_result(
        tool_call_id: str,
        tool_name: str,
        content: str,
        node: str,
    ) -> bool:
        if tool_call_id and tool_call_id in emitted_tool_results:
            return False
        if tool_call_id:
            emitted_tool_results.add(tool_call_id)

        duration_ms = None
        if tool_call_id and tool_call_id in tool_started_at:
            duration_ms = int((time.perf_counter() - tool_started_at.pop(tool_call_id)) * 1000)

        failure_reason = _detect_tool_failure(content)
        tool_run = tool_runs_by_id.get(tool_call_id)
        if tool_run is None:
            tool_run = {
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "node": node,
                "started_at_ms": None,
                "duration_ms": None,
                "failed": False,
            }
            tool_runs.append(tool_run)
            if tool_call_id:
                tool_runs_by_id[tool_call_id] = tool_run

        tool_run["name"] = tool_name or tool_run.get("name", "")
        tool_run["node"] = node or tool_run.get("node", "")
        tool_run["duration_ms"] = duration_ms
        tool_run["completed_at_ms"] = int(time.time() * 1000)
        tool_run["failed"] = failure_reason is not None
        if failure_reason:
            tool_run["failure_reason"] = failure_reason

        log_agent_event(
            "ERROR" if failure_reason else "INFO",
            "tool_result",
            run_id=run_id,
            session_id=session_id,
            mode=mode,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            node=node,
            duration_ms=duration_ms,
            failed=failure_reason is not None,
            failure_reason=failure_reason,
            content=content,
        )
        return True

    def _record_node_update(node_name: str) -> None:
        nonlocal last_logged_node
        if not node_name:
            return
        node_update_counts[node_name] = node_update_counts.get(node_name, 0) + 1
        if node_name != last_logged_node:
            last_logged_node = node_name
            log_agent_event(
                "INFO",
                "node_update",
                run_id=run_id,
                session_id=session_id,
                mode=mode,
                node=node_name,
                update_count=node_update_counts[node_name],
            )

    while True:
        item = await queue.get()
        if item is _SENTINEL:
            break

        event_mode, payload = item
        if event_mode == "__error__":
            run_failed = True
            error_message = str(payload)
            log_agent_event(
                "ERROR",
                "agent_run_error_event",
                run_id=run_id,
                session_id=session_id,
                mode=mode,
                error=error_message,
                elapsed_ms=int((time.perf_counter() - run_started_at) * 1000),
            )
            yield _format_sse("error_event", {"message": str(payload)})
            break

        if event_mode == "messages":
            stream_event_counts["messages"] += 1
            msg_chunk, metadata = payload
            node = metadata.get("langgraph_node", "")
            _record_node_update(node)

            if isinstance(msg_chunk, AIMessageChunk):
                text = _extract_text_from_content(msg_chunk.content)
                assistant_text, delta = _merge_stream_text(assistant_text, text)
                if delta and not delta.lstrip().startswith("<|"):
                    yield _format_sse("token", {"text": delta, "node": node})

                if msg_chunk.tool_call_chunks:
                    for tool_call in msg_chunk.tool_call_chunks:
                        tool_call_id = tool_call.get("id") or str(tool_call.get("index", ""))
                        if tool_call_id and tool_call.get("name"):
                            pending_tool_names[tool_call_id] = tool_call["name"]
                        if tool_call.get("name") and _register_tool_start(
                            tool_call_id,
                            tool_call["name"],
                            node,
                            tool_call,
                        ):
                            yield _format_sse(
                                "tool_start",
                                {
                                    "tool_call_id": tool_call_id,
                                    "name": tool_call["name"],
                                    "node": node,
                                },
                            )

            elif isinstance(msg_chunk, ToolMessage):
                content = msg_chunk.content if isinstance(msg_chunk.content, str) else str(msg_chunk.content)
                tool_name = msg_chunk.name or pending_tool_names.get(msg_chunk.tool_call_id, "")
                for event in _emit_tool_side_effects(
                    tool_name,
                    content,
                    generated_files,
                    seen_refs,
                ):
                    yield event
                if _register_tool_result(msg_chunk.tool_call_id or "", tool_name, content, node):
                    yield _format_sse(
                        "tool_result",
                        {
                            "tool_call_id": msg_chunk.tool_call_id or "",
                            "name": tool_name,
                            "content": content[:3000],
                            "node": node,
                        },
                    )

        elif event_mode == "updates":
            stream_event_counts["updates"] += 1
            for node_name, node_data in payload.items():
                if node_name.startswith("__"):
                    continue
                _record_node_update(node_name)

                if "SkillsMiddleware" in node_name and node_name not in seen_skills:
                    seen_skills.add(node_name)
                    yield _format_sse("skill_loaded", {"name": "xlsx"})

                if isinstance(node_data, dict) and "messages" in node_data:
                    raw_messages = node_data["messages"]
                    if hasattr(raw_messages, "value"):
                        raw_messages = raw_messages.value
                    if not isinstance(raw_messages, list):
                        raw_messages = [raw_messages] if raw_messages else []

                    for message in raw_messages:
                        if isinstance(message, AIMessage):
                            text = _extract_text_from_content(message.content)
                            assistant_text, delta = _merge_stream_text(assistant_text, text)
                            if delta.strip() and not delta.lstrip().startswith("<|"):
                                yield _format_sse(
                                    "token",
                                    {"text": delta, "node": node_name},
                                )
                            if message.tool_calls:
                                for tool_call in message.tool_calls:
                                    tool_name = tool_call.get("name", "")
                                    tool_call_id = tool_call.get("id", "")
                                    pending_tool_names[tool_call_id] = tool_name
                                    if _register_tool_start(
                                        tool_call_id,
                                        tool_name,
                                        node_name,
                                        tool_call,
                                    ):
                                        yield _format_sse(
                                            "tool_start",
                                            {
                                                "tool_call_id": tool_call_id,
                                                "name": tool_name,
                                                "node": node_name,
                                            },
                                        )
                        elif isinstance(message, ToolMessage):
                            content = (
                                message.content
                                if isinstance(message.content, str)
                                else str(message.content)
                            )
                            tool_name = message.name or pending_tool_names.get(message.tool_call_id, "")
                            for event in _emit_tool_side_effects(
                                tool_name,
                                content,
                                generated_files,
                                seen_refs,
                            ):
                                yield event
                            if _register_tool_result(
                                message.tool_call_id or "",
                                tool_name,
                                content,
                                node_name,
                            ):
                                yield _format_sse(
                                    "tool_result",
                                    {
                                        "tool_call_id": message.tool_call_id or "",
                                        "name": tool_name,
                                        "content": content[:3000],
                                        "node": node_name,
                                    },
                                )

                yield _format_sse("node_update", {"node": node_name})

    all_files = list_output_filenames() or generated_files
    final_text = assistant_text.strip()
    build_report = None
    if mode == "build":
        final_text, build_report = _extract_build_report(final_text, build_context)

    total_elapsed_ms = int((time.perf_counter() - run_started_at) * 1000)
    completed_tool_runs = [
        tool_run for tool_run in tool_runs if tool_run.get("completed_at_ms") is not None
    ]
    unfinished_tool_runs = [
        tool_run for tool_run in tool_runs if tool_run.get("completed_at_ms") is None
    ]
    tool_failure_count = sum(1 for tool_run in tool_runs if tool_run.get("failed"))
    total_tool_duration_ms = sum(
        tool_run["duration_ms"]
        for tool_run in tool_runs
        if isinstance(tool_run.get("duration_ms"), int)
    )
    log_agent_event(
        "ERROR" if run_failed else "INFO",
        "agent_run_finished",
        run_id=run_id,
        session_id=session_id,
        mode=mode,
        elapsed_ms=total_elapsed_ms,
        failed=run_failed,
        error=error_message or None,
        final_text=final_text,
        build_report=build_report,
    )
    log_agent_event(
        "ERROR" if run_failed or tool_failure_count else "INFO",
        "agent_run_summary",
        run_id=run_id,
        session_id=session_id,
        mode=mode,
        elapsed_ms=total_elapsed_ms,
        failed=run_failed,
        error=error_message or None,
        user_prompt=prompt,
        effective_prompt=effective_prompt,
        build_context=build_context if mode == "build" else None,
        generated_files=all_files,
        referenced_uploads=sorted(seen_refs),
        loaded_skills=sorted(seen_skills),
        stream_event_counts=stream_event_counts,
        node_update_counts=node_update_counts,
        tool_count=len(tool_runs),
        completed_tool_count=len(completed_tool_runs),
        unfinished_tool_count=len(unfinished_tool_runs),
        tool_failure_count=tool_failure_count,
        total_tool_duration_ms=total_tool_duration_ms,
        tool_runs=tool_runs,
        tool_summary=_summarize_tool_runs(tool_runs),
        final_text=final_text,
        build_report=build_report,
    )
    if mode == "build" and not run_failed:
        yield _format_sse(
            "todos",
            {
                "items": [
                    {"id": "ocr", "content": "PDF OCR 처리", "status": "completed"},
                    {"id": "embedding", "content": "청크 임베딩 생성", "status": "completed"},
                    {"id": "neo4j_upsert", "content": "Neo4j 문서 그래프 업로드", "status": "completed"},
                    {"id": "agent_build", "content": "온톨로지 구축 에이전트 실행", "status": "completed"},
                ]
            },
        )
    yield _format_sse(
        "done",
        {
            "message": "완료!",
            "files": all_files,
            "text": final_text,
            "build_report": build_report,
        },
    )
