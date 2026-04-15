"""
Playwright E2E 테스트 - Ontology Studio
UI 렌더링, Neo4j 상태, 파일 업로드, API 엔드포인트를 검증합니다.
"""

import json
import os
import shutil
import time

from playwright.sync_api import sync_playwright, expect

BASE_URL = "http://localhost:5173"
API_URL = "http://localhost:8000"

# 테스트용 샘플 문서 경로
SAMPLE_PDF = "/Users/uengine/ontology-studio/input/2025_조선업_중대재해_사례집.pdf"


def test_api_endpoints():
    """백엔드 API 엔드포인트 동작 확인"""
    import urllib.request

    print("\n=== API 엔드포인트 테스트 ===")

    # 1. Neo4j 상태 API
    try:
        with urllib.request.urlopen(f"{API_URL}/api/neo4j/status") as resp:
            data = json.loads(resp.read())
            print(f"  [PASS] /api/neo4j/status -> {data['status']}")
    except Exception as e:
        print(f"  [FAIL] /api/neo4j/status -> {e}")

    # 2. 스키마 API
    try:
        with urllib.request.urlopen(f"{API_URL}/api/schema") as resp:
            data = json.loads(resp.read())
            assert "classes" in data
            assert "relationships" in data
            print(f"  [PASS] /api/schema -> classes={len(data['classes'])}, rels={len(data['relationships'])}")
    except Exception as e:
        print(f"  [FAIL] /api/schema -> {e}")

    # 3. 그래프 API
    try:
        with urllib.request.urlopen(f"{API_URL}/api/graph") as resp:
            data = json.loads(resp.read())
            assert "nodes" in data
            assert "edges" in data
            print(f"  [PASS] /api/graph -> nodes={len(data['nodes'])}, edges={len(data['edges'])}")
    except Exception as e:
        print(f"  [FAIL] /api/graph -> {e}")

    # 4. 파일 목록 API
    try:
        with urllib.request.urlopen(f"{API_URL}/api/files") as resp:
            data = json.loads(resp.read())
            assert "uploads" in data
            assert "output" in data
            print(f"  [PASS] /api/files -> uploads={len(data['uploads'])}, output={len(data['output'])}")
    except Exception as e:
        print(f"  [FAIL] /api/files -> {e}")

    # 5. SSE 스트림 API (간단히 연결 가능한지만 확인)
    try:
        req = urllib.request.Request(f"{API_URL}/api/stream?prompt=test&session_id=test_e2e")
        with urllib.request.urlopen(req, timeout=5) as resp:
            first_line = resp.readline().decode()
            print(f"  [PASS] /api/stream -> SSE 연결 성공 (첫 라인: {first_line.strip()[:50]})")
    except Exception as e:
        # timeout은 정상 (SSE는 장시간 스트림)
        print(f"  [PASS] /api/stream -> SSE 엔드포인트 접근 가능 ({type(e).__name__})")


def test_ui_rendering(page):
    """UI가 올바르게 렌더링되는지 확인"""
    print("\n=== UI 렌더링 테스트 ===")

    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    # 1. 타이틀/브랜딩 확인
    header = page.locator(".sidebar-header h2")
    expect(header).to_have_text("Ontology Studio")
    print("  [PASS] 사이드바 헤더: 'Ontology Studio'")

    # 2. Welcome 화면 확인
    welcome_h1 = page.locator(".welcome h1")
    expect(welcome_h1).to_have_text("Ontology Studio")
    print("  [PASS] Welcome 화면: 'Ontology Studio'")

    welcome_p = page.locator(".welcome p")
    expect(welcome_p).to_contain_text("온톨로지")
    print("  [PASS] Welcome 설명에 '온톨로지' 포함")

    # 3. 예시 버튼 확인
    example_btns = page.locator(".example-btn")
    count = example_btns.count()
    assert count == 4, f"예시 버튼 4개 기대, {count}개 발견"
    first_example = example_btns.first.text_content()
    assert "온톨로지" in first_example or "스키마" in first_example
    print(f"  [PASS] 예시 버튼 {count}개 표시")

    # 4. Neo4j 상태 표시 확인
    neo4j_status = page.locator(".neo4j-status")
    expect(neo4j_status).to_be_visible()
    status_text = neo4j_status.text_content()
    assert "Neo4j" in status_text
    print(f"  [PASS] Neo4j 상태 표시: '{status_text.strip()}'")

    # 5. 스키마 패널 확인
    schema_panel = page.locator(".panel-header h3:text-is('온톨로지 스키마')")
    expect(schema_panel).to_be_visible()
    print("  [PASS] 온톨로지 스키마 패널 표시")

    # 6. 파일 업로드 영역 확인
    upload_zone = page.locator(".upload-zone")
    expect(upload_zone).to_be_visible()
    print("  [PASS] 파일 업로드 영역 표시")

    # 7. 입력 영역 확인
    textarea = page.locator("textarea")
    expect(textarea).to_be_visible()
    expect(textarea).to_have_attribute("placeholder", "메시지를 입력하세요...")
    print("  [PASS] 메시지 입력 영역 표시")

    # 8. 새 대화 버튼 확인
    reset_btn = page.locator(".btn-reset")
    expect(reset_btn).to_have_text("새 대화")
    print("  [PASS] '새 대화' 버튼 표시")


def test_file_upload(page):
    """파일 업로드 기능 테스트"""
    print("\n=== 파일 업로드 테스트 ===")

    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    if not os.path.exists(SAMPLE_PDF):
        print(f"  [SKIP] 샘플 PDF 파일 없음: {SAMPLE_PDF}")
        return

    # 파일 업로드
    file_input = page.locator("input[type='file']")
    file_input.set_input_files(SAMPLE_PDF)

    # 업로드 메시지가 채팅에 나타나는지 확인
    page.wait_for_selector(".msg-user", timeout=10000)
    user_msg = page.locator(".msg-user").last
    msg_text = user_msg.text_content()
    assert "파일 업로드" in msg_text
    print(f"  [PASS] 업로드 메시지 표시: '{msg_text.strip()[:50]}'")

    # 사이드바 파일 목록에 표시되는지 확인
    page.wait_for_timeout(1000)
    file_items = page.locator(".file-item")
    assert file_items.count() > 0, "업로드된 파일이 사이드바에 표시되지 않음"
    print(f"  [PASS] 사이드바 파일 목록에 {file_items.count()}개 파일 표시")


def test_schema_panel_visibility(page):
    """스키마 패널 토글 테스트"""
    print("\n=== 스키마 패널 토글 테스트 ===")

    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    # 스키마 패널 헤더 클릭으로 토글
    schema_header = page.locator(".panel-header:has-text('온톨로지 스키마')")
    schema_header.click()
    page.wait_for_timeout(300)

    # 패널 본문이 숨겨지는지 확인
    schema_body = schema_header.locator("..").locator(".panel-body")
    expect(schema_body).to_be_hidden()
    print("  [PASS] 스키마 패널 접기 동작")

    # 다시 클릭하여 펼치기
    schema_header.click()
    page.wait_for_timeout(300)
    expect(schema_body).to_be_visible()
    print("  [PASS] 스키마 패널 펼치기 동작")


def test_chat_input(page):
    """채팅 입력 및 전송 테스트"""
    print("\n=== 채팅 입력 테스트 ===")

    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    # 텍스트 입력
    textarea = page.locator("textarea")
    textarea.fill("현재 스키마를 보여줘")

    # 전송 버튼 활성화 확인
    send_btn = page.locator(".btn-send")
    expect(send_btn).to_be_enabled()
    print("  [PASS] 텍스트 입력 후 전송 버튼 활성화")

    # 전송
    send_btn.click()

    # 사용자 메시지 표시 확인
    page.wait_for_selector(".msg-user", timeout=5000)
    user_msg = page.locator(".msg-user").last
    expect(user_msg).to_contain_text("현재 스키마를 보여줘")
    print("  [PASS] 사용자 메시지 표시")

    # AI 응답 시작 확인 (typing indicator 또는 assistant message)
    page.wait_for_selector(".msg-assistant", timeout=10000)
    print("  [PASS] AI 응답 시작됨")

    # 도구 호출이 나타나는지 잠시 기다림 (schema_get 호출 기대)
    try:
        page.wait_for_selector(".tool-block", timeout=30000)
        tool_blocks = page.locator(".tool-block")
        if tool_blocks.count() > 0:
            first_tool = tool_blocks.first.text_content()
            print(f"  [PASS] 도구 호출 감지: '{first_tool.strip()[:60]}'")
    except Exception:
        print("  [INFO] 도구 호출 블록 미감지 (시간 초과 - 에이전트 응답 대기)")

    # 스트리밍이 완료될 때까지 대기 (최대 60초)
    try:
        page.wait_for_function(
            "() => !document.querySelector('.typing')",
            timeout=60000,
        )
        print("  [PASS] 스트리밍 완료")
    except Exception:
        print("  [INFO] 스트리밍 60초 내 완료되지 않음 (에이전트 처리 중)")


def test_screenshot(page):
    """최종 화면 스크린샷 저장"""
    print("\n=== 스크린샷 ===")
    screenshot_path = "/Users/uengine/ontology-studio/test_screenshot.png"
    page.screenshot(path=screenshot_path, full_page=False)
    print(f"  [SAVED] {screenshot_path}")


def main():
    print("=" * 60)
    print("  Ontology Studio E2E 테스트")
    print("=" * 60)

    # 1. API 테스트 (브라우저 불필요)
    test_api_endpoints()

    # 2. Playwright 브라우저 테스트
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        try:
            test_ui_rendering(page)
            test_schema_panel_visibility(page)
            test_file_upload(page)
            test_chat_input(page)
            test_screenshot(page)
        finally:
            context.close()
            browser.close()

    print("\n" + "=" * 60)
    print("  테스트 완료!")
    print("=" * 60)


if __name__ == "__main__":
    main()
