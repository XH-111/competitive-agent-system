import { Plus, Sparkles } from "lucide-react";
import { FormEvent, useState } from "react";
import { api } from "../api/client";
import type { Task, TaskFormValues } from "../types";

const examples: Array<{ label: string; values: TaskFormValues }> = [
  {
    label: "使用示例：AI 编程工具",
    values: {
      taskName: "AI 编程工具竞品分析",
      competitors: "Cursor, Trae, GitHub Copilot",
      region: "全球",
      industry: "AI Coding Agent",
      collectionStrategyMode: "balanced",
    },
  },
  {
    label: "使用示例：企业协作工具",
    values: {
      taskName: "企业协作工具竞品分析",
      competitors: "飞书, 钉钉, 企业微信",
      region: "中国",
      industry: "B2B SaaS",
      collectionStrategyMode: "balanced",
    },
  },
  {
    label: "使用示例：美妆电商平台",
    values: {
      taskName: "美妆电商平台竞品分析",
      competitors: "Sephora, Ulta Beauty, Watsons",
      region: "全球",
      industry: "Beauty Retail",
      collectionStrategyMode: "balanced",
    },
  },
];

function Field({
  label,
  value,
  placeholder,
  helper,
  error,
  onChange,
}: {
  label: string;
  value: string;
  placeholder: string;
  helper: string;
  error?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="text-sm font-semibold text-ink">{label}</span>
      <input
        className={`mt-1 w-full rounded border px-3 py-2 ${error ? "border-danger bg-red-50" : "border-line bg-white"}`}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
      <span className="mt-1 block text-xs leading-5 text-slate-500">{helper}</span>
      {error && <span className="mt-1 block text-xs font-semibold text-danger">{error}</span>}
    </label>
  );
}

export function TaskForm({ onCreated }: { onCreated: (task: Task) => void }) {
  const [form, setForm] = useState<TaskFormValues>(examples[1].values);
  const [errors, setErrors] = useState<Partial<Record<keyof TaskFormValues, string>>>({});
  const [busy, setBusy] = useState(false);

  function update(field: keyof TaskFormValues, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
  }

  function parseCompetitors(value: string) {
    return value.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
  }

  function validate() {
    const nextErrors: Partial<Record<keyof TaskFormValues, string>> = {};
    const competitorList = parseCompetitors(form.competitors);
    if (!form.taskName.trim()) nextErrors.taskName = "请输入分析任务名称。";
    if (competitorList.length < 2) nextErrors.competitors = "请至少输入 2 个竞品名称。";
    if (!form.region.trim()) nextErrors.region = "请输入分析区域。";
    if (!form.industry.trim()) nextErrors.industry = "请输入行业或产品类型。";
    setErrors(nextErrors);
    return { valid: Object.keys(nextErrors).length === 0, competitorList };
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const { valid, competitorList } = validate();
    if (!valid) return;
    setBusy(true);
    try {
      const task = await api.createTask({
        product_name: form.taskName.trim(),
        competitors: competitorList,
        region: form.region.trim(),
        industry: form.industry.trim(),
        collection_strategy_mode: form.collectionStrategyMode,
      });
      onCreated(task);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded border border-line bg-white p-4">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">创建分析任务</h2>
          <p className="mt-1 text-sm text-slate-600">
            填写分析目标和竞品范围。创建任务后，点击“运行竞品分析流程”开始执行。
          </p>
        </div>
        <Sparkles className="mt-1 text-accent" size={20} />
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {examples.map((example) => (
          <button
            key={example.label}
            type="button"
            onClick={() => {
              setForm(example.values);
              setErrors({});
            }}
            className="rounded border border-line bg-panel px-3 py-2 text-sm font-semibold text-ink hover:border-accent hover:text-accent"
          >
            {example.label}
          </button>
        ))}
      </div>

      <form onSubmit={submit} className="grid gap-4 md:grid-cols-2">
        <Field label="Task Name / 分析任务名称" value={form.taskName} placeholder="例如：企业协作工具竞品分析" helper="用于标识本次分析任务，并作为 Planner 的分析主题。" error={errors.taskName} onChange={(value) => update("taskName", value)} />
        <Field label="Competitors / 竞品名称" value={form.competitors} placeholder="例如：飞书, 钉钉, 企业微信" helper="多个竞品请用中文逗号或英文逗号分隔。" error={errors.competitors} onChange={(value) => update("competitors", value)} />
        <Field label="Region / 分析区域" value={form.region} placeholder="例如：中国 / 全球 / 北美" helper="用于限定 Planner 和信息采集的地域口径。" error={errors.region} onChange={(value) => update("region", value)} />
        <Field label="Industry / 行业或产品类型" value={form.industry} placeholder="例如：B2B SaaS / 电商平台 / AI 编程工具" helper="用于辅助 Planner 规划维度、问题和查询词。" error={errors.industry} onChange={(value) => update("industry", value)} />
        <label className="block md:col-span-2">
          <span className="text-sm font-semibold text-ink">采集策略模式</span>
          <select
            className="mt-1 w-full rounded border border-line bg-white px-3 py-2"
            value={form.collectionStrategyMode}
            onChange={(event) => setForm((current) => ({
              ...current,
              collectionStrategyMode: event.target.value as TaskFormValues["collectionStrategyMode"],
            }))}
          >
            <option value="simple">简易模式：query 3 / 维度 5 / QA 1</option>
            <option value="balanced">均衡模式：query 5 / 维度 10 / QA 2</option>
            <option value="expert">专家模式：query 8 / 维度 20 / QA 3</option>
          </select>
          <span className="mt-1 block text-xs leading-5 text-slate-500">
            控制 Collector 的采集数量、EvidenceQA 最低证据数和正文抽取范围。
          </span>
        </label>
        <div className="md:col-span-2">
          <button className="inline-flex items-center justify-center gap-2 rounded bg-accent px-4 py-2 font-semibold text-white disabled:opacity-50" disabled={busy}>
            <Plus size={16} /> 创建任务
          </button>
          <span className="ml-3 align-middle text-sm text-slate-600">创建后可在任务列表中选择并运行。</span>
        </div>
      </form>
    </section>
  );
}
