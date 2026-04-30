"""File operations against the sandbox workspace."""

from __future__ import annotations

import subprocess
import unicodedata
import uuid
import re
from pathlib import Path

from fastapi import UploadFile

from ...shared.kernel.settings import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CACHE_ROOT = _REPO_ROOT / ".cache"
_LOCAL_SESSIONS_ROOT = _CACHE_ROOT / "sessions"
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def get_repo_root() -> Path:
    """Return the repository root directory."""

    return _REPO_ROOT


def get_cache_root() -> Path:
    """Return the local cache root directory."""

    return _CACHE_ROOT


def normalize_session_id(session_id: str | None) -> str:
    """Return a safe session id segment for local and sandbox paths."""

    normalized = (session_id or "default").strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or not _SESSION_ID_PATTERN.fullmatch(normalized)
    ):
        raise ValueError("invalid session_id")
    return normalized


def _safe_filename(filename: str) -> str:
    """Return a safe single path segment for uploaded/generated files."""

    normalized = unicodedata.normalize("NFC", filename or "").strip()
    name = Path(normalized).name
    if not name or name in {".", ".."} or name != normalized:
        raise ValueError("invalid filename")
    return name


def get_local_upload_root(session_id: str | None = None) -> Path:
    """Return the directory used for backend-side upload copies."""

    return _LOCAL_SESSIONS_ROOT / normalize_session_id(session_id) / "uploads"


def get_sandbox_upload_root(session_id: str | None = None) -> str:
    """Return the session-scoped upload directory inside the sandbox."""

    return f"/workspace/sessions/{normalize_session_id(session_id)}/uploads"


def get_sandbox_output_root(session_id: str | None = None) -> str:
    """Return the session-scoped output directory inside the sandbox."""

    return f"/workspace/sessions/{normalize_session_id(session_id)}/output"


def resolve_sandbox_workspace_path(path: str, session_id: str | None = None) -> str:
    """Map legacy workspace paths to the current session-scoped workspace."""

    if path == "/workspace/uploads":
        return get_sandbox_upload_root(session_id)
    if path.startswith("/workspace/uploads/"):
        return path.replace("/workspace/uploads", get_sandbox_upload_root(session_id), 1)
    if path == "/workspace/output":
        return get_sandbox_output_root(session_id)
    if path.startswith("/workspace/output/"):
        return path.replace("/workspace/output", get_sandbox_output_root(session_id), 1)
    return path


def ensure_workspace_dirs(session_id: str | None = None) -> None:
    """Ensure upload and output directories exist in the sandbox."""

    upload_root = get_sandbox_upload_root(session_id)
    output_root = get_sandbox_output_root(session_id)
    local_upload_root = get_local_upload_root(session_id)
    settings = get_settings()
    local_upload_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "docker",
            "exec",
            settings.container_name,
            "mkdir",
            "-p",
            upload_root,
            output_root,
        ],
        capture_output=True,
    )


def _format_file_size(size_bytes: int) -> str:
    """Return a human-readable file size string."""

    size = float(max(size_bytes, 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            rounded = round(size, 1)
            if rounded.is_integer():
                return f"{int(rounded)} {unit}"
            return f"{rounded:.1f} {unit}"
        size /= 1024
    return "0 B"


def _list_local_upload_records(session_id: str | None = None) -> list[dict]:
    """Return uploaded files using the backend-local cache as source of truth."""

    uploads = []
    for path in list_local_upload_files(session_id):
        uploads.append(
            {
                "name": path.name,
                "size": _format_file_size(path.stat().st_size),
            }
        )
    return uploads


def _list_container_output_records(session_id: str | None = None) -> list[dict]:
    """Return generated output files from the sandbox container."""

    settings = get_settings()
    output_root = get_sandbox_output_root(session_id)
    result = subprocess.run(
        [
            "docker",
            "exec",
            settings.container_name,
            "bash",
            "-c",
            f"ls -lh {output_root}/ 2>/dev/null",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    output_files = []
    for line in result.stdout.strip().splitlines():
        if not line or line.startswith("total"):
            continue
        parts = line.split()
        if len(parts) >= 9:
            output_files.append(
                {
                    "name": " ".join(parts[8:]),
                    "size": parts[4],
                }
            )
    return output_files


async def save_uploads(files: list[UploadFile], session_id: str | None = None) -> list[dict]:
    """Copy uploaded files into the sandbox workspace."""

    ensure_workspace_dirs(session_id)
    settings = get_settings()
    upload_root = get_sandbox_upload_root(session_id)
    local_upload_root = get_local_upload_root(session_id)
    uploaded = []
    for uploaded_file in files:
        content = await uploaded_file.read()
        filename = _safe_filename(uploaded_file.filename or "unknown")
        container_path = f"{upload_root}/{filename}"
        local_path = local_upload_root / filename
        local_path.write_bytes(content)
        tmp_name = f"/tmp/_upload_{uuid.uuid4().hex}"
        Path(tmp_name).write_bytes(content)
        tmp_container = f"{upload_root}/_tmp_{uuid.uuid4().hex}"
        result = subprocess.run(
            ["docker", "cp", tmp_name, f"{settings.container_name}:{tmp_container}"],
            capture_output=True,
        )
        Path(tmp_name).unlink(missing_ok=True)
        if result.returncode == 0:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    settings.container_name,
                    "mv",
                    tmp_container,
                    container_path,
                ],
                capture_output=True,
            )
            uploaded.append(
                {
                    "name": filename,
                    "path": container_path,
                    "local_path": str(local_path),
                    "size": len(content),
                }
            )
        else:
            uploaded.append(
                {
                    "name": filename,
                    "local_path": str(local_path),
                    "error": result.stderr.decode() if result.stderr else "upload failed",
                }
            )
    return uploaded


def list_workspace_files(session_id: str | None = None) -> dict:
    """Return upload and output file listings from the sandbox."""

    return {
        "uploads": _list_local_upload_records(session_id),
        "output": _list_container_output_records(session_id),
    }


def list_output_filenames(session_id: str | None = None) -> list[str]:
    """Return the names of files generated in the output workspace."""

    settings = get_settings()
    output_root = get_sandbox_output_root(session_id)
    scan = subprocess.run(
        ["docker", "exec", settings.container_name, "ls", output_root],
        capture_output=True,
        text=True,
    )
    if scan.returncode != 0:
        return []
    return [name for name in scan.stdout.strip().split("\n") if name]


def list_local_upload_files(session_id: str | None = None) -> list[Path]:
    """Return backend-local upload copies for OCR and indexing."""

    local_upload_root = get_local_upload_root(session_id)
    if not local_upload_root.exists():
        return []
    return sorted(
        [path for path in local_upload_root.iterdir() if path.is_file()],
        key=lambda path: path.name.lower(),
    )


def copy_output_file(filename: str, session_id: str | None = None) -> str | None:
    """Copy a generated output file from the sandbox to a temp file."""

    settings = get_settings()
    safe_name = _safe_filename(filename)
    local_path = f"/tmp/_dl_{uuid.uuid4().hex}_{safe_name}"
    container_path = f"{get_sandbox_output_root(session_id)}/{safe_name}"
    result = subprocess.run(
        ["docker", "cp", f"{settings.container_name}:{container_path}", local_path],
        capture_output=True,
    )
    if result.returncode == 0:
        return local_path
    return None


def delete_upload_file(filename: str, session_id: str | None = None) -> bool:
    """Delete a single uploaded file from local cache and sandbox."""

    settings = get_settings()
    norm = _safe_filename(filename)
    local = get_local_upload_root(session_id) / norm
    deleted = False
    if local.exists():
        local.unlink(missing_ok=True)
        deleted = True
    subprocess.run(
        [
            "docker", "exec", settings.container_name,
            "rm", "-f", f"{get_sandbox_upload_root(session_id)}/{norm}",
        ],
        capture_output=True,
    )
    return deleted


def clear_workspace_files(session_id: str | None = None) -> None:
    """Remove uploaded and generated files from one session workspace."""

    settings = get_settings()
    session_id = normalize_session_id(session_id)
    subprocess.run(
        [
            "docker",
            "exec",
            settings.container_name,
            "bash",
            "-c",
            f"rm -rf /workspace/sessions/{session_id}",
        ],
        capture_output=True,
    )
    local_root = _LOCAL_SESSIONS_ROOT / session_id
    if local_root.exists():
        for path in sorted(local_root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
        local_root.rmdir()
