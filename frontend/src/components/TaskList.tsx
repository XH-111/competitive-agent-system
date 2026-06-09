import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import type { Task } from "../types";
import { Pill } from "../types";

const PAGE_SIZE = 8;

export function TaskList({
  tasks,
  currentTaskId,
  onSelect,
}: {
  tasks: Task[];
  currentTaskId?: string;
  onSelect: (task: Task) => void;
}) {
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(tasks.length / PAGE_SIZE));
  const pageTasks = tasks.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  useEffect(() => {
    setPage((current) => Math.min(current, pageCount));
  }, [pageCount]);

  return (
    <section className="rounded border border-line bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">任务列表</h2>
        <span className="text-xs text-slate-500">共 {tasks.length} 条</span>
      </div>

      <div className="overflow-auto rounded border border-line">
        <table className="w-full min-w-[980px] border-collapse text-left text-sm">
          <thead className="bg-panel">
            <tr>
              <th className="p-2">task_id</th>
              <th className="p-2">任务名称</th>
              <th className="p-2">竞品</th>
              <th className="p-2">区域</th>
              <th className="p-2">行业</th>
              <th className="p-2">状态</th>
              <th className="p-2">created_at</th>
            </tr>
          </thead>
          <tbody>
            {pageTasks.map((task) => (
              <tr
                key={task.task_id}
                onClick={() => onSelect(task)}
                className={`cursor-pointer border-t border-line hover:bg-blue-50 ${
                  currentTaskId === task.task_id ? "bg-blue-50" : ""
                }`}
              >
                <td className="p-2 font-semibold">{task.task_id}</td>
                <td className="p-2">{task.product_name}</td>
                <td className="p-2">{task.competitors.join("、")}</td>
                <td className="p-2">{task.region}</td>
                <td className="p-2">{task.industry}</td>
                <td className="p-2"><Pill value={task.status} /></td>
                <td className="p-2 text-xs text-slate-600">
                  {task.created_at ? new Date(task.created_at).toLocaleString() : "-"}
                </td>
              </tr>
            ))}
            {!tasks.length && (
              <tr>
                <td className="p-3 text-slate-500" colSpan={7}>暂无任务</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {tasks.length > PAGE_SIZE && (
        <div className="mt-3 flex items-center justify-end gap-2">
          <button
            type="button"
            title="上一页"
            aria-label="上一页"
            disabled={page === 1}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
            className="inline-flex size-8 items-center justify-center rounded border border-line bg-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ChevronLeft size={16} />
          </button>
          <span className="min-w-20 text-center text-sm text-slate-600">
            {page} / {pageCount}
          </span>
          <button
            type="button"
            title="下一页"
            aria-label="下一页"
            disabled={page === pageCount}
            onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
            className="inline-flex size-8 items-center justify-center rounded border border-line bg-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      )}
    </section>
  );
}
