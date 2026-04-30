"""Sandbox tools wrapping DockerSandboxBackend for use with create_agent."""

from __future__ import annotations

from contextlib import contextmanager
import json
import threading

from ..files.service import (
    ensure_workspace_dirs,
    normalize_session_id,
    resolve_sandbox_workspace_path,
)
from ...shared.kernel.settings import get_settings
from ...shared.sandbox.docker_backend import DockerSandboxBackend

_backend = None
_session_context = threading.local()
_OCR_SANITY_CHECK_COMMAND = r"""python - <<'PY'
import importlib.util
import json
import shutil
import subprocess
import sys

required_bins = ["tesseract", "gs", "qpdf"]
optional_bins = ["ocrmypdf"]
required_modules = ["fitz", "pdfplumber", "pypdf", "PIL", "pytesseract"]

missing_bins = [name for name in required_bins if shutil.which(name) is None]
missing_modules = [
    name for name in required_modules if importlib.util.find_spec(name) is None
]

langs_proc = subprocess.run(
    ["tesseract", "--list-langs"],
    capture_output=True,
    text=True,
    check=False,
)
langs_output = (langs_proc.stdout or "") + "\n" + (langs_proc.stderr or "")
installed_langs = {
    line.strip()
    for line in langs_output.splitlines()
    if line.strip() and not line.lower().startswith("list of available languages")
}
missing_langs = sorted({"kor", "eng"} - installed_langs)

payload = {
    "required_bins": required_bins,
    "optional_bins": {name: shutil.which(name) is not None for name in optional_bins},
    "required_modules": required_modules,
    "missing_bins": missing_bins,
    "missing_modules": missing_modules,
    "installed_langs": sorted(installed_langs),
    "missing_langs": missing_langs,
}

if missing_bins or missing_modules or missing_langs:
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(1)

print(json.dumps(payload, ensure_ascii=False))
PY"""
_OCR_BOOTSTRAP_COMMAND = (
    "DEBIAN_FRONTEND=noninteractive apt-get update"
    " && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends"
    " ghostscript ocrmypdf qpdf tesseract-ocr tesseract-ocr-eng tesseract-ocr-kor"
    " && uv pip install --system --no-cache"
    " pdfplumber pypdf pymupdf pillow pytesseract"
    " && apt-get clean"
    " && rm -rf /var/lib/apt/lists/*"
)


def _get_backend() -> DockerSandboxBackend:
    global _backend
    if _backend is None:
        settings = get_settings()
        _backend = DockerSandboxBackend(
            container_name=settings.container_name,
            workdir=settings.sandbox_workdir,
        )
    return _backend


def get_current_session_id() -> str:
    """Return the session id bound to the current agent worker thread."""

    return getattr(_session_context, "session_id", "default")


@contextmanager
def sandbox_session_context(session_id: str):
    """Bind sandbox file tools to one session while an agent run is active."""

    previous = getattr(_session_context, "session_id", None)
    _session_context.session_id = normalize_session_id(session_id)
    try:
        yield
    finally:
        if previous is None:
            try:
                delattr(_session_context, "session_id")
            except AttributeError:
                pass
        else:
            _session_context.session_id = previous


def map_workspace_path(path: str) -> str:
    """Map public /workspace upload/output paths to the current session root."""

    return resolve_sandbox_workspace_path(path, get_current_session_id())


def map_workspace_command(command: str) -> str:
    """Rewrite common workspace paths inside an agent shell command."""

    session_id = get_current_session_id()
    mapped = command.replace(
        "/workspace/uploads",
        resolve_sandbox_workspace_path("/workspace/uploads", session_id),
    )
    return mapped.replace(
        "/workspace/output",
        resolve_sandbox_workspace_path("/workspace/output", session_id),
    )


def ensure_sandbox_ready() -> None:
    """Fail fast when the sandbox container is unavailable during startup."""

    _get_backend().assert_ready()


def ensure_sandbox_ocr_ready() -> None:
    """Verify the default OCR runtime is available inside the sandbox."""

    result = _get_backend().execute(_OCR_SANITY_CHECK_COMMAND, timeout=120)
    output = (result.output or "").strip()
    if result.exit_code != 0:
        raise RuntimeError(
            "Sandbox OCR runtime is not ready: "
            + (output[:1000] if output else f"exit_code={result.exit_code}")
        )


def prepare_sandbox_ocr_runtime() -> str:
    """Install the default OCR runtime inside the sandbox if needed."""

    result = _get_backend().execute(_OCR_BOOTSTRAP_COMMAND, timeout=600)
    output = (result.output or "").strip()
    if result.exit_code != 0:
        raise RuntimeError(
            "Sandbox OCR bootstrap failed: "
            + (output[:2000] if output else f"exit_code={result.exit_code}")
        )
    return output


def execute(command: str) -> str:
    """Execute a shell command (Python or bash) in the sandbox container. Use this for file exploration, running parser scripts, and any code execution."""

    try:
        ensure_workspace_dirs(get_current_session_id())
        result = _get_backend().execute(map_workspace_command(command), timeout=120)
        output = result.output or ""
        if result.exit_code != 0:
            return json.dumps({
                "exit_code": result.exit_code,
                "output": output[:3000],
            }, ensure_ascii=False)
        # Truncate long outputs to save context
        if len(output) > 3000:
            output = output[:3000] + "\n... [truncated]"
        return output
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def sandbox_ls(path: str = "/workspace/uploads") -> str:
    """List files in the sandbox directory."""

    try:
        ensure_workspace_dirs(get_current_session_id())
        result = _get_backend().ls(map_workspace_path(path))
        files = []
        for entry in result.entries:
            files.append({
                "path": entry.get("path", ""),
                "is_dir": entry.get("is_dir", False),
            })
        return json.dumps(files, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def sandbox_read(file_path: str, offset: int = 0, limit: int = 200) -> str:
    """Read file contents from the sandbox."""

    try:
        ensure_workspace_dirs(get_current_session_id())
        result = _get_backend().read(map_workspace_path(file_path), offset=offset, limit=limit)
        content = result.file_data or ""
        if len(content) > 3000:
            content = content[:3000] + "\n... [truncated]"
        return content
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def sandbox_write(file_path: str, content: str) -> str:
    """Write file contents to the sandbox."""

    try:
        ensure_workspace_dirs(get_current_session_id())
        mapped_path = map_workspace_path(file_path)
        _get_backend().write(mapped_path, content)
        return json.dumps({"status": "ok", "path": file_path}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
