"""Docker-backed sandbox implementation for DeepAgents."""

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
    """Execute sandbox commands inside a named Docker container."""

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
                    "docker",
                    "exec",
                    "-w",
                    self._workdir,
                    self._container_name,
                    "bash",
                    "-c",
                    command,
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
        except Exception as exc:  # pragma: no cover - passthrough error handling
            return ExecuteResponse(
                output=str(exc),
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
                    import base64

                    b64 = base64.b64encode(content).decode()
                    self.execute(f"echo '{b64}' | base64 -d > {path}")
                results.append(FileUploadResponse(path=path, error=None))
            except Exception as exc:  # pragma: no cover - passthrough error handling
                results.append(FileUploadResponse(path=path, error=str(exc)))
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
                    results.append(
                        FileDownloadResponse(path=path, content=proc.stdout, error=None)
                    )
                else:
                    import base64

                    resp = self.execute(f"base64 {path}")
                    content = base64.b64decode(resp.output.strip())
                    results.append(
                        FileDownloadResponse(path=path, content=content, error=None)
                    )
            except Exception as exc:  # pragma: no cover - passthrough error handling
                results.append(
                    FileDownloadResponse(path=path, content=b"", error=str(exc))
                )
        return results
