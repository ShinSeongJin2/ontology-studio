"""HTTP API for sandbox file operations."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse

from .service import copy_output_file, list_workspace_files, save_uploads

router = APIRouter(tags=["files"])


@router.post("/api/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    session_id: str = Form(""),
):
    """Upload user files into the sandbox workspace."""

    del session_id
    uploaded = await save_uploads(files)
    return {"uploaded": uploaded}


@router.get("/api/files")
async def list_files():
    """List uploaded and generated files."""

    return list_workspace_files()


@router.get("/api/download/{filename:path}")
async def download_file(filename: str):
    """Download a generated output file from the sandbox."""

    local_path = copy_output_file(filename)
    if local_path:
        return FileResponse(
            local_path,
            media_type="application/octet-stream",
            filename=filename.split("/")[-1],
        )
    return {"error": f"파일을 찾을 수 없습니다: {filename}"}
