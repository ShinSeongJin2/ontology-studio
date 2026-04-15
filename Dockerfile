FROM python:3.12-slim

# LibreOffice 설치 (xlsx 스킬의 recalc.py에서 사용)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libreoffice-calc && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Python 패키지 설치
RUN pip install --no-cache-dir openpyxl pandas pdfplumber python-docx

# 작업 디렉토리 설정
WORKDIR /workspace

# 스킬 스크립트 복사
COPY skills/ /workspace/skills/

# 컨테이너가 계속 실행되도록
CMD ["sleep", "infinity"]
