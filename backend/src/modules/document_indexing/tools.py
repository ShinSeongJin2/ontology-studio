"""Agent tools for hybrid retrieval over OCR-indexed chunks."""

from __future__ import annotations

import json

from .service import DocumentIndexingService

_service = DocumentIndexingService()


def hybrid_search_chunks(
    query: str,
    top_k: int = 5,
    target_node_ids: str = "[]",
    document_ids: str = "[]",
    chunk_refs: str = "[]",
    source_pages: str = "[]",
) -> str:
    """Return hybrid BM25 + vector chunk search results fused with RRF."""

    try:
        results = _service.hybrid_search(
            query=query,
            top_k=max(1, int(top_k)),
            target_node_ids=json.loads(target_node_ids) if target_node_ids else [],
            document_ids=json.loads(document_ids) if document_ids else [],
            chunk_refs=json.loads(chunk_refs) if chunk_refs else [],
            source_pages=json.loads(source_pages) if source_pages else [],
        )
        return json.dumps(results, ensure_ascii=False, default=str)
    except Exception as exc:  # pragma: no cover - passthrough error handling
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
