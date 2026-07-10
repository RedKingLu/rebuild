import { useEffect, useState } from "react";
import { api, type EvaluationItem } from "../api";
import { Empty } from "../components";

export function EvaluationsPage() {
  const [evals, setEvals] = useState<EvaluationItem[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.evaluations().then((d) => setEvals(d.evaluations)).catch((e) => setErr(String(e))); }, []);

  return (
    <div>
      <div className="section-title">评测结果</div>
      <div className="eval-top">⚠️ 评测来自导入数据，非平台自动评测，不代表模型全局能力。</div>
      {err && <Empty>加载失败：{err}</Empty>}
      {evals.length === 0 && <Empty>暂无评测数据（评测数据由社区/平台导入）</Empty>}
      <div className="card">
        <table>
          <thead><tr><th>模型</th><th>场景 / 任务</th><th>指标</th><th>分数</th><th>成功率</th><th>样本</th><th>来源</th></tr></thead>
          <tbody>
            {evals.map((e) => (
              <tr key={e.eval_id}>
                <td className="mono">{e.model_id}</td>
                <td>{e.scenario || "—"}<div className="muted">{e.task_type || ""}</div></td>
                <td>{e.metric || "—"}</td>
                <td>{e.score ?? "—"}</td>
                <td>{e.success_rate != null ? e.success_rate : "—"}</td>
                <td>{e.sample_count ?? "—"}</td>
                <td className="muted">{e.source || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {evals.map((e) => (
        <div key={e.eval_id} className="card" style={{ marginTop: 12 }}>
          <div className="card-name">{e.eval_id}</div>
          <div className="card-desc">方法：{e.eval_method || "未说明"}</div>
          <div className="card-desc">局限：{e.limitations || "未说明"}</div>
        </div>
      ))}
    </div>
  );
}
