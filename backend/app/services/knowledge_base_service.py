import hashlib
import json
import logging
import math
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.db_models import KnowledgeChunkRecordRow, KnowledgeItemRecordRow
from app.schemas import Evidence, KnowledgeChunk, KnowledgeItem, RetrievedKnowledgeChunk, Task
from app.services.embedding_service import EmbeddingService
from app.services.kb_ingestion_queue import enqueue_kb_ingestion_job

logger = logging.getLogger(__name__)

try:  # Optional integration: fallback remains active when LlamaIndex is absent.
    from llama_index.core import Document, VectorStoreIndex  # type: ignore
    from llama_index.core.node_parser import SentenceSplitter  # type: ignore
except Exception:  # noqa: BLE001
    Document = None  # type: ignore
    VectorStoreIndex = None  # type: ignore
    SentenceSplitter = None  # type: ignore


MIN_SNIPPET_CHARS = 80
DEFAULT_TOP_K = 5


class KbIngestionService:
    def __init__(self, db: Session, embedding_service: EmbeddingService | None = None):
        self.db = db
        self.embedding_service = embedding_service or EmbeddingService()

    def enqueue_for_evidence(self, evidence: list[Evidence], *, task: Task, run_id: str | None) -> None:
        candidates = [item for item in evidence if self._is_ingestable(item)]
        logger.info(
            "kb_ingestion_enqueued",
            extra={"task_id": task.task_id, "run_id": run_id, "candidate_count": len(candidates)},
        )
        if not candidates:
            return
        bind = self.db.get_bind()

        def job() -> None:
            JobSession = sessionmaker(bind=bind, autoflush=False, autocommit=False)
            with JobSession() as db:
                Base.metadata.create_all(bind=db.get_bind())
                KbIngestionService(db).ingest_evidence(candidates, task=task, run_id=run_id)

        enqueue_kb_ingestion_job(job)

    def ingest_evidence(self, evidence: list[Evidence], *, task: Task, run_id: str | None) -> dict[str, int]:
        Base.metadata.create_all(bind=self.db.get_bind())
        result = {"ingested": 0, "skipped_low_quality": 0, "skipped_duplicate": 0, "failed": 0}
        for item in evidence:
            if not self._is_ingestable(item):
                result["skipped_low_quality"] += 1
                logger.info("kb_ingestion_skipped_low_quality", extra={"evidence_id": item.evidence_id})
                continue
            try:
                if self._ingest_one(item, task=task, run_id=run_id):
                    result["ingested"] += 1
                else:
                    result["skipped_duplicate"] += 1
            except Exception:  # noqa: BLE001
                result["failed"] += 1
                logger.exception("kb_ingestion_failed", extra={"evidence_id": item.evidence_id})
        return result

    def _ingest_one(self, evidence: Evidence, *, task: Task, run_id: str | None) -> bool:
        text = self._text_for_evidence(evidence)
        content_hash = self._content_hash(text)
        duplicate = self.db.query(KnowledgeItemRecordRow).filter_by(content_hash=content_hash).first()
        if duplicate is not None:
            duplicate.updated_at = datetime.utcnow()
            self.db.commit()
            logger.info("kb_ingestion_skipped_duplicate", extra={"evidence_id": evidence.evidence_id})
            return False

        item = KnowledgeItem(
            source_type="public_evidence",
            competitor=evidence.competitor,
            industry=task.industry,
            region=task.region,
            title=evidence.page_title,
            text=text,
            source_url=evidence.url or "",
            source_domain=evidence.source_domain,
            source_quality=evidence.source_quality,
            confidence=evidence.confidence,
            relevance_score=evidence.relevance_score,
            evidence_id=evidence.evidence_id,
            task_id=task.task_id,
            run_id=run_id or evidence.run_id,
            content_hash=content_hash,
        )
        self.db.add(self._item_row(item))
        chunks = self._chunks_for_item(item, content_mode=evidence.content_mode)
        embeddings = self.embedding_service.embed_texts([chunk.text for chunk in chunks])
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            chunk.embedding = embedding
            self.db.add(self._chunk_row(chunk))
        self.db.commit()
        logger.info("kb_ingestion_success", extra={"evidence_id": evidence.evidence_id, "chunk_count": len(chunks)})
        return True

    def _chunks_for_item(self, item: KnowledgeItem, *, content_mode: str) -> list[KnowledgeChunk]:
        metadata = self._metadata_for_item(item, content_mode=content_mode)
        texts = self._split_text_with_llamaindex(item.text, metadata) or self._split_text(item.text)
        chunks: list[KnowledgeChunk] = []
        for index, text in enumerate(texts):
            chunk_id = f"kb_chunk_{hashlib.sha1(f'{item.item_id}:{index}:{text}'.encode('utf-8')).hexdigest()[:12]}"
            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    item_id=item.item_id,
                    text=text,
                    token_count=len(text.split()),
                    metadata_json={**metadata, "chunk_id": chunk_id},
                )
            )
        return chunks

    @staticmethod
    def _split_text_with_llamaindex(text: str, metadata: dict[str, Any]) -> list[str]:
        if Document is None or SentenceSplitter is None:
            return []
        try:
            document = Document(text=text, metadata=metadata)
            splitter = SentenceSplitter(chunk_size=512, chunk_overlap=80)
            nodes = splitter.get_nodes_from_documents([document])
            if VectorStoreIndex is not None:
                # Touch VectorStoreIndex capability without forcing external embeddings.
                _ = VectorStoreIndex
            return [node.get_content().strip() for node in nodes if node.get_content().strip()]
        except Exception:  # noqa: BLE001
            logger.exception("llamaindex_chunking_failed")
            return []

    @staticmethod
    def _split_text(text: str, *, chunk_size: int = 1200, overlap: int = 160) -> list[str]:
        if len(text) <= chunk_size:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            chunks.append(text[start : start + chunk_size].strip())
            start += max(1, chunk_size - overlap)
        return [chunk for chunk in chunks if chunk]

    @classmethod
    def _is_ingestable(cls, evidence: Evidence) -> bool:
        if evidence.relevance_level not in {"high", "medium"}:
            return False
        if evidence.source_quality == "low_quality":
            return False
        if evidence.confidence < 0.6:
            return False
        if not evidence.url:
            return False
        text = cls._text_for_evidence(evidence)
        if not text:
            return False
        if not evidence.page_fetch_success and len(text) < MIN_SNIPPET_CHARS:
            return False
        return True

    @staticmethod
    def _text_for_evidence(evidence: Evidence) -> str:
        return re.sub(r"\s+", " ", (evidence.content_excerpt or evidence.snippet or "")).strip()

    @staticmethod
    def _content_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _metadata_for_item(item: KnowledgeItem, *, content_mode: str) -> dict[str, Any]:
        now = item.updated_at.isoformat()
        return {
            "chunk_id": None,
            "source_type": item.source_type,
            "competitor": item.competitor,
            "industry": item.industry,
            "region": item.region,
            "source_url": item.source_url,
            "source_domain": item.source_domain,
            "source_quality": item.source_quality,
            "evidence_id": item.evidence_id,
            "run_id": item.run_id,
            "task_id": item.task_id,
            "content_mode": content_mode,
            "confidence": item.confidence,
            "relevance_score": item.relevance_score,
            "collected_at": item.created_at.isoformat(),
            "updated_at": now,
        }

    @staticmethod
    def _item_row(item: KnowledgeItem) -> KnowledgeItemRecordRow:
        return KnowledgeItemRecordRow(**item.model_dump(mode="python"))

    @staticmethod
    def _chunk_row(chunk: KnowledgeChunk) -> KnowledgeChunkRecordRow:
        return KnowledgeChunkRecordRow(
            chunk_id=chunk.chunk_id,
            item_id=chunk.item_id,
            text=chunk.text,
            embedding=json.dumps(chunk.embedding),
            token_count=chunk.token_count,
            metadata_json=json.dumps(chunk.metadata_json, ensure_ascii=False),
            created_at=chunk.created_at,
        )


class KbRetrieverService:
    def __init__(self, db: Session, embedding_service: EmbeddingService | None = None):
        self.db = db
        self.embedding_service = embedding_service or EmbeddingService()

    def retrieve_for_task(self, task: Task, *, selected_dimensions: list[str] | None = None, top_k: int = DEFAULT_TOP_K) -> list[RetrievedKnowledgeChunk]:
        logger.info("kb_retrieval_started", extra={"task_id": task.task_id, "top_k": top_k})
        query = self._query_for_task(task, selected_dimensions or [])
        query_embedding = self.embedding_service.embed_text(query)
        rows = self.db.query(KnowledgeChunkRecordRow).all()
        scored: list[RetrievedKnowledgeChunk] = []
        for row in rows:
            metadata = self._metadata(row)
            if not self._metadata_allowed(task, metadata):
                continue
            score = self._cosine(query_embedding, self._embedding(row))
            scored.append(
                RetrievedKnowledgeChunk(
                    chunk_id=row.chunk_id,
                    text=row.text,
                    score=round(score, 4),
                    metadata=metadata,
                    evidence_id=metadata.get("evidence_id"),
                    text_preview=row.text[:240],
                    source_url=metadata.get("source_url"),
                    source_domain=metadata.get("source_domain"),
                    source_quality=metadata.get("source_quality"),
                    updated_at=self._parse_datetime(metadata.get("updated_at")),
                )
            )
        output = sorted(scored, key=lambda item: item.score, reverse=True)[:top_k]
        logger.info("kb_retrieval_finished", extra={"task_id": task.task_id, "hit_count": len(output)})
        return output

    @staticmethod
    def _query_for_task(task: Task, selected_dimensions: list[str]) -> str:
        return " ".join([task.product_name, task.industry, task.region, *task.competitors, *selected_dimensions])

    @staticmethod
    def _metadata_allowed(task: Task, metadata: dict[str, Any]) -> bool:
        if metadata.get("source_quality") == "low_quality":
            return False
        competitor = metadata.get("competitor")
        if competitor and competitor not in task.competitors:
            return False
        industry = metadata.get("industry")
        if industry and task.industry and str(industry).lower() != task.industry.lower():
            return False
        region = metadata.get("region")
        if region and task.region and str(region).lower() != task.region.lower():
            return False
        return True

    @staticmethod
    def _metadata(row: KnowledgeChunkRecordRow) -> dict[str, Any]:
        try:
            return json.loads(row.metadata_json)
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _embedding(row: KnowledgeChunkRecordRow) -> list[float]:
        try:
            return [float(value) for value in json.loads(row.embedding)]
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        numerator = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if not left_norm or not right_norm:
            return 0.0
        return max(0.0, min(1.0, (numerator / (left_norm * right_norm) + 1.0) / 2.0))

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
