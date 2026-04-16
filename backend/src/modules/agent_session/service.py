"""Agent lifecycle and SSE streaming service."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

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
from ...shared.kernel.settings import get_settings
from ...shared.sandbox.docker_backend import DockerSandboxBackend

AgentMode = Literal["build", "answer"]

_agents: dict[AgentMode, object] = {}
_sessions: dict[str, list] = {}

ONTOLOGY_BUILD_SYSTEM_PROMPT = """\
한국어로 응답하세요. 당신은 Ontology Studio 에이전트입니다.

## 역할
사용자가 온톨로지 스키마를 설계하고, 문서에서 엔티티와 관계를 추출하여 Neo4j에 저장하도록 돕습니다.

## 작업 흐름
1. **스키마 설계**: schema_create_class, schema_create_relationship_type 도구로 온톨로지 스키마를 정의
2. **문서 처리**: execute 도구로 업로드된 문서(PDF, DOCX 등)의 텍스트를 추출 (pdfplumber, python-docx 사용)
3. **엔티티 추출**: 문서 텍스트를 분석하여 스키마에 맞는 엔티티를 식별
4. **중복 해결(Disambiguation)**: entity_search로 기존 엔티티를 검색하여 동일 엔티티는 재사용
5. **관계 추출**: 엔티티 간 관계를 식별하고 relationship_create로 저장
6. **저장**: 모든 결과를 Neo4j에 저장

## 도구 사용 가이드
- schema_get(): 현재 온톨로지 스키마 전체 조회
- schema_create_class(name, description, properties): 새 클래스 정의
- schema_create_relationship_type(name, from_class, to_class, description, properties): 관계 유형 정의
- entity_search(class_name, search_criteria): 중복 확인을 위한 엔티티 검색 (반드시 생성 전에 호출!)
- entity_create(class_name, properties, match_keys): 엔티티 인스턴스 생성 (MERGE로 중복 방지)
- relationship_create(from_entity_id, to_entity_id, relationship_type, properties): 엔티티 간 관계 생성
- neo4j_cypher(query, params): 고급 Cypher 쿼리 직접 실행
- execute: Python 코드 실행 (문서 파싱, 데이터 처리에 사용)

## 중요 규칙
- 엔티티 생성 전 항상 entity_search로 기존 엔티티 확인하세요
- 문서 파싱 시 execute 도구로 pdfplumber(PDF), python-docx(DOCX) 사용
- 스키마 클래스명은 영문 PascalCase (예: Person, Company, Accident)
- 관계 유형명은 영문 UPPER_SNAKE_CASE (예: WORKS_AT, OCCURRED_AT)
- entity_create 시 match_keys로 중복 방지 키를 지정하세요
- 파일은 /workspace/uploads/에서 읽고, /workspace/output/에 저장하세요
- 각 도구 호출 전에 한국어로 간단히 설명하세요
- 대량의 엔티티를 추출할 때는 문서를 섹션별로 나누어 처리하세요
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
- 답변 전에 필요한 경우 schema_get, entity_search, neo4j_cypher_readonly 도구로 근거를 확인하세요.
- 온톨로지에 없는 내용은 추측하지 말고, 정보가 부족하다고 명확히 말하세요.
- 사용자가 그래프를 바꾸거나 새 온톨로지를 만들고 싶다면 온톨로지 구축 모드로 전환하라고 안내하세요.

## 도구 사용 가이드
- schema_get(): 현재 온톨로지 스키마와 관계 정의를 확인
- entity_search(class_name, search_criteria): 특정 클래스 엔티티를 조건으로 조회
- neo4j_cypher_readonly(query, params): 읽기 전용 Cypher 조회. MATCH/OPTIONAL MATCH/WHERE/WITH/RETURN 중심으로 사용

## 답변 방식
- 먼저 질문 의도를 짧게 정리한 뒤 필요한 조회를 수행하세요.
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
]

_ANSWER_MODE_TOOLS = [
    schema_get,
    entity_search,
    neo4j_cypher_readonly,
]

_BUILD_REPORT_PATTERN = re.compile(
    r"```ontology_build_report\s*(\{.*?\})\s*```",
    re.DOTALL,
)
_TEXT_CONTENT_BLOCK_TYPES = {"text", "output_text"}


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


def _normalize_openai_model_name(model_name: str) -> str:
    """Convert provider-prefixed OpenAI model identifiers to bare model names."""

    if model_name.startswith("openai:"):
        return model_name.split(":", maxsplit=1)[1]
    return model_name


def _should_use_responses_api(model_name: str, base_url: str) -> bool:
    """Use Responses API for official OpenAI GPT-5 models."""

    normalized_model = _normalize_openai_model_name(model_name)
    return not base_url and normalized_model.startswith("gpt-5")


def _init_model():
    """Create the configured model instance or provider string."""

    settings = get_settings()
    if settings.openai_base_url or settings.openai_model.startswith("openai:"):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=_normalize_openai_model_name(settings.openai_model),
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            temperature=0,
            reasoning_effort=settings.openai_reasoning_effort,
            use_responses_api=_should_use_responses_api(
                settings.openai_model,
                settings.openai_base_url,
            ),
            streaming=False,
        )
    return settings.openai_model


def _create_build_agent():
    """Create the ontology construction agent with sandbox access."""

    from deepagents import create_deep_agent

    settings = get_settings()
    backend = DockerSandboxBackend(
        container_name=settings.container_name,
        workdir=settings.sandbox_workdir,
    )
    return create_deep_agent(
        model=_init_model(),
        backend=backend,
        tools=_BUILD_MODE_TOOLS,
        system_prompt=ONTOLOGY_BUILD_SYSTEM_PROMPT,
    )


def _create_answer_agent():
    """Create the question-answering agent with read-only ontology tools."""

    from deepagents import create_deep_agent

    return create_deep_agent(
        model=_init_model(),
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
        history.append(HumanMessage(content=prompt))

        settings = get_settings()
        stream_modes = ["messages", "updates"] if not settings.openai_base_url else ["updates"]
        for event_mode, payload in agent.stream(
            {"messages": list(history)},
            stream_mode=stream_modes,
        ):
            loop.call_soon_threadsafe(queue.put_nowait, (event_mode, payload))
    except Exception as exc:  # pragma: no cover - passthrough error handling
        loop.call_soon_threadsafe(queue.put_nowait, ("__error__", str(exc)))
    finally:
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
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    thread = threading.Thread(
        target=_run_agent_in_thread,
        args=(effective_prompt, session_id, mode, queue, loop),
        daemon=True,
    )
    thread.start()

    mode_label = "온톨로지 구축" if mode == "build" else "질문 응답"
    status_message = f"{mode_label} 모드 실행 시작..."
    if mode == "build" and build_context["golden_questions"]:
        status_message = (
            f"{mode_label} 모드 실행 시작... "
            f"{len(build_context['golden_questions'])}개의 Golden Question 기준으로 구축합니다."
        )
    yield _format_sse("status", {"message": status_message})

    generated_files: list[str] = []
    seen_skills: set[str] = set()
    seen_refs: set[str] = set()
    pending_tool_names: dict[str, str] = {}
    emitted_tool_starts: set[str] = set()
    emitted_tool_results: set[str] = set()
    assistant_text = ""

    while True:
        item = await queue.get()
        if item is _SENTINEL:
            break

        event_mode, payload = item
        if event_mode == "__error__":
            yield _format_sse("error_event", {"message": str(payload)})
            break

        if event_mode == "messages":
            msg_chunk, metadata = payload
            node = metadata.get("langgraph_node", "")

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
                            if tool_call_id not in emitted_tool_starts:
                                emitted_tool_starts.add(tool_call_id)
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
                result_key = msg_chunk.tool_call_id or ""
                if not result_key or result_key not in emitted_tool_results:
                    if result_key:
                        emitted_tool_results.add(result_key)
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
            for node_name, node_data in payload.items():
                if node_name.startswith("__"):
                    continue

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
                                    if not tool_call_id or tool_call_id not in emitted_tool_starts:
                                        if tool_call_id:
                                            emitted_tool_starts.add(tool_call_id)
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
                            result_key = message.tool_call_id or ""
                            if not result_key or result_key not in emitted_tool_results:
                                if result_key:
                                    emitted_tool_results.add(result_key)
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

    yield _format_sse(
        "done",
        {
            "message": "완료!",
            "files": all_files,
            "text": final_text,
            "build_report": build_report,
        },
    )
