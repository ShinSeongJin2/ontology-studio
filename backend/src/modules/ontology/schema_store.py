"""File-backed persistence for ontology schema snapshots."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .schema_models import OntologySchemaModel

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCHEMA_STORE_ROOT = _REPO_ROOT / ".cache" / "ontology_schemas"
_ACTIVE_SCHEMA_FILE = _SCHEMA_STORE_ROOT / "active_schema.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_store_dir() -> Path:
    _SCHEMA_STORE_ROOT.mkdir(parents=True, exist_ok=True)
    return _SCHEMA_STORE_ROOT


def _schema_path(schema_id: str) -> Path:
    return _ensure_store_dir() / f"{schema_id}.json"


def _metadata_from_schema(schema: OntologySchemaModel) -> dict:
    return {
        "id": schema.id,
        "name": schema.name,
        "domain": schema.domain,
        "description": schema.description,
        "createdAt": schema.createdAt,
        "updatedAt": schema.updatedAt,
        "version": schema.version,
        "nodeCount": len(schema.nodes),
        "hasSchemaJson": True,
    }


def get_active_schema_id() -> str | None:
    """Return the currently active schema identifier if present."""

    if not _ACTIVE_SCHEMA_FILE.exists():
        return None
    try:
        payload = json.loads(_ACTIVE_SCHEMA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    schema_id = str(payload.get("activeSchemaId") or "").strip()
    return schema_id or None


def list_saved_schemas() -> dict:
    """Return stored schema metadata and the active schema id."""

    _ensure_store_dir()
    items = []
    for path in sorted(_SCHEMA_STORE_ROOT.glob("*.json"), key=lambda candidate: candidate.name):
        if path.name == _ACTIVE_SCHEMA_FILE.name:
            continue
        try:
            schema = OntologySchemaModel.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            schema_id = path.stem
            items.append(
                {
                    "id": schema_id,
                    "name": schema_id,
                    "domain": None,
                    "description": "손상된 스키마 파일",
                    "createdAt": None,
                    "updatedAt": None,
                    "version": None,
                    "nodeCount": 0,
                    "hasSchemaJson": False,
                }
            )
            continue
        items.append(_metadata_from_schema(schema))
    items.sort(key=lambda item: ((item.get("updatedAt") or ""), item.get("name") or ""), reverse=True)
    return {"schemas": items, "activeSchemaId": get_active_schema_id()}


def get_schema_by_id(schema_id: str) -> OntologySchemaModel | None:
    """Load a stored schema by id."""

    path = _schema_path(schema_id)
    if not path.exists():
        return None
    try:
        return OntologySchemaModel.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_active_schema() -> OntologySchemaModel | None:
    """Load the active schema snapshot if present."""

    active_schema_id = get_active_schema_id()
    if not active_schema_id:
        return None
    return get_schema_by_id(active_schema_id)


def save_schema_snapshot(schema: OntologySchemaModel) -> dict:
    """Persist a schema snapshot and mark it active."""

    _ensure_store_dir()
    existing = get_schema_by_id(schema.id or "") if schema.id else None
    now = _now_iso()
    next_version = (existing.version if existing and existing.version is not None else 0) + 1
    schema_id = schema.id or f"schema_{uuid.uuid4().hex[:12]}"
    created_at = existing.createdAt if existing and existing.createdAt else now
    stored_schema = schema.model_copy(
        update={
            "id": schema_id,
            "createdAt": created_at,
            "updatedAt": now,
            "version": next_version,
        }
    )
    _schema_path(schema_id).write_text(
        stored_schema.model_dump_json(indent=2),
        encoding="utf-8",
    )
    set_active_schema(schema_id)
    return {"success": True, "id": schema_id, "version": next_version}


def set_active_schema(schema_id: str) -> dict:
    """Mark a stored schema as active."""

    schema = get_schema_by_id(schema_id)
    if schema is None:
        raise ValueError(f"스키마를 찾을 수 없습니다: {schema_id}")
    _ensure_store_dir()
    _ACTIVE_SCHEMA_FILE.write_text(
        json.dumps({"activeSchemaId": schema_id}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"success": True, "schemaId": schema_id, "name": schema.name}


def rename_schema(schema_id: str, *, name: str | None = None, description: str | None = None) -> dict:
    """Update the name or description of a stored schema."""

    schema = get_schema_by_id(schema_id)
    if schema is None:
        raise ValueError(f"스키마를 찾을 수 없습니다: {schema_id}")
    update_payload = {"updatedAt": _now_iso()}
    if name is not None:
        update_payload["name"] = name
    if description is not None:
        update_payload["description"] = description
    updated_schema = schema.model_copy(update=update_payload)
    _schema_path(schema_id).write_text(
        updated_schema.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return {"success": True, "id": schema_id, "name": updated_schema.name}


def delete_schema(schema_id: str) -> dict:
    """Delete a stored schema snapshot."""

    path = _schema_path(schema_id)
    if not path.exists():
        raise ValueError(f"스키마를 찾을 수 없습니다: {schema_id}")
    path.unlink()
    if get_active_schema_id() == schema_id:
        _ACTIVE_SCHEMA_FILE.unlink(missing_ok=True)
    return {"success": True}


def reset_schema_store() -> dict:
    """Delete all stored schema snapshots and the active pointer."""

    _ensure_store_dir()
    deleted = 0
    for path in _SCHEMA_STORE_ROOT.glob("*.json"):
        if path.exists():
            path.unlink()
            deleted += 1
    _ACTIVE_SCHEMA_FILE.unlink(missing_ok=True)
    return {"success": True, "deleted": deleted}
