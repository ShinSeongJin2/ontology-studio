# Ontology Studio

Ontology Studio는 온톨로지 스키마를 설계하고, 문서에서 엔티티와 관계를 추출해 Neo4j에 저장할 수 있도록 돕는 단일 배포형 애플리케이션입니다. 현재 구조는 `AGENTS.md`의 모듈러 모놀리스 원칙에 맞춰 백엔드는 `backend/`, 프론트엔드는 `features`/`shared` 단위로 정리되어 있습니다.

## 프로젝트 구조

```text
backend/
├─ requirements.txt
├─ skills/
└─ src/
   ├─ host/                  # FastAPI 앱 조립 및 실행 진입점
   ├─ modules/
   │  ├─ agent_session/      # 에이전트 생성, 세션, SSE 스트림
   │  ├─ files/              # 업로드/목록/다운로드
   │  └─ ontology/           # Neo4j 스키마/그래프 API 및 도구
   └─ shared/
      ├─ kernel/             # 환경 설정
      └─ sandbox/            # Docker 샌드박스 백엔드

frontend/
└─ src/
   ├─ host/                  # App shell
   ├─ features/
   │  ├─ chat/
   │  ├─ files/
   │  ├─ ontology/
   │  └─ session/
   └─ shared/
      ├─ hooks/
      └─ ui/
```

## 선행 조건

- Python 3.12 이상
- Node.js 20 이상
- Docker
- Neo4j
- OpenAI 호환 모델 API 접근 정보

## 환경 변수

루트에 `.env` 파일을 두면 백엔드가 자동으로 읽습니다.

```env
OPENAI_API_KEY=
OPENAI_MODEL=openai:gpt-5
OPENAI_BASE_URL=

CONTAINER_NAME=deepagents-sandbox
SANDBOX_WORKDIR=/workspace

BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000

NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=12345678
```

## Neo4j 준비

로컬에 Neo4j가 없다면 Docker로 빠르게 띄울 수 있습니다.

```bash
docker run -d \
  --name ontology-neo4j \
  -p 7474:7474 \
  -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/12345678 \
  neo4j:5
```

기본 README 예시는 위 설정을 기준으로 작성되어 있습니다. 다른 계정이나 포트를 쓰면 `.env` 값도 함께 맞춰 주세요.

## 샌드박스 컨테이너 준비

백엔드는 문서 파싱과 에이전트 `execute` 도구 실행을 위해 별도의 Docker 컨테이너를 사용합니다. 기본 컨테이너 이름은 `deepagents-sandbox`입니다.

```bash
docker build -t ontology-studio-sandbox .
docker run -d --name deepagents-sandbox ontology-studio-sandbox
```

이미 다른 이름의 컨테이너를 사용 중이면 `CONTAINER_NAME` 환경 변수를 함께 바꿔 주세요.

## 백엔드 실행

```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -r backend/requirements.txt
python -m backend.src.host
```

대안으로 다음 명령도 사용할 수 있습니다.

```bash
uvicorn backend.src.host.app:app --host 0.0.0.0 --port 8000
```

## 프론트엔드 실행

```bash
cd frontend
npm install
npm run dev
```

기본 개발 서버는 `http://localhost:5173`에서 열립니다. 프론트는 기본적으로 `http://localhost:8000`의 백엔드 API를 사용합니다.

## 서비스 실행 순서

1. Neo4j를 실행합니다.
2. 샌드박스 컨테이너를 실행합니다.
3. 백엔드를 실행합니다.
4. 프론트엔드를 실행합니다.
5. 브라우저에서 `http://localhost:5173`에 접속합니다.

## E2E 테스트

`test_e2e.py`는 프론트와 백엔드가 이미 실행 중이라고 가정합니다. Playwright는 별도로 설치해야 합니다.

```bash
pip install playwright
playwright install chromium
python test_e2e.py
```

파일 업로드 테스트까지 포함하려면 샘플 문서 경로를 지정하세요.

```bash
export ONTOLOGY_STUDIO_SAMPLE_PDF=/absolute/path/to/sample.pdf
python test_e2e.py
```

지원하는 선택 환경 변수:

- `ONTOLOGY_STUDIO_FRONTEND_URL`: 기본값 `http://localhost:5173`
- `ONTOLOGY_STUDIO_API_URL`: 기본값 `http://localhost:8000`
- `ONTOLOGY_STUDIO_SAMPLE_PDF`: 업로드 테스트용 문서 경로
- `ONTOLOGY_STUDIO_SCREENSHOT_PATH`: 스크린샷 저장 경로

## 참고 사항

- 백엔드는 시작 시 샌드박스 컨테이너 내부의 `/workspace/uploads`, `/workspace/output` 디렉터리를 준비합니다.
- 세션 초기화 API는 대화 이력뿐 아니라 샌드박스의 업로드/출력 파일도 함께 비웁니다.
- `backend/skills/`는 샌드박스 이미지에 포함할 추가 스킬 자산을 두는 위치입니다.