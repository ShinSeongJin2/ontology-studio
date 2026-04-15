"""
FastAPI 백엔드 - Ontology Studio
Deep Agent 기반 온톨로지 스키마 설계 + 문서 엔티티/관계 추출 + Neo4j 저장
StreamingResponse로 직접 SSE를 flush하여 실시간 전달
"""

import asyncio
import json
import os
import re
import subprocess
import threading
import unicodedata
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage

from dotenv import load_dotenv

from docker_sandbox import DockerSandboxBackend
from neo4j_tools import (
    neo4j_cypher,
    schema_create_class,
    schema_create_relationship_type,
    schema_get,
    entity_create,
    entity_search,
    relationship_create,
)

load_dotenv()

CONTAINER_NAME = os.environ.get("CONTAINER_NAME", "deepagents-sandbox")
SANDBOX_WORKDIR = os.environ.get("SANDBOX_WORKDIR", "/workspace")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "openai:gpt-5")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")

_agent = None
_sessions: dict[str, list] = {}


def _init_model():
    """환경변수에 따라 모델 인스턴스 생성"""
    if OPENAI_BASE_URL:
        # 커스텀 OpenAI-호환 엔드포인트 (vLLM, Ollama 등)
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=OPENAI_MODEL,
            base_url=OPENAI_BASE_URL,
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            temperature=0,
            streaming=False,  # vLLM 호환: invoke 모드에서 tool calling 안정화
        )
    # 기본: deepagents의 provider:model 포맷 (예: "openai:gpt-5")
    return OPENAI_MODEL


ONTOLOGY_SYSTEM_PROMPT = """\
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
"""


def get_agent():
    global _agent
    if _agent is None:
        from deepagents import create_deep_agent

        backend = DockerSandboxBackend(
            container_name=CONTAINER_NAME,
            workdir=SANDBOX_WORKDIR,
        )
        _agent = create_deep_agent(
            model=_init_model(),
            backend=backend,
            tools=[
                neo4j_cypher,
                schema_create_class,
                schema_create_relationship_type,
                schema_get,
                entity_create,
                entity_search,
                relationship_create,
            ],
            system_prompt=ONTOLOGY_SYSTEM_PROMPT,
        )
    return _agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_agent()
    subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "mkdir", "-p",
         "/workspace/uploads", "/workspace/output"],
        capture_output=True,
    )
    yield


app = FastAPI(title="Ontology Studio", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Neo4j API ───


@app.get("/api/neo4j/status")
async def neo4j_status():
    try:
        from neo4j_tools import get_driver
        driver = get_driver()
        driver.verify_connectivity()
        return {"status": "connected"}
    except Exception as e:
        return {"status": "disconnected", "error": str(e)}


@app.get("/api/schema")
async def get_schema_endpoint():
    try:
        result = schema_get()
        return json.loads(result)
    except Exception as e:
        return {"classes": [], "relationships": [], "error": str(e)}


@app.get("/api/graph")
async def get_graph(class_name: str = "", limit: int = 100):
    try:
        from neo4j_tools import get_driver
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
                n = record["n"]
                if n and n.element_id not in nodes:
                    nodes[n.element_id] = {
                        "id": n.element_id,
                        "label": dict(n).get("name", ""),
                        "labels": list(n.labels),
                        "properties": dict(n),
                    }
                m = record["m"]
                if m and m.element_id not in nodes:
                    nodes[m.element_id] = {
                        "id": m.element_id,
                        "label": dict(m).get("name", ""),
                        "labels": list(m.labels),
                        "properties": dict(m),
                    }
                r = record["r"]
                if r:
                    edges.append({
                        "from": r.start_node.element_id,
                        "to": r.end_node.element_id,
                        "type": r.type,
                        "properties": dict(r),
                    })

        return {"nodes": list(nodes.values()), "edges": edges}
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}


# ─── SSE 헬퍼 ───

def _format_sse(event: str, data: dict) -> str:
    """SSE 프로토콜 문자열 생성 (즉시 flush용)"""
    encoded = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {encoded}\n\n"


# ─── 파일 업로드 ───


@app.post("/api/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    session_id: str = Form(""),
):
    uploaded = []
    for f in files:
        content = await f.read()
        filename = unicodedata.normalize("NFC", f.filename or "unknown")
        container_path = f"/workspace/uploads/{filename}"
        tmp_name = f"/tmp/_upload_{uuid.uuid4().hex}"
        Path(tmp_name).write_bytes(content)
        tmp_container = f"/workspace/uploads/_tmp_{uuid.uuid4().hex}"
        result = subprocess.run(
            ["docker", "cp", tmp_name, f"{CONTAINER_NAME}:{tmp_container}"],
            capture_output=True,
        )
        Path(tmp_name).unlink(missing_ok=True)
        if result.returncode == 0:
            subprocess.run(
                ["docker", "exec", CONTAINER_NAME, "mv", tmp_container, container_path],
                capture_output=True,
            )
            uploaded.append({"name": filename, "path": container_path, "size": len(content)})
        else:
            uploaded.append({"name": filename, "error": result.stderr.decode()})
    return {"uploaded": uploaded}


# ─── 파일 목록 ───


@app.get("/api/files")
async def list_files():
    result = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "bash", "-c",
         "echo '=== uploads ===' && ls -lh /workspace/uploads/ 2>/dev/null && "
         "echo '=== output ===' && ls -lh /workspace/output/ 2>/dev/null"],
        capture_output=True, text=True,
    )
    files = {"uploads": [], "output": []}
    current = None
    for line in result.stdout.strip().split("\n"):
        if "=== uploads ===" in line:
            current = "uploads"
            continue
        elif "=== output ===" in line:
            current = "output"
            continue
        if current and not line.startswith("total"):
            parts = line.split()
            if len(parts) >= 9:
                files[current].append({
                    "name": " ".join(parts[8:]),
                    "size": parts[4],
                })
    return files


# ─── 파일 다운로드 ───


@app.get("/api/download/{filename:path}")
async def download_file(filename: str):
    local_path = f"/tmp/_dl_{uuid.uuid4().hex}_{Path(filename).name}"
    container_path = f"/workspace/output/{filename}"
    result = subprocess.run(
        ["docker", "cp", f"{CONTAINER_NAME}:{container_path}", local_path],
        capture_output=True,
    )
    if result.returncode == 0:
        return FileResponse(
            local_path,
            media_type="application/octet-stream",
            filename=Path(filename).name,
        )
    return {"error": f"파일을 찾을 수 없습니다: {filename}"}


# ─── SSE 스트리밍 (StreamingResponse 직접 사용) ───

_SENTINEL = object()


def _run_agent_in_thread(prompt: str, session_id: str, aq: asyncio.Queue, loop: asyncio.AbstractEventLoop):
    """별도 스레드에서 동기 agent.stream() → asyncio.Queue 로 전달"""
    try:
        ag = get_agent()
        if session_id not in _sessions:
            _sessions[session_id] = []
        history = _sessions[session_id]
        history.append(HumanMessage(content=prompt))

        # 커스텀 모델(vLLM 등)은 messages 스트리밍에서 tool calling 파싱 실패 가능
        # updates 모드만 사용하여 invoke 기반으로 안정적으로 동작
        stream_modes = ["messages", "updates"] if not OPENAI_BASE_URL else ["updates"]
        for mode, payload in ag.stream(
            {"messages": list(history)},
            stream_mode=stream_modes,
        ):
            loop.call_soon_threadsafe(aq.put_nowait, (mode, payload))
    except Exception as e:
        loop.call_soon_threadsafe(aq.put_nowait, ("__error__", str(e)))
    finally:
        loop.call_soon_threadsafe(aq.put_nowait, _SENTINEL)


async def _generate_sse(prompt: str, session_id: str):
    """asyncio.Queue 에서 이벤트를 꺼내 SSE 문자열로 즉시 yield"""
    aq: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    thread = threading.Thread(
        target=_run_agent_in_thread,
        args=(prompt, session_id, aq, loop),
        daemon=True,
    )
    thread.start()

    # 첫 이벤트 즉시 전송
    yield _format_sse("status", {"message": "에이전트 실행 시작..."})

    generated_files = []
    seen_skills = set()
    seen_refs = set()  # 참조된 파일
    pending_tool_names = {}  # tool_call_id -> name (todo 파싱용)

    while True:
        item = await aq.get()

        if item is _SENTINEL:
            break

        mode, payload = item

        if mode == "__error__":
            yield _format_sse("error", {"message": str(payload)})
            break

        if mode == "messages":
            msg_chunk, metadata = payload
            node = metadata.get("langgraph_node", "")

            if isinstance(msg_chunk, AIMessageChunk):
                # 텍스트 토큰
                if msg_chunk.content:
                    text = ""
                    if isinstance(msg_chunk.content, str):
                        text = msg_chunk.content
                    elif isinstance(msg_chunk.content, list):
                        for block in msg_chunk.content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text += block.get("text", "")
                            elif isinstance(block, str):
                                text += block
                    if text:
                        yield _format_sse("token", {"text": text, "node": node})

                # 도구 호출 시작
                if msg_chunk.tool_call_chunks:
                    for tc in msg_chunk.tool_call_chunks:
                        tc_id = tc.get("id") or tc.get("index", "")
                        if tc_id and tc.get("name"):
                            pending_tool_names[tc_id] = tc["name"]
                            yield _format_sse(
                                "tool_start",
                                {"tool_call_id": str(tc_id), "name": tc["name"], "node": node},
                            )

            # 도구 결과
            elif isinstance(msg_chunk, ToolMessage):
                content = msg_chunk.content
                if not isinstance(content, str):
                    content = str(content)
                tool_name = msg_chunk.name or pending_tool_names.get(msg_chunk.tool_call_id, "")

                # ── todo 이벤트 감지 (write_todos 도구) ──
                if tool_name == "write_todos":
                    todos = []
                    try:
                        # "Updated todo list to [{...}, ...]" 형태에서 리스트 부분 추출
                        import ast
                        bracket_start = content.find("[")
                        if bracket_start >= 0:
                            list_str = content[bracket_start:]
                            todos = ast.literal_eval(list_str)
                    except Exception:
                        pass
                    if todos:
                        yield _format_sse("todos", {"items": todos})

                # ── 참조 파일 감지 (ls, read_file 등) ──
                if tool_name in ("ls", "read_file", "glob"):
                    # 따옴표로 감싼 경로 추출 (공백 포함 파일명 대응)
                    file_matches = re.findall(r"/workspace/uploads/[^'\"\]\n]+", content)
                    for fm in file_matches:
                        fm = fm.rstrip(" ,")
                        fname = fm.split("/workspace/uploads/")[-1]
                        if fname and fname not in seen_refs:
                            seen_refs.add(fname)
                            yield _format_sse("ref_file", {"name": fname, "path": fm})

                # ── 생성 파일 감지 ──
                if "/workspace/output/" in content:
                    found = re.findall(r"/workspace/output/[\w\-\.]+", content)
                    for fp in found:
                        fname = Path(fp).name
                        if fname not in generated_files:
                            generated_files.append(fname)

                yield _format_sse(
                    "tool_result",
                    {
                        "tool_call_id": msg_chunk.tool_call_id or "",
                        "name": tool_name,
                        "content": content[:3000],
                        "node": node,
                    },
                )

                # Neo4j 관련 도구 실행 후 프론트엔드에 리프레시 신호
                if tool_name in (
                    "schema_create_class", "schema_create_relationship_type",
                    "entity_create", "entity_search", "relationship_create",
                    "neo4j_cypher",
                ):
                    yield _format_sse("neo4j_update", {"tool": tool_name})

        elif mode == "updates":
            for node_name, node_data in payload.items():
                if node_name.startswith("__"):
                    continue
                # 스킬 로드 감지
                if "SkillsMiddleware" in node_name and node_name not in seen_skills:
                    seen_skills.add(node_name)
                    yield _format_sse("skill_loaded", {"name": "xlsx"})

                # updates 모드에서 메시지 추출 (커스텀 모델 호환)
                if isinstance(node_data, dict) and "messages" in node_data:
                    from langchain_core.messages import AIMessage
                    msgs_raw = node_data["messages"]
                    # deepagents는 Overwrite 래퍼를 사용할 수 있음
                    if hasattr(msgs_raw, 'value'):
                        msgs_raw = msgs_raw.value
                    if not isinstance(msgs_raw, list):
                        msgs_raw = [msgs_raw] if msgs_raw else []
                    for msg in msgs_raw:
                        # AI 텍스트 응답
                        if isinstance(msg, AIMessage):
                            if msg.content:
                                text = msg.content if isinstance(msg.content, str) else str(msg.content)
                                if text.strip() and not text.startswith("<|"):
                                    yield _format_sse("token", {"text": text, "node": node_name})
                            # 도구 호출
                            if msg.tool_calls:
                                for tc in msg.tool_calls:
                                    tc_name = tc.get("name", "")
                                    pending_tool_names[tc.get("id", "")] = tc_name
                                    yield _format_sse(
                                        "tool_start",
                                        {"tool_call_id": tc.get("id", ""), "name": tc_name, "node": node_name},
                                    )
                        # 도구 결과
                        elif isinstance(msg, ToolMessage):
                            content = msg.content if isinstance(msg.content, str) else str(msg.content)
                            tool_name = msg.name or pending_tool_names.get(msg.tool_call_id, "")

                            if tool_name == "write_todos":
                                todos = []
                                try:
                                    import ast
                                    bi = content.find("[")
                                    if bi >= 0:
                                        todos = ast.literal_eval(content[bi:])
                                except Exception:
                                    pass
                                if todos:
                                    yield _format_sse("todos", {"items": todos})

                            if tool_name in ("ls", "read_file", "glob"):
                                file_matches = re.findall(r"/workspace/uploads/[^'\"\]\n]+", content)
                                for fm in file_matches:
                                    fm = fm.rstrip(" ,")
                                    fname = fm.split("/workspace/uploads/")[-1]
                                    if fname and fname not in seen_refs:
                                        seen_refs.add(fname)
                                        yield _format_sse("ref_file", {"name": fname, "path": fm})

                            if "/workspace/output/" in content:
                                found = re.findall(r"/workspace/output/[\w\-\.]+", content)
                                for fp in found:
                                    fname = Path(fp).name
                                    if fname not in generated_files:
                                        generated_files.append(fname)

                            yield _format_sse(
                                "tool_result",
                                {"tool_call_id": msg.tool_call_id or "", "name": tool_name, "content": content[:3000], "node": node_name},
                            )

                            # Neo4j 관련 도구 실행 후 프론트엔드에 리프레시 신호
                            if tool_name in (
                                "schema_create_class", "schema_create_relationship_type",
                                "entity_create", "entity_search", "relationship_create",
                                "neo4j_cypher",
                            ):
                                yield _format_sse("neo4j_update", {"tool": tool_name})

                yield _format_sse("node_update", {"node": node_name})

    # output 디렉토리 스캔
    scan = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "ls", "/workspace/output/"],
        capture_output=True, text=True,
    )
    all_files = [f for f in scan.stdout.strip().split("\n") if f] if scan.returncode == 0 else generated_files

    yield _format_sse("done", {"message": "완료!", "files": all_files})


@app.get("/api/stream")
async def stream_endpoint(prompt: str = "", session_id: str = "default"):
    if not prompt:
        return {"error": "prompt is required"}
    return StreamingResponse(
        _generate_sse(prompt, session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx 프록시 버퍼링 방지
        },
    )


# ─── 세션 초기화 ───


@app.post("/api/session/reset")
async def reset_session(session_id: str = "default"):
    _sessions.pop(session_id, None)
    subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "bash", "-c",
         "rm -rf /workspace/output/* /workspace/uploads/*"],
        capture_output=True,
    )
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("BACKEND_HOST", "0.0.0.0")
    port = int(os.environ.get("BACKEND_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
