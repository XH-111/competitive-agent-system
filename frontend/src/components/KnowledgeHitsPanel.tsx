import { Database, ExternalLink } from "lucide-react";
import type { KnowledgeHit, KnowledgeRetrievalStrategy, WorkflowSummary } from "../types";

export function KnowledgeHitsPanel({ workflowSummary }: { workflowSummary?: WorkflowSummary }) {
  const hits = workflowSummary?.knowledge_hits ?? [];
  const strategy = workflowSummary?.knowledge_retrieval_strategy;
  const hasWorkflowRun = Boolean(workflowSummary?.workflow_engine_used);

  if (!hasWorkflowRun) return null;

  return (
    <section className="mb-4 rounded border border-line bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Database size={18} className="text-accent" />
            <h2 className="text-base font-semibold">长期知识库召回</h2>
          </div>
          <p className="mt-1 text-sm text-slate-600">
            展示本次 AnalystAgent 前从长期知识库召回的内容；当前运行 Evidence 仍然优先于历史知识。
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <MetricPill label="命中" value={`${workflowSummary?.retrieved_knowledge_chunk_count ?? hits.length} 条`} />
          <MetricPill label="Top K" value={String(strategy?.top_k ?? 5)} />
          <MetricPill label="相似度" value={strategyLabel(strategy?.similarity)} />
        </div>
      </div>

      <div className="mt-3 rounded border border-blue-100 bg-blue-50 px-3 py-2 text-xs leading-5 text-slate-700">
        <span className="font-semibold text-slate-900">找回策略：</span>
        {formatStrategy(strategy)}
      </div>

      {hits.length === 0 ? (
        <div className="mt-3 rounded border border-dashed border-line bg-panel p-4 text-sm text-slate-600">
          本次未命中长期知识库；系统仍会使用当前运行采集到的 Evidence 完成分析。
        </div>
      ) : (
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          {hits.map((hit, index) => (
            <KnowledgeHitCard key={hit.chunk_id || index} hit={hit} rank={index + 1} />
          ))}
        </div>
      )}
    </section>
  );
}

function KnowledgeHitCard({ hit, rank }: { hit: KnowledgeHit; rank: number }) {
  return (
    <article className="rounded border border-line bg-panel p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full bg-ink px-2 py-0.5 text-xs font-semibold text-white">#{rank}</span>
          <span className="rounded-full border border-blue-200 bg-white px-2 py-0.5 text-xs font-semibold text-accent">
            score {formatScore(hit.score)}
          </span>
          <span className="rounded-full border border-line bg-white px-2 py-0.5 text-xs text-slate-600">
            {hit.source_quality ?? "unknown"}
          </span>
        </div>
        {hit.source_url && (
          <a
            href={hit.source_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs font-semibold text-accent hover:underline"
          >
            打开来源 <ExternalLink size={13} />
          </a>
        )}
      </div>

      <p className="mt-3 max-h-24 overflow-hidden text-sm leading-6 text-slate-800">
        {hit.text_preview || "暂无摘要"}
      </p>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-line pt-2 text-xs text-slate-500">
        <span>来源域名：{hit.source_domain ?? "-"}</span>
        <span>evidence_id：{hit.evidence_id ?? "-"}</span>
        <span>更新：{formatDate(hit.updated_at)}</span>
      </div>
    </article>
  );
}

function MetricPill({ label, value }: { label: string; value: string }) {
  return (
    <span className="rounded border border-line bg-panel px-2 py-1">
      <span className="text-slate-500">{label}：</span>
      <span className="font-semibold text-ink">{value}</span>
    </span>
  );
}

function formatStrategy(strategy?: KnowledgeRetrievalStrategy): string {
  if (!strategy) {
    return "使用 knowledge_chunks.embedding 做余弦相似度召回，默认 top_k=5。";
  }
  if (strategy.retriever === "disabled_for_custom_runner") {
    return "Custom Runner 当前不启用长期知识库召回；请使用 LangGraph Runner 查看 RAG 命中。";
  }
  return [
    `${strategy.retriever ?? "KbRetrieverService"}`,
    `向量存储 ${strategy.vector_store ?? "sqlite_json_embedding"}`,
    `embedding ${strategy.embedding_provider ?? "local_hash_embedding"}`,
    `${strategyLabel(strategy.similarity)} top_${strategy.top_k ?? 5}`,
  ].join(" · ");
}

function strategyLabel(value?: string): string {
  if (!value) return "余弦相似度";
  if (value === "cosine_similarity") return "余弦相似度";
  return value;
}

function formatScore(score: number): string {
  if (!Number.isFinite(score)) return "-";
  return score.toFixed(3);
}

function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}
