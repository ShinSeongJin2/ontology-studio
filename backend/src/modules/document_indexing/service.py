"""Document OCR, chunking, embedding, and Neo4j ingestion pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from ...shared.kernel.model_profiles import resolve_model_profile
from ...shared.kernel.settings import get_settings
from ..files.service import list_local_upload_files
from ..ontology.tools import get_driver
from .ocr_service import PDFOCRService

IndexingProgressCallback = Callable[[int, str, dict[str, Any] | None], Awaitable[None]]

DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 150
CHUNK_FULLTEXT_INDEX_NAME = "chunk_source_text_fulltext"
CHUNK_VECTOR_INDEX_NAME = "chunk_embedding_vector"


@dataclass(frozen=True)
class ChunkRecord:
    """Serializable chunk ready for embedding and persistence."""

    chunk_id: str
    chunk_ref: str
    document_id: str
    document_name: str
    source_page: int
    chunk_index: int
    source_text: str
    char_count: int
    text_hash: str
    page_text_hash: str
    page_sha256: str
    embedding: list[float]


class DocumentIndexingService:
    """Build document and chunk graph from uploaded PDFs."""

    def __init__(self, ocr_service: PDFOCRService | None = None) -> None:
        self._ocr_service = ocr_service or PDFOCRService()

    async def _emit_progress(
        self,
        on_progress: IndexingProgressCallback | None,
        progress: int,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if on_progress is None:
            return
        await on_progress(max(0, min(100, int(progress))), message, detail)

    def _scale_progress(self, start: int, end: int, current: int, total: int) -> int:
        if total <= 0:
            return end
        fraction = max(0.0, min(1.0, current / total))
        return max(start, min(end, int(round(start + ((end - start) * fraction)))))

    def list_uploaded_pdf_paths(self) -> list[Path]:
        """Return uploaded PDFs available for OCR and indexing."""

        return [
            path
            for path in list_local_upload_files()
            if path.suffix.lower() == ".pdf" and path.is_file()
        ]

    async def ingest_uploaded_pdfs(
        self,
        on_progress: IndexingProgressCallback | None = None,
    ) -> dict[str, Any]:
        """OCR, chunk, embed, and upload all uploaded PDFs."""

        pdf_paths = self.list_uploaded_pdf_paths()
        if not pdf_paths:
            raise ValueError("OCR 대상 PDF가 없습니다. 먼저 PDF를 업로드하세요.")

        results = []
        total_files = len(pdf_paths)
        for file_index, pdf_path in enumerate(pdf_paths, start=1):
            file_progress_start = self._scale_progress(0, 100, file_index - 1, total_files)
            file_progress_end = self._scale_progress(0, 100, file_index, total_files)

            async def relay_progress(
                progress: int,
                message: str,
                detail: dict[str, Any] | None,
            ) -> None:
                mapped_progress = file_progress_start + int(
                    ((file_progress_end - file_progress_start) * progress) / 100
                )
                payload = dict(detail or {})
                payload["documentName"] = pdf_path.name
                payload["documentIndex"] = file_index
                payload["documentTotal"] = total_files
                await self._emit_progress(on_progress, mapped_progress, message, payload)

            result = await self.ingest_pdf(pdf_path, on_progress=relay_progress)
            results.append(result)

        return {
            "documentCount": len(results),
            "documents": results,
        }

    async def ingest_pdf(
        self,
        pdf_path: str | Path,
        on_progress: IndexingProgressCallback | None = None,
    ) -> dict[str, Any]:
        """OCR, chunk, embed, and upload a single PDF."""

        path = Path(pdf_path)
        if not path.exists():
            raise ValueError(f"PDF 파일을 찾을 수 없습니다: {path}")

        document_id = path.name
        pdf_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        ocr_result = await self._ocr_service.extract_text_from_path_with_metadata(
            path,
            on_progress=on_progress,
        )
        pages = ocr_result.get("pages", [])
        if not pages:
            raise ValueError(f"OCR 결과가 비어 있습니다: {path.name}")

        await self._emit_progress(
            on_progress,
            90,
            "청크 분할 및 임베딩 준비 중",
            {"stage": "chunking", "documentName": path.name},
        )
        chunk_payloads = self._build_chunk_payloads(document_id, path.name, pages)
        embeddings = await self._embed_texts(
            [chunk["source_text"] for chunk in chunk_payloads],
            on_progress=on_progress,
        )
        chunk_records = [
            ChunkRecord(
                **chunk,
                embedding=embedding,
            )
            for chunk, embedding in zip(chunk_payloads, embeddings, strict=True)
        ]
        await self._emit_progress(
            on_progress,
            96,
            "Neo4j에 OCR 청크 업로드 중",
            {"stage": "neo4j_upsert", "documentName": path.name},
        )
        self._upsert_document_graph(
            document_id=document_id,
            document_name=path.name,
            source_path=str(path),
            document_sha256=pdf_sha256,
            page_count=ocr_result.get("pageCount", len(pages)),
            ocr_engine=ocr_result.get("ocrEngine"),
            chunk_records=chunk_records,
        )
        return {
            "documentId": document_id,
            "documentName": path.name,
            "documentSha256": pdf_sha256,
            "pageCount": ocr_result.get("pageCount", len(pages)),
            "chunkCount": len(chunk_records),
            "ocrAppliedPages": ocr_result.get("ocrAppliedPages", []),
            "ocrCachedPages": ocr_result.get("ocrCachedPages", []),
            "ocrEngine": ocr_result.get("ocrEngine"),
        }

    def _build_chunk_payloads(
        self,
        document_id: str,
        document_name: str,
        pages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        chunk_payloads: list[dict[str, Any]] = []
        for page in pages:
            page_number = int(page.get("pageNumber", 0))
            page_text = str(page.get("text", "")).strip()
            if not page_text:
                continue
            chunks = self._split_text(page_text)
            for chunk_index, chunk_text in enumerate(chunks, start=1):
                chunk_id = f"{document_id}::p{page_number:04d}::c{chunk_index:03d}"
                chunk_ref = f"{document_name}#page={page_number:04d}#chunk={chunk_index:03d}"
                chunk_payloads.append(
                    {
                        "chunk_id": chunk_id,
                        "chunk_ref": chunk_ref,
                        "document_id": document_id,
                        "document_name": document_name,
                        "source_page": page_number,
                        "chunk_index": chunk_index,
                        "source_text": chunk_text,
                        "char_count": len(chunk_text),
                        "text_hash": hashlib.sha256(
                            chunk_text.encode("utf-8", errors="ignore")
                        ).hexdigest(),
                        "page_text_hash": str(page.get("textHash", "")),
                        "page_sha256": str(page.get("pageSha256", "")),
                    }
                )
        return chunk_payloads

    def _split_text(self, text: str) -> list[str]:
        blocks = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        if not blocks:
            return []

        chunks: list[str] = []
        current = ""
        for block in blocks:
            candidate = f"{current}\n\n{block}".strip() if current else block
            if len(candidate) <= DEFAULT_CHUNK_SIZE:
                current = candidate
                continue
            if current:
                chunks.append(current)
            if len(block) <= DEFAULT_CHUNK_SIZE:
                current = block
                continue
            start = 0
            while start < len(block):
                end = min(len(block), start + DEFAULT_CHUNK_SIZE)
                piece = block[start:end].strip()
                if piece:
                    chunks.append(piece)
                if end >= len(block):
                    break
                start = max(end - DEFAULT_CHUNK_OVERLAP, start + 1)
            current = ""
        if current:
            chunks.append(current)
        return chunks

    async def _embed_texts(
        self,
        texts: list[str],
        on_progress: IndexingProgressCallback | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []

        settings = get_settings()
        profile = resolve_model_profile(
            purpose="ocr_embedding",
            model_name=settings.ocr_embedding_model,
            openai_base_url=settings.openai_base_url,
            openai_api_key=settings.openai_api_key,
        )
        if not profile.is_openai:
            raise ValueError(
                f"OCR embedding provider is not supported yet: {profile.provider}"
            )
        if not profile.api_key:
            raise ValueError("OPENAI_API_KEY is required for OCR embeddings")

        await self._emit_progress(
            on_progress,
            92,
            "OCR 청크 임베딩 생성 중",
            {"stage": "embedding", "chunkCount": len(texts)},
        )

        batch_size = 32
        embeddings: list[list[float]] = []
        for offset in range(0, len(texts), batch_size):
            batch = texts[offset : offset + batch_size]
            batch_embeddings = await asyncio.to_thread(
                self._embed_batch_sync,
                profile.model_name,
                profile.base_url,
                profile.api_key,
                batch,
            )
            embeddings.extend(batch_embeddings)
        return embeddings

    def _embed_batch_sync(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        texts: list[str],
    ) -> list[list[float]]:
        from openai import OpenAI

        client_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": base_url or "https://api.openai.com/v1",
        }
        client = OpenAI(**client_kwargs)
        response = client.embeddings.create(model=model_name, input=texts)
        return [list(item.embedding) for item in response.data]

    def _upsert_document_graph(
        self,
        *,
        document_id: str,
        document_name: str,
        source_path: str,
        document_sha256: str,
        page_count: int,
        ocr_engine: str | None,
        chunk_records: list[ChunkRecord],
    ) -> None:
        if not chunk_records:
            raise ValueError(f"업로드할 청크가 없습니다: {document_name}")

        driver = get_driver()
        embedding_dimensions = len(chunk_records[0].embedding)
        with driver.session() as session:
            session.run(
                """
                CREATE CONSTRAINT document_id_unique IF NOT EXISTS
                FOR (d:Document) REQUIRE d.document_id IS UNIQUE
                """
            )
            session.run(
                """
                CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
                FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE
                """
            )
            session.run(
                f"""
                CREATE FULLTEXT INDEX {CHUNK_FULLTEXT_INDEX_NAME} IF NOT EXISTS
                FOR (c:Chunk) ON EACH [c.source_text]
                """
            )
            session.run(
                f"""
                CREATE VECTOR INDEX {CHUNK_VECTOR_INDEX_NAME} IF NOT EXISTS
                FOR (c:Chunk) ON (c.embedding)
                OPTIONS {{
                  indexConfig: {{
                    `vector.dimensions`: {embedding_dimensions},
                    `vector.similarity_function`: 'cosine'
                  }}
                }}
                """
            )
            session.run(
                """
                MERGE (d:Document {document_id: $document_id})
                ON CREATE SET d.created_at = datetime()
                SET d.name = $document_name,
                    d.file_name = $document_name,
                    d.source_path = $source_path,
                    d.document_sha256 = $document_sha256,
                    d.page_count = $page_count,
                    d.ocr_engine = $ocr_engine,
                    d.updated_at = datetime()
                """,
                {
                    "document_id": document_id,
                    "document_name": document_name,
                    "source_path": source_path,
                    "document_sha256": document_sha256,
                    "page_count": page_count,
                    "ocr_engine": ocr_engine,
                },
            )
            session.run(
                """
                MATCH (d:Document {document_id: $document_id})
                OPTIONAL MATCH (d)-[:HAS_CHUNK]->(existing:Chunk)
                WITH d, collect(existing.chunk_id) AS existing_ids
                UNWIND $chunks AS chunk
                MERGE (c:Chunk {chunk_id: chunk.chunk_id})
                ON CREATE SET c.created_at = datetime()
                SET c.document_id = chunk.document_id,
                    c.document_name = chunk.document_name,
                    c.chunk_ref = chunk.chunk_ref,
                    c.source_text = chunk.source_text,
                    c.source_page = chunk.source_page,
                    c.chunk_index = chunk.chunk_index,
                    c.char_count = chunk.char_count,
                    c.text_hash = chunk.text_hash,
                    c.page_text_hash = chunk.page_text_hash,
                    c.page_sha256 = chunk.page_sha256,
                    c.embedding = chunk.embedding,
                    c.updated_at = datetime()
                MERGE (d)-[:HAS_CHUNK]->(c)
                """,
                {
                    "document_id": document_id,
                    "chunks": [self._chunk_to_payload(chunk) for chunk in chunk_records],
                },
            )
            session.run(
                """
                MATCH (d:Document {document_id: $document_id})-[old_rel:HAS_CHUNK]->(stale:Chunk)
                WHERE NOT stale.chunk_id IN $chunk_ids
                DELETE old_rel
                DETACH DELETE stale
                """,
                {
                    "document_id": document_id,
                    "chunk_ids": [chunk.chunk_id for chunk in chunk_records],
                },
            )
            session.run(
                """
                MATCH (d:Document {document_id: $document_id})-[:HAS_CHUNK]->(:Chunk)-[r:NEXT_CHUNK]->(:Chunk)
                DELETE r
                """,
                {"document_id": document_id},
            )
            links = self._build_chunk_links(chunk_records)
            if links:
                session.run(
                    """
                    UNWIND $links AS link
                    MATCH (d:Document {document_id: $document_id})-[:HAS_CHUNK]->(from_chunk:Chunk {chunk_id: link.from_chunk_id})
                    MATCH (d)-[:HAS_CHUNK]->(to_chunk:Chunk {chunk_id: link.to_chunk_id})
                    MERGE (from_chunk)-[r:NEXT_CHUNK]->(to_chunk)
                    ON CREATE SET r.created_at = datetime()
                    SET r.updated_at = datetime()
                    """,
                    {"document_id": document_id, "links": links},
                )

    def _chunk_to_payload(self, chunk: ChunkRecord) -> dict[str, Any]:
        return {
            "chunk_id": chunk.chunk_id,
            "chunk_ref": chunk.chunk_ref,
            "document_id": chunk.document_id,
            "document_name": chunk.document_name,
            "source_page": chunk.source_page,
            "chunk_index": chunk.chunk_index,
            "source_text": chunk.source_text,
            "char_count": chunk.char_count,
            "text_hash": chunk.text_hash,
            "page_text_hash": chunk.page_text_hash,
            "page_sha256": chunk.page_sha256,
            "embedding": chunk.embedding,
        }

    def _build_chunk_links(self, chunk_records: list[ChunkRecord]) -> list[dict[str, str]]:
        ordered = sorted(
            chunk_records,
            key=lambda chunk: (chunk.source_page, chunk.chunk_index),
        )
        return [
            {
                "from_chunk_id": ordered[index].chunk_id,
                "to_chunk_id": ordered[index + 1].chunk_id,
            }
            for index in range(len(ordered) - 1)
        ]

    def reset_document_graph(self) -> None:
        """Delete Document and Chunk nodes created by OCR ingestion."""

        driver = get_driver()
        with driver.session() as session:
            session.run("MATCH (d:Document)-[r]->() DELETE r")
            session.run("MATCH (d:Document) DETACH DELETE d")
            session.run("MATCH (c:Chunk) DETACH DELETE c")

    def hybrid_search(
        self,
        *,
        query: str,
        top_k: int = 5,
        target_node_ids: list[str] | None = None,
        document_ids: list[str] | None = None,
        chunk_refs: list[str] | None = None,
        source_pages: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Run BM25 + vector retrieval over Chunk nodes and fuse with RRF."""

        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query is required")

        resolved_filters = self._resolve_target_node_filters(target_node_ids or [])
        document_filter = sorted(
            {
                *[str(item) for item in document_ids or [] if str(item).strip()],
                *resolved_filters["document_ids"],
            }
        )
        chunk_ref_filter = sorted(
            {
                *[str(item) for item in chunk_refs or [] if str(item).strip()],
                *resolved_filters["chunk_refs"],
            }
        )
        source_page_filter = sorted(
            {
                *[int(item) for item in source_pages or []],
                *resolved_filters["source_pages"],
            }
        )

        vector_hits = self._query_vector_index(
            query=normalized_query,
            top_k=max(top_k * 5, 20),
            document_ids=document_filter,
            chunk_refs=chunk_ref_filter,
            source_pages=source_page_filter,
        )
        fulltext_hits = self._query_fulltext_index(
            query=normalized_query,
            top_k=max(top_k * 5, 20),
            document_ids=document_filter,
            chunk_refs=chunk_ref_filter,
            source_pages=source_page_filter,
        )
        return self._fuse_ranked_results(vector_hits, fulltext_hits, top_k=top_k)

    def _resolve_target_node_filters(self, target_node_ids: list[str]) -> dict[str, list[Any]]:
        if not target_node_ids:
            return {"document_ids": [], "chunk_refs": [], "source_pages": []}

        driver = get_driver()
        with driver.session() as session:
            rows = session.run(
                """
                MATCH (n)
                WHERE elementId(n) IN $target_node_ids
                RETURN
                  n.document_id AS document_id,
                  n.chunk_ref AS chunk_ref,
                  n.source_page AS source_page
                """,
                {"target_node_ids": target_node_ids},
            )
            document_ids: set[str] = set()
            chunk_refs: set[str] = set()
            source_pages: set[int] = set()
            for row in rows:
                if row.get("document_id"):
                    document_ids.add(str(row["document_id"]))
                if row.get("chunk_ref"):
                    chunk_refs.add(str(row["chunk_ref"]))
                if row.get("source_page") is not None:
                    source_pages.add(int(row["source_page"]))
        return {
            "document_ids": sorted(document_ids),
            "chunk_refs": sorted(chunk_refs),
            "source_pages": sorted(source_pages),
        }

    def _query_vector_index(
        self,
        *,
        query: str,
        top_k: int,
        document_ids: list[str],
        chunk_refs: list[str],
        source_pages: list[int],
    ) -> list[dict[str, Any]]:
        query_embedding = self._embed_query(query)
        driver = get_driver()
        with driver.session() as session:
            rows = session.run(
                f"""
                CALL db.index.vector.queryNodes('{CHUNK_VECTOR_INDEX_NAME}', $candidate_limit, $embedding)
                YIELD node, score
                WHERE (size($document_ids) = 0 OR node.document_id IN $document_ids)
                  AND (size($chunk_refs) = 0 OR node.chunk_ref IN $chunk_refs)
                  AND (size($source_pages) = 0 OR node.source_page IN $source_pages)
                RETURN node, score
                LIMIT $candidate_limit
                """,
                {
                    "candidate_limit": top_k,
                    "embedding": query_embedding,
                    "document_ids": document_ids,
                    "chunk_refs": chunk_refs,
                    "source_pages": source_pages,
                },
            )
            return [self._serialize_chunk_row(row["node"], float(row["score"]), "vector") for row in rows]

    def _query_fulltext_index(
        self,
        *,
        query: str,
        top_k: int,
        document_ids: list[str],
        chunk_refs: list[str],
        source_pages: list[int],
    ) -> list[dict[str, Any]]:
        driver = get_driver()
        with driver.session() as session:
            rows = session.run(
                f"""
                CALL db.index.fulltext.queryNodes('{CHUNK_FULLTEXT_INDEX_NAME}', $query)
                YIELD node, score
                WHERE (size($document_ids) = 0 OR node.document_id IN $document_ids)
                  AND (size($chunk_refs) = 0 OR node.chunk_ref IN $chunk_refs)
                  AND (size($source_pages) = 0 OR node.source_page IN $source_pages)
                RETURN node, score
                LIMIT $candidate_limit
                """,
                {
                    "query": query,
                    "candidate_limit": top_k,
                    "document_ids": document_ids,
                    "chunk_refs": chunk_refs,
                    "source_pages": source_pages,
                },
            )
            return [self._serialize_chunk_row(row["node"], float(row["score"]), "fulltext") for row in rows]

    def _embed_query(self, query: str) -> list[float]:
        settings = get_settings()
        profile = resolve_model_profile(
            purpose="ocr_embedding",
            model_name=settings.ocr_embedding_model,
            openai_base_url=settings.openai_base_url,
            openai_api_key=settings.openai_api_key,
        )
        if not profile.is_openai or not profile.api_key:
            raise ValueError("OpenAI embedding configuration is required for hybrid search")
        return self._embed_batch_sync(
            profile.model_name,
            profile.base_url,
            profile.api_key,
            [query],
        )[0]

    def _serialize_chunk_row(
        self,
        node: Any,
        score: float,
        source: str,
    ) -> dict[str, Any]:
        properties = dict(node)
        return {
            "chunk_id": properties.get("chunk_id"),
            "chunk_ref": properties.get("chunk_ref"),
            "document_id": properties.get("document_id"),
            "source_page": properties.get("source_page"),
            "source_text": properties.get("source_text"),
            "score": score,
            "source": source,
        }

    def _fuse_ranked_results(
        self,
        vector_hits: list[dict[str, Any]],
        fulltext_hits: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        rrf_k = 60
        fused: dict[str, dict[str, Any]] = {}
        for source_name, hits in (("vector", vector_hits), ("fulltext", fulltext_hits)):
            for rank, hit in enumerate(hits, start=1):
                chunk_id = str(hit.get("chunk_id"))
                if not chunk_id:
                    continue
                current = fused.setdefault(
                    chunk_id,
                    {
                        "chunk_id": chunk_id,
                        "chunk_ref": hit.get("chunk_ref"),
                        "document_id": hit.get("document_id"),
                        "source_page": hit.get("source_page"),
                        "source_text": hit.get("source_text"),
                        "rrf_score": 0.0,
                        "vector_score": None,
                        "fulltext_score": None,
                        "matched_sources": [],
                    },
                )
                current["rrf_score"] += 1.0 / (rrf_k + rank)
                current[f"{source_name}_score"] = hit.get("score")
                if source_name not in current["matched_sources"]:
                    current["matched_sources"].append(source_name)

        ranked = sorted(
            fused.values(),
            key=lambda item: item["rrf_score"],
            reverse=True,
        )
        return ranked[: max(1, top_k)]
