import hashlib
import json
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.database import Base
from app.db_models import KnowledgeChunkRecordRow, KnowledgeItemRecordRow
from app.schemas import EvidenceAnalystOutput, Task
from app.services.embedding_service import EmbeddingService


class AnalystKnowledgeService:
    """把 EvidenceAnalyst 已回答的问题沉淀为长期知识。

    这里不处理 partial / not_found，只沉淀 answered 且带 evidence_ids 的问答。
    这些内容是基于已采集 Evidence 的二次结构化结果，适合用于后续 RAG 加速，
    但不能替代原始 Evidence，因此会用独立 source_type 与 public_evidence 区分。
    """

    def __init__(self, db: Session, embedding_service: EmbeddingService | None = None):
        self.db = db
        self.embedding_service = embedding_service or EmbeddingService()

    def ingest_answered_questions(
        self,
        *,
        task: Task,
        run_id: str | None,
        evidence_analyst_output: EvidenceAnalystOutput | None,
    ) -> dict[str, Any]:
        Base.metadata.create_all(bind=self.db.get_bind())
        result = {
            "source_type": "evidence_analyst_answer",
            "candidate_count": 0,
            "ingested": 0,
            "skipped_no_evidence": 0,
            "skipped_duplicate": 0,
            "failed": 0,
        }
        if evidence_analyst_output is None:
            return result

        for group in evidence_analyst_output.question_results:
            for answer in group.question_answers:
                # 只有经过 EvidenceAnalyst 明确回答的问题才有积累价值；缺口问题留给补采或问卷处理。
                if answer.answer_status != "answered":
                    continue
                result["candidate_count"] += 1
                # 没有 evidence_ids 的 answered 不可信，避免把无来源的分析结果写进长期知识库。
                if not answer.evidence_ids:
                    result["skipped_no_evidence"] += 1
                    continue
                try:
                    if self._ingest_one(
                        task=task,
                        run_id=run_id,
                        competitor=group.competitor,
                        dimension_id=group.dimension_id,
                        dimension_goal=group.dimension_goal,
                        question_id=answer.question_id,
                        question=answer.question,
                        answer=answer.answer,
                        evidence_ids=answer.evidence_ids,
                    ):
                        result["ingested"] += 1
                    else:
                        result["skipped_duplicate"] += 1
                except Exception:  # noqa: BLE001 - KB accumulation should not break report generation.
                    result["failed"] += 1
        return result

    def _ingest_one(
        self,
        *,
        task: Task,
        run_id: str | None,
        competitor: str | None,
        dimension_id: str | None,
        dimension_goal: str,
        question_id: str,
        question: str,
        answer: str,
        evidence_ids: list[str],
    ) -> bool:
        # 去重维度绑定 task/question/answer/evidence，避免同一轮或重复运行时反复写入相同问答。
        text = self._knowledge_text(
            competitor=competitor,
            dimension_id=dimension_id,
            dimension_goal=dimension_goal,
            question=question,
            answer=answer,
            evidence_ids=evidence_ids,
        )
        content_hash = hashlib.sha256(
            json.dumps(
                {
                    "task_id": task.task_id,
                    "question_id": question_id,
                    "answer": self._normalize(answer),
                    "evidence_ids": sorted(evidence_ids),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        duplicate = self.db.query(KnowledgeItemRecordRow).filter_by(content_hash=content_hash).first()
        if duplicate is not None:
            duplicate.updated_at = datetime.utcnow()
            self.db.commit()
            return False

        item_id = f"kb_analyst_{uuid4().hex[:10]}"
        now = datetime.utcnow()
        self.db.add(
            KnowledgeItemRecordRow(
                item_id=item_id,
                source_type="evidence_analyst_answer",
                competitor=competitor,
                industry=task.industry,
                region=task.region,
                title=question[:240],
                text=text,
                source_url=f"analyst-answer://{task.task_id}/{run_id or 'run'}/{question_id}",
                source_domain="evidence_analyst",
                source_quality="analysis",
                confidence=0.72,
                relevance_score=0.78,
                evidence_id=f"analyst_{question_id}",
                fact_id=question_id,
                task_id=task.task_id,
                run_id=run_id,
                content_hash=content_hash,
                created_at=now,
                updated_at=now,
            )
        )
        metadata = {
            # RAG 检索时可以通过 metadata 识别这是分析产物，而不是原始网页或问卷回答。
            "source_type": "evidence_analyst_answer",
            "task_id": task.task_id,
            "run_id": run_id,
            "competitor": competitor,
            "dimension_id": dimension_id,
            "dimension_goal": dimension_goal,
            "question_id": question_id,
            "answer_status": "answered",
            "evidence_ids": evidence_ids,
            "requires_verification": False,
            "confidence": 0.72,
            "relevance_score": 0.78,
        }
        chunk_id = f"kb_chunk_{hashlib.sha1(f'{item_id}:{text}'.encode('utf-8')).hexdigest()[:12]}"
        embedding = self.embedding_service.embed_text(text)
        self.db.add(
            KnowledgeChunkRecordRow(
                chunk_id=chunk_id,
                item_id=item_id,
                text=text,
                embedding=json.dumps(embedding),
                token_count=len(text.split()),
                metadata_json=json.dumps({**metadata, "chunk_id": chunk_id}, ensure_ascii=False),
                created_at=now,
            )
        )
        self.db.commit()
        return True

    @staticmethod
    def _knowledge_text(
        *,
        competitor: str | None,
        dimension_id: str | None,
        dimension_goal: str,
        question: str,
        answer: str,
        evidence_ids: list[str],
    ) -> str:
        return "\n".join(
            [
                "[EvidenceAnalyst Answer]",
                "This is a structured answer generated by EvidenceAnalyst from collected Evidence.",
                f"Competitor: {competitor or '-'}",
                f"Dimension: {dimension_id or '-'}",
                f"Dimension goal: {dimension_goal or '-'}",
                f"Question: {question}",
                f"Answer: {answer}",
                f"Evidence ids: {', '.join(evidence_ids)}",
            ]
        )

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()
