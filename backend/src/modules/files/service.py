"""File operations against the sandbox workspace."""

from __future__ import annotations

import subprocess
import unicodedata
import uuid
from pathlib import Path

from fastapi import UploadFile

from ...shared.kernel.settings import get_settings


def ensure_workspace_dirs() -> None:
    """Ensure upload and output directories exist in the sandbox."""

    settings = get_settings()
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
    uploaded = []
    for uploaded_file in files:
        content = await uploaded_file.read()
        filename = unicodedata.normalize("NFC", uploaded_file.filename or "unknown")
        container_path = f"/workspace/uploads/{filename}"
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
                {"name": filename, "path": container_path, "size": len(content)}
            )
        else:
            uploaded.append(
                {
                    "name": filename,
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
