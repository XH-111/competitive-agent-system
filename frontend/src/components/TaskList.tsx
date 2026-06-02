import type { Task } from "../types";
import { Pill } from "../types";

export function TaskList({
  tasks,
  currentTaskId,
  onSelect
}: {
  tasks: Task[];
  currentTaskId?: string;
  onSelect: (task: Task) => void;
}) {
  return (
    <section className="rounded border border-line bg-white p-4">
      <h2 className="mb-3 text-lg font-semibold">任务列表</h2>
      <div className="overflow-auto rounded border border-line">
        <table className="w-full min-w-[980px] border-collapse text-left text-sm">
          <thead className="bg-panel">
            <tr>
              <th className="p-2">任务 ID task_id</th>
              <th className="p-2">任务名称</th>
              <th className="p-2">竞品</th>
              <th className="p-2">区域</th>
              <th className="p-2">行业</th>
              <th className="p-2">状态</th>
              <th className="p-2">创建时间 created_at</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => (
              <tr
                key={task.task_id}
                onClick={() => onSelect(task)}
                className={`cursor-pointer border-t border-line hover:bg-blue-50 ${currentTaskId === task.task_id ? "bg-blue-50" : ""}`}
              >
                <td className="p-2 font-semibold">{task.task_id}</td>
                <td className="p-2">{task.product_name}</td>
                <td className="p-2">{task.competitors.join("、")}</td>
                <td className="p-2">{task.region}</td>
                <td className="p-2">{task.industry}</td>
                <td className="p-2"><Pill value={task.status} /></td>
                <td className="p-2 text-xs text-slate-600">{task.created_at ? new Date(task.created_at).toLocaleString() : "-"}</td>
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
    </section>
  );
}
