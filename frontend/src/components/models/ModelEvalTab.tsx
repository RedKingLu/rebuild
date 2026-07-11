/** ModelEvalTab (R15-4-C10 + R16-B E4): imported Agent×Model evaluation results - DISPLAY ONLY.
 *
 * R16-B E4 enhancements:
 *  - Multi-dimension filter toolbar (agent_type / task_type / scenario / model_id)
 *  - CSV batch import button (stdlib csv on the backend; no new frontend dependency)
 *  - Cross-model comparison view for a selected (task_type, scenario) pair
 *  - Rank-by-score, framed as imported-data-only (red line #9: NOT auto-eval engine)
 *
 * Red line #9/#10/#11: every row shows source / eval_method / limitations / sample_count.
 * Rankings come from imported data and do NOT represent the model's global capability.
 * Empty state explicit. No ranking presented as an absolute global verdict. No auto-eval entry.
 */
import { useEffect, useState, useCallback } from "react";
import {
  listModelEvaluations, importEvaluationsCsv, compareEvaluations,
  type ModelEvalResult, type EvalCompareData,
} from "../../services/modelService";

type Mode = "list" | "compare";

const EMPTY = "-";

export function ModelEvalTab() {
  const [evals, setEvals] = useState<ModelEvalResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // filter state
  const [fAgent, setFAgent] = useState("");
  const [fTask, setFTask] = useState("");
  const [fScenario, setFScenario] = useState("");
  const [fModel, setFModel] = useState("");

  // compare state
  const [mode, setMode] = useState<Mode>("list");
  const [cmp, setCmp] = useState<EvalCompareData | null>(null);
  const [cmpTask, setCmpTask] = useState("");
  const [cmpScenario, setCmpScenario] = useState("");

  const fetchList = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const params = {
        agent_type: fAgent || undefined,
        task_type: fTask || undefined,
        scenario: fScenario || undefined,
        model_id: fModel || undefined,
      };
      const d = await listModelEvaluations(params);
      setEvals(d.evaluations);
    } catch (e) { setError(e instanceof Error ? e.message : "加载评测结果失败"); }
    finally { setLoading(false); }
  }, [fAgent, fTask, fScenario, fModel]);

  useEffect(() => { if (mode === "list") fetchList(); }, [mode, fetchList]);

  // distinct filter values derived from loaded evals
  const vals = useCallback((key: keyof Pick<ModelEvalResult,"agent_type"|"task_type"|"scenario">) =>
    Array.from(new Set(evals.map((e) => e[key]).filter(Boolean))).sort() as string[], [evals]);

  const onCsv = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; if (!file) return;
    setBusy(true); setNotice(null);
    try {
      const r = await importEvaluationsCsv(file);
      setNotice(`CSV 导入完成：写入 ${r.imported} 条${r.errors?.length ? `，${r.errors.length} 条跳过` : ""}`);
      await fetchList();
    } catch (err) { setNotice("导入失败：" + (err instanceof Error ? err.message : "未知错误")); }
    finally { setBusy(false); e.target.value = ""; }
  };

  const onCompare = async () => {
    if (!cmpTask || !cmpScenario) { setNotice("对比视图需同时选择 任务类型 与 场景"); return; }
    setBusy(true); setError(null);
    try {
      const d = await compareEvaluations(cmpTask, cmpScenario);
      setCmp(d); setMode("compare");
    } catch (e) { setError(e instanceof Error ? e.message : "对比查询失败"); }
    finally { setBusy(false); }
  };

  const inputSm = { fontSize: 13, padding: "5px 8px" } as const;

  return (
    <div>
      {/* Disclaimer (R15-4 / R16-B: display-only) */}
      <div className="eval-disclaimer">
        ⚠️ 评测结果来自<strong>导入数据</strong>，<strong>非平台自动评测</strong>，也<strong>不代表模型的全局能力</strong>。
        每条评测均附方法学与局限性，请结合场景判断。
      </div>

      {/* Toolbar: filters + actions */}
      <div className="row" style={{ gap: 8, margin: "10px 0", flexWrap: "wrap", alignItems: "center" }}>
        <select className="inp" style={inputSm} value={fAgent} onChange={(e) => setFAgent(e.target.value)}>
          <option value="">全部 Agent 类型</option>
          {vals("agent_type").map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
        <select className="inp" style={inputSm} value={fTask} onChange={(e) => setFTask(e.target.value)}>
          <option value="">全部任务类型</option>
          {vals("task_type").map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
        <select className="inp" style={inputSm} value={fScenario} onChange={(e) => setFScenario(e.target.value)}>
          <option value="">全部场景</option>
          {vals("scenario").map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
        <input className="inp" style={inputSm} placeholder="按模型 ID 过滤…" value={fModel}
               onChange={(e) => setFModel(e.target.value)} />
        <button className="btn sm" onClick={fetchList} disabled={busy}>筛选</button>
        <label className="btn sm ghost" style={{ cursor: "pointer" }}>
          {busy ? "导入中…" : "导入 CSV"}
          <input type="file" accept=".csv" style={{ display: "none" }} onChange={onCsv} disabled={busy} />
        </label>
      </div>

      {/* Compare trigger */}
      <div className="row" style={{ gap: 8, margin: "6px 0 12px", flexWrap: "wrap", alignItems: "center" }}>
        <span className="sub" style={{ fontSize: 12 }}>对比（同任务+场景，多模型横向）：</span>
        <select className="inp" style={inputSm} value={cmpTask} onChange={(e) => setCmpTask(e.target.value)}>
          <option value="">选择任务类型</option>
          {vals("task_type").map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
        <select className="inp" style={inputSm} value={cmpScenario} onChange={(e) => setCmpScenario(e.target.value)}>
          <option value="">选择场景</option>
          {vals("scenario").map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
        <button className="btn sm" onClick={onCompare} disabled={busy || !cmpTask || !cmpScenario}>查看对比</button>
        {mode === "compare" && <button className="btn sm ghost" onClick={() => setMode("list")}>返回列表</button>}
      </div>

      {notice && <div className="sub" style={{ fontSize: 12, marginBottom: 8, color: "var(--ink-2)" }}>{notice}</div>}
      {error && <div className="empty" style={{ color: "var(--red)" }}>加载失败：{error}</div>}

      {/* LIST MODE */}
      {mode === "list" && !loading && !error && (
        evals.length === 0
          ? <div className="empty"><p className="sub">暂无评测数据（评测数据由社区/平台导入，支持 JSON 与 CSV 批量导入）。</p></div>
          : <div className="eval-grid">
              {evals.map((e) => (
                <div key={e.eval_id} className="card" style={{ padding: 14 }}>
                  <div className="spread">
                    <b>{e.scenario || e.eval_id}</b>
                    <span className="tag grey" style={{ fontSize: 11 }}>{e.task_type || EMPTY}</span>
                  </div>
                  <div className="sub" style={{ fontSize: 11, marginTop: 2 }}>
                    模型：{e.model_id} · 指标：{e.metric || EMPTY} · 分数：{e.score ?? EMPTY} · 成功率：{e.success_rate ?? EMPTY} · 样本：{e.sample_count ?? EMPTY}
                  </div>
                  <div className="sub" style={{ fontSize: 11, marginTop: 2 }}>
                    Agent 类型：{e.agent_type || EMPTY} · 成本：{e.cost_level || EMPTY} · 时延：{e.latency_level || EMPTY}
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
      )}

      {/* COMPARE MODE */}
      {mode === "compare" && cmp && (
        <div>
          <h3 style={{ fontSize: 15, margin: "0 0 8px" }}>横向对比 · {cmp.task_type} · {cmp.scenario}</h3>
          <p className="sub" style={{ fontSize: 11, marginBottom: 8 }}>{cmp.note}</p>
          {cmp.rows.length === 0
            ? <div className="empty"><p className="sub">该任务+场景下暂无可对比的评测数据。</p></div>
            : <table className="eval-table" style={{ width: "100%" }}>
                <thead>
                  <tr>
                    <th>排名<br /><span className="muted">(按分数)</span></th>
                    <th>模型</th><th>指标</th><th>分数</th><th>成功率</th><th>样本</th><th>来源</th><th>方法学</th><th>局限性</th>
                  </tr>
                </thead>
                <tbody>
                  {cmp.rows.map((r, i) => (
                    <tr key={r.eval_id}>
                      <td>{i + 1}</td>
                      <td className="mono" style={{ fontSize: 11 }}>{r.model_id}</td>
                      <td>{r.metric ?? EMPTY}</td>
                      <td>{r.score ?? EMPTY}</td>
                      <td>{r.success_rate ?? EMPTY}</td>
                      <td>{r.sample_count ?? EMPTY}</td>
                      <td style={{ fontSize: 11 }}>{r.source || EMPTY}</td>
                      <td style={{ fontSize: 11 }}>{r.eval_method || EMPTY}</td>
                      <td style={{ fontSize: 11, maxWidth: 220 }}>{r.limitations || EMPTY}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
          }
        </div>
      )}

      {loading && <div className="empty">加载中…</div>}
    </div>
  );
}
