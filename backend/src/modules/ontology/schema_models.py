"""Pydantic models for persisted ontology schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class OntologyLabelModel(BaseModel):
    """Frontend-facing layer label metadata."""

    name: str
    description: str | None = None
    icon: str | None = None
    color: str | None = None


class OntologyNodePropertyModel(BaseModel):
    """Property definition for a schema node."""

    name: str
    type: Literal["string", "number", "boolean", "date", "datetime", "json"]
    description: str | None = None
    required: bool | None = None


class OntologyNodeModel(BaseModel):
    """Serializable ontology node used by the reference frontend."""

    id: str
    name: str
    label: str
    layer: str
    description: str | None = None
    properties: list[OntologyNodePropertyModel] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    embeddingTerms: list[str] = Field(default_factory=list)
    unit: str | None = None
    formula: str | None = None
    targetValue: float | int | None = None
    thresholds: dict[str, float | int] = Field(default_factory=dict)
    source_text: str | None = None
    chunk_ref: str | None = None
    source_page: int | None = None
    document_id: str | None = None
    dataSource: str | None = None
    dataSourceSchema: dict[str, Any] | str | None = None
    materializedView: str | None = None
    instanceCount: int = 0
    position: dict[str, float] | None = None


class OntologyRelationshipModel(BaseModel):
    """Serializable ontology relationship used by the reference frontend."""

    id: str
    source: str
    target: str
    type: str
    description: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class OntologySchemaModel(BaseModel):
    """Snapshot of an ontology graph for persistence and frontend loading."""

    id: str | None = None
    name: str
    description: str | None = None
    domain: str | None = None
    nodes: list[OntologyNodeModel] = Field(default_factory=list)
    relationships: list[OntologyRelationshipModel] = Field(default_factory=list)
    labels: list[OntologyLabelModel] = Field(default_factory=list)
    createdAt: str | None = None
    updatedAt: str | None = None
    version: int | None = None
