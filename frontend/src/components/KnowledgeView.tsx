import { Database } from "lucide-react";
import type { CapabilityBucket, CapabilityMap, Evidence, Report, SwotAnalysis, SwotItem } from "../types";

export function KnowledgeView({
  report,
  evidence,
  onEvidenceIdsSelect
}: {
  report?: Report;
  evidence?: Evidence[];
  onEvidenceIdsSelect?: (ids: string[]) => void;
}) {
  const knowledge = report?.json_report.core?.knowledge ?? report?.json_report.knowledge;
  if (!knowledge) return null;

  const swot = report?.json_report.core?.swot ?? report?.json_report.swot;
  const capabilityMap = (knowledge as { capability_map?: CapabilityMap }).capability_map;
  const evidenceById = new Map((evidence ?? []).map((item) => [item.evidence_id, item]));

  return (
    <section className="bg-white p-4">
      <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold"><Database size={18} /> 竞品知识 Knowledge</h2>
      {swot && (
        <div className="mb-3">
          <div className="mb-2 text-sm font-semibold text-slate-700">结构化 SWOT 摘要</div>
          <SwotPanel swot={swot} evidenceById={evidenceById} onEvidenceIdsSelect={onEvidenceIdsSelect} />
        </div>
      )}
      {capabilityMap && (
        <div className="mb-3">
          <div className="mb-2 text-sm font-semibold text-slate-700">标准化能力地图 Capability Map</div>
          <CapabilityMapPanel capabilityMap={capabilityMap} evidenceById={evidenceById} onEvidenceIdsSelect={onEvidenceIdsSelect} />
        </div>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        {Object.entries(knowledge).map(([key, value]) => {
          const evidenceIds = extractEvidenceIds(value);
          const insufficient = JSON.stringify(value).includes("Evidence is insufficient");
          return (
            <div key={key} className={`rounded border p-3 ${insufficient ? "border-amber-300 bg-amber-50" : "border-line bg-panel"}`}>
              <div className="mb-2 text-sm font-semibold">{key}</div>
              {insufficient && <div className="mb-2 rounded border border-amber-300 bg-white px-2 py-1 text-xs text-warning">当前公开证据不足，暂不做强结论。</div>}
              {evidenceIds.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1 text-xs">
                  {evidenceIds.map((id) => (
                    <button
                      key={id}
                      className="rounded border border-line bg-white px-2 py-0.5 text-slate-700 hover:border-accent"
                      onClick={() => onEvidenceIdsSelect?.([id])}
                      title={evidenceById.get(id)?.source_domain ?? id}
                    >
                      {id}
                    </button>
                  ))}
                </div>
              )}
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs leading-5">{JSON.stringify(value, null, 2)}</pre>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function CapabilityMapPanel({
  capabilityMap,
  evidenceById,
  onEvidenceIdsSelect,
}: {
  capabilityMap: CapabilityMap;
  evidenceById: Map<string, Evidence>;
  onEvidenceIdsSelect?: (ids: string[]) => void;
}) {
  const entries = Object.entries(capabilityMap.competitor_capabilities ?? {});
  if (!entries.length) return null;
  return (
    <div className="space-y-3">
      <div className="rounded border border-line bg-panel p-3 text-xs text-slate-700">
        领域配置 domain pack: {capabilityMap.domain_pack?.display_name ?? "-"}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {entries.map(([competitor, buckets]) => (
          <div key={competitor} className="rounded border border-line bg-panel p-3">
            <div className="mb-2 text-sm font-semibold">{competitor}</div>
            <div className="space-y-2">
              {buckets.map((bucket) => (
                <CapabilityBucketCard
                  key={`${competitor}-${bucket.capability_area}`}
                  bucket={bucket}
                  evidenceById={evidenceById}
                  onEvidenceIdsSelect={onEvidenceIdsSelect}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function CapabilityBucketCard({
  bucket,
  evidenceById,
  onEvidenceIdsSelect,
}: {
  bucket: CapabilityBucket;
  evidenceById: Map<string, Evidence>;
  onEvidenceIdsSelect?: (ids: string[]) => void;
}) {
  return (
    <div className="rounded border border-line bg-white p-3 text-xs leading-5">
      <div className="font-semibold">
        {bucket.capability_area} · {Math.round(bucket.confidence * 100)}%
      </div>
      <div className="mt-1">{bucket.summary}</div>
      {!!bucket.normalized_features.length && (
        <div className="mt-2 flex flex-wrap gap-1">
          {bucket.normalized_features.slice(0, 4).map((feature) => (
            <span key={feature} className="rounded border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] text-accent">
              {feature}
            </span>
          ))}
        </div>
      )}
      {!!bucket.evidence_ids.length && (
        <div className="mt-2 flex flex-wrap gap-1">
          {bucket.evidence_ids.slice(0, 4).map((id) => (
            <button
              key={id}
              className="rounded border border-line bg-panel px-2 py-0.5 text-slate-700 hover:border-accent"
              onClick={() => onEvidenceIdsSelect?.([id])}
              title={evidenceById.get(id)?.source_domain ?? id}
            >
              {id}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function SwotPanel({
  swot,
  evidenceById,
  onEvidenceIdsSelect,
}: {
  swot: SwotAnalysis;
  evidenceById: Map<string, Evidence>;
  onEvidenceIdsSelect?: (ids: string[]) => void;
}) {
  const sections: Array<{ key: keyof SwotAnalysis; label: string }> = [
    { key: "strengths", label: "优势 Strengths" },
    { key: "weaknesses", label: "劣势 Weaknesses" },
    { key: "opportunities", label: "机会 Opportunities" },
    { key: "threats", label: "威胁 Threats" },
  ];

  return (
    <div className="mb-3 grid gap-3 md:grid-cols-2">
      {sections.map(({ key, label }) => (
        <div key={key} className="rounded border border-line bg-panel p-3">
          <div className="mb-2 text-sm font-semibold">{label}</div>
          <div className="space-y-2">
            {swot[key].map((item, index) => (
              <SwotCard
                key={`${key}-${item.competitor ?? "overall"}-${index}`}
                item={item}
                evidenceById={evidenceById}
                onEvidenceIdsSelect={onEvidenceIdsSelect}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function SwotCard({
  item,
  evidenceById,
  onEvidenceIdsSelect,
}: {
  item: SwotItem;
  evidenceById: Map<string, Evidence>;
  onEvidenceIdsSelect?: (ids: string[]) => void;
}) {
  return (
    <div className="rounded border border-line bg-white p-3 text-xs leading-5">
      <div className="font-semibold">{item.competitor ?? "整体 overall"} · {Math.round(item.confidence * 100)}%</div>
      <div className="mt-1">{item.summary}</div>
      <div className="mt-2 flex flex-wrap gap-1">
        {item.evidence_ids.map((id) => (
          <button
            key={id}
            className="rounded border border-line bg-panel px-2 py-0.5 text-slate-700 hover:border-accent"
            onClick={() => onEvidenceIdsSelect?.([id])}
            title={evidenceById.get(id)?.source_domain ?? id}
          >
            {id}
          </button>
        ))}
      </div>
    </div>
  );
}

function extractEvidenceIds(value: unknown): string[] {
  const ids = new Set<string>();

  function walk(item: unknown) {
    if (Array.isArray(item)) {
      item.forEach(walk);
      return;
    }
    if (item && typeof item === "object") {
      for (const [key, nested] of Object.entries(item)) {
        if (key === "evidence_ids" && Array.isArray(nested)) {
          nested.forEach((id) => {
            if (typeof id === "string") ids.add(id);
          });
        } else {
          walk(nested);
        }
      }
    }
  }

  walk(value);
  return Array.from(ids);
}
