"""HTTP API for sandbox file operations."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from .service import copy_output_file, delete_upload_file, list_workspace_files, save_uploads

router = APIRouter(tags=["files"])


@router.post("/api/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    session_id: str = Form("default"),
):
    """Upload user files into the sandbox workspace."""

    try:
        uploaded = await save_uploads(files, session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"uploaded": uploaded}


@router.get("/api/files")
async def list_files(session_id: str = Query("default")):
    """List uploaded and generated files."""

    try:
        return list_workspace_files(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/files/{filename:path}")
async def delete_file(filename: str, session_id: str = Query("default")):
    """Delete an uploaded file."""
    try:
        deleted = delete_upload_file(filename, session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if deleted:
        return {"status": "ok", "filename": filename}
    return {"status": "not_found", "filename": filename}


@router.get("/api/download/{filename:path}")
async def download_file(filename: str, session_id: str = Query("default")):
    """Download a generated output file from the sandbox."""

    try:
        local_path = copy_output_file(filename, session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if local_path:
        return FileResponse(
            local_path,
            media_type="application/octet-stream",
            filename=filename.split("/")[-1],
        )
    return {"error": f"파일을 찾을 수 없습니다: {filename}"}
