"""Docker 기반 샌드박스 백엔드.

BaseSandbox를 확장하여 Docker 컨테이너 내에서 명령을 실행합니다.
에이전트의 execute 도구가 Docker 컨테이너 안에서 동작하므로,
openpyxl 코드 실행 및 recalc.py 스크립트 호출이 자동으로 이루어집니다.
"""

from __future__ import annotations

import subprocess
import uuid

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox


class DockerSandboxBackend(BaseSandbox):
    """Docker 컨테이너에서 명령을 실행하는 샌드박스 백엔드."""

    def __init__(
        self,
        container_name: str,
        workdir: str = "/workspace",
        timeout: int = 120,
    ) -> None:
        self._container_name = container_name
        self._workdir = workdir
        self._timeout = timeout
        self._id = str(uuid.uuid4())

    @property
    def id(self) -> str:
        return self._id

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        effective_timeout = timeout or self._timeout
        try:
            result = subprocess.run(
                [
                    "docker", "exec",
                    "-w", self._workdir,
                    self._container_name,
                    "bash", "-c", command,
                ],
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
            output = result.stdout + result.stderr
            return ExecuteResponse(
                output=output,
                exit_code=result.returncode,
                truncated=False,
            )
        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"Command timed out after {effective_timeout}s",
                exit_code=124,
                truncated=True,
            )
        except Exception as e:
            return ExecuteResponse(
                output=str(e),
                exit_code=1,
                truncated=False,
            )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        results = []
        for path, content in files:
            try:
                proc = subprocess.run(
                    ["docker", "cp", "-", f"{self._container_name}:{path}"],
                    input=content,
                    capture_output=True,
                    timeout=30,
                )
                if proc.returncode != 0:
                    # Fallback: write via execute
                    import base64
                    b64 = base64.b64encode(content).decode()
                    self.execute(f"echo '{b64}' | base64 -d > {path}")
                results.append(FileUploadResponse(path=path, error=None))
            except Exception as e:
                results.append(FileUploadResponse(path=path, error=str(e)))
        return results

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        results = []
        for path in paths:
            try:
                proc = subprocess.run(
                    ["docker", "cp", f"{self._container_name}:{path}", "/dev/stdout"],
                    capture_output=True,
                    timeout=30,
                )
                if proc.returncode == 0:
                    results.append(FileDownloadResponse(path=path, content=proc.stdout, error=None))
                else:
                    # Fallback: read via execute and base64
                    resp = self.execute(f"base64 {path}")
                    import base64
                    content = base64.b64decode(resp.output.strip())
                    results.append(FileDownloadResponse(path=path, content=content, error=None))
            except Exception as e:
                results.append(FileDownloadResponse(path=path, content=b"", error=str(e)))
        return results
