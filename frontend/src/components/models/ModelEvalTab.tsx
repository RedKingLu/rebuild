/** ModelEvalTab (R15-4-C10): imported Agent×Model evaluation results — DISPLAY ONLY.

 * Red line #9/#10/#11: NOT an auto-evaluation engine. Every row MUST show
 * source / eval_method / limitations / sample_count. Header disclaimer states that
 * rankings come from imported data and do NOT represent the model's global capability.
 * Empty state explicit. No ranking is presented as an absolute global verdict.
 */
import { useEffect, useState } from "react";
import { listModelEvaluations, type ModelEvalResult } from "../../services/modelService";

export function ModelEvalTab() {
  const [evals, setEvals] = useState<ModelEvalResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError(null);
    listModelEvaluations()
      .then((d) => { if (alive) setEvals(d.evaluations); })
      .catch((e) => { if (alive) setError(String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  if (loading) return <div className="empty">加载中…</div>;
  if (error) return <div className="empty" style={{ color: "var(--red)" }}>加载失败：{error}</div>;

  return (
    <div>
      <div className="eval-disclaimer">
        ⚠️ 评测结果来自<strong>导入数据</strong>，<strong>非平台自动评测</strong>，也<strong>不代表模型全局能力</strong>。
        每条评测均附方法学与局限性，请结合场景判断。
      </div>
      {evals.length === 0 && (
        <div className="empty"><p className="sub">暂无评测数据（评测数据由社区/平台导入）。</p></div>
      )}
      <div className="eval-grid">
        {evals.map((e) => (
          <div key={e.eval_id} className="card" style={{ padding: 14 }}>
            <div className="spread">
              <b>{e.scenario || e.eval_id}</b>
              <span className="tag grey" style={{ fontSize: 11 }}>{e.task_type || "—"}</span>
            </div>
            <div className="sub" style={{ fontSize: 11, marginTop: 2 }}>
              模型：{e.model_id} · 指标：{e.metric || "—"} · 分数：{e.score ?? "—"} · 成功率：{e.success_rate ?? "—"} · 样本：{e.sample_count ?? "—"}
            </div>
            <table className="eval-table">
              <tbody>
                <tr><th>来源</th><td>{e.source || "未说明"}</td></tr>
                <tr><th>方法学</th><td>{e.eval_method || "未说明"}</td></tr>
                <tr><th>局限性</th><td>{e.limitations || "未说明"}</td></tr>
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}
