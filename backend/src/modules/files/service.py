"""File operations against the sandbox workspace."""

from __future__ import annotations

import subprocess
import unicodedata
import uuid
from pathlib import Path

from fastapi import UploadFile

from ...shared.kernel.settings import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CACHE_ROOT = _REPO_ROOT / ".cache"
_LOCAL_UPLOAD_ROOT = _CACHE_ROOT / "uploads"


def get_repo_root() -> Path:
    """Return the repository root directory."""

    return _REPO_ROOT


def get_cache_root() -> Path:
    """Return the local cache root directory."""

    return _CACHE_ROOT


def get_local_upload_root() -> Path:
    """Return the directory used for backend-side upload copies."""

    return _LOCAL_UPLOAD_ROOT


def ensure_workspace_dirs() -> None:
    """Ensure upload and output directories exist in the sandbox."""

    settings = get_settings()
    _LOCAL_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "docker",
            "exec",
            settings.container_name,
            "mkdir",
            "-p",
            "/workspace/uploads",
            "/workspace/output",
        ],
        capture_output=True,
    )


async def save_uploads(files: list[UploadFile]) -> list[dict]:
    """Copy uploaded files into the sandbox workspace."""

    settings = get_settings()
    _LOCAL_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    uploaded = []
    for uploaded_file in files:
        content = await uploaded_file.read()
        filename = unicodedata.normalize("NFC", uploaded_file.filename or "unknown")
        container_path = f"/workspace/uploads/{filename}"
        local_path = _LOCAL_UPLOAD_ROOT / filename
        local_path.write_bytes(content)
        tmp_name = f"/tmp/_upload_{uuid.uuid4().hex}"
        Path(tmp_name).write_bytes(content)
        tmp_container = f"/workspace/uploads/_tmp_{uuid.uuid4().hex}"
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


def list_workspace_files() -> dict:
    """Return upload and output file listings from the sandbox."""

    settings = get_settings()
    result = subprocess.run(
        [
            "docker",
            "exec",
            settings.container_name,
            "bash",
            "-c",
            "echo '=== uploads ===' && ls -lh /workspace/uploads/ 2>/dev/null && "
            "echo '=== output ===' && ls -lh /workspace/output/ 2>/dev/null",
        ],
        capture_output=True,
        text=True,
    )
    files = {"uploads": [], "output": []}
    current = None
    for line in result.stdout.strip().split("\n"):
        if "=== uploads ===" in line:
            current = "uploads"
            continue
        if "=== output ===" in line:
            current = "output"
            continue
        if current and line and not line.startswith("total"):
            parts = line.split()
            if len(parts) >= 9:
                files[current].append(
                    {
                        "name": " ".join(parts[8:]),
                        "size": parts[4],
                    }
                )
    return files


def list_output_filenames() -> list[str]:
    """Return the names of files generated in the output workspace."""

    settings = get_settings()
    scan = subprocess.run(
        ["docker", "exec", settings.container_name, "ls", "/workspace/output/"],
        capture_output=True,
        text=True,
    )
    if scan.returncode != 0:
        return []
    return [name for name in scan.stdout.strip().split("\n") if name]


def list_local_upload_files() -> list[Path]:
    """Return backend-local upload copies for OCR and indexing."""

    if not _LOCAL_UPLOAD_ROOT.exists():
        return []
    return sorted(
        [path for path in _LOCAL_UPLOAD_ROOT.iterdir() if path.is_file()],
        key=lambda path: path.name.lower(),
    )


def copy_output_file(filename: str) -> str | None:
    """Copy a generated output file from the sandbox to a temp file."""

    settings = get_settings()
    local_path = f"/tmp/_dl_{uuid.uuid4().hex}_{Path(filename).name}"
    container_path = f"/workspace/output/{filename}"
    result = subprocess.run(
        ["docker", "cp", f"{settings.container_name}:{container_path}", local_path],
        capture_output=True,
    )
    if result.returncode == 0:
        return local_path
    return None


def clear_workspace_files() -> None:
    """Remove uploaded and generated files from the sandbox workspace."""

    settings = get_settings()
    subprocess.run(
        [
            "docker",
            "exec",
            settings.container_name,
            "bash",
            "-c",
            "rm -rf /workspace/output/* /workspace/uploads/*",
        ],
        capture_output=True,
    )
    if _LOCAL_UPLOAD_ROOT.exists():
        for path in _LOCAL_UPLOAD_ROOT.iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)
