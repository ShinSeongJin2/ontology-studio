FROM python:3.12-slim

# uv 설치 (Python 패키지 관리를 uv로 통일)
COPY --from=ghcr.io/astral-sh/uv:0.7.0 /uv /uvx /bin/

# LibreOffice 설치 (xlsx 스킬의 recalc.py에서 사용)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libreoffice-calc && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 샌드박스 Python 패키지 설치
RUN uv pip install --system --no-cache openpyxl pandas pdfplumber python-docx

# 작업 디렉토리 설정
WORKDIR /workspace

# 백엔드 스킬 스크립트 복사
COPY backend/skills/ /workspace/skills/

# 컨테이너가 계속 실행되도록
CMD ["sleep", "infinity"]
