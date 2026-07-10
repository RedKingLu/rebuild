/** ModelCatalogTab (R15-4-C9): persistent ModelCatalog data source for ModelsPage.

 * Data comes from GET /api/model-catalog (persistent model_catalog table, R15-4-C8),
 * NOT the in-memory ModelProfileInfo. Shows context window, max output, modalities,
 * capability/task tags, source, official icon/link, availability.
 * Filters: provider / family / availability / task tags.
 * Empty fields display "未登记" (never fabricated). Empty state explicit.
 * Header reflects that catalog = imported/seeded reference data, not an auto-eval engine.
 */
import { useEffect, useState } from "react";
import { listModelCatalog, type ModelCatalogEntry } from "../../services/modelService";
import { SourceBadge } from "../SourceBadge";

export function ModelCatalogTab() {
  const [models, setModels] = useState<ModelCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [provider, setProvider] = useState("");
  const [family, setFamily] = useState("");
  const [availability, setAvailability] = useState("");
  const [task, setTask] = useState("");

  useEffect(() => {
    let alive = true;
    setLoading(true); setError(null);
    listModelCatalog({ provider, family, availability, task })
      .then((d) => { if (alive) setModels(d.models); })
      .catch((e) => { if (alive) setError(String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [provider, family, availability, task]);

  const providers = Array.from(new Set(models.map((m) => m.provider_id))).sort();
  const families = Array.from(new Set(models.map((m) => m.family).filter(Boolean))).sort();
  const tasks = Array.from(new Set(models.flatMap((m) => m.task_tags))).sort();

  const v = (x: string | number | null | undefined, fallback = "未登记") =>
    (x === null || x === undefined || x === "" || x === 0) ? fallback : String(x);

  return (
    <div>
      {/* Toolbar: filters */}
      <div className="spread" style={{ marginBottom: 8 }}>
        <div className="row" style={{ gap: 6 }}>
          <span className="sub" style={{ fontSize: 12 }}>供应商：</span>
          <button className={`btn sm ${provider === "" ? "" : "ghost"}`} onClick={() => setProvider("")}>全部</button>
          {providers.map((p) => (
            <button key={p} className={`btn sm ${provider === p ? "" : "ghost"}`} onClick={() => setProvider(p)}>{p}</button>
          ))}
        </div>
        <div className="row" style={{ gap: 6 }}>
          <select className="inp" style={{ width: "auto" }} value={family} onChange={(e) => setFamily(e.target.value)}>
            <option value="">全部族</option>
            {families.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          <select className="inp" style={{ width: "auto" }} value={availability} onChange={(e) => setAvailability(e.target.value)}>
            <option value="">全部状态</option>
            <option value="available">可用</option>
            <option value="unavailable">不可用</option>
          </select>
          <select className="inp" style={{ width: "auto" }} value={task} onChange={(e) => setTask(e.target.value)}>
            <option value="">全部任务</option>
            {tasks.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </div>

      <p className="sub" style={{ fontSize: 11, marginBottom: 10 }}>
        模型资料库（持久化 catalog，数据来源：导入 / seed）。不代表平台自动评测结果，也不代表模型全局能力。
      </p>

      {error && <div className="empty" style={{ color: "var(--red)" }}>加载失败：{error}</div>}
      {loading && <div className="empty">加载中…</div>}
      {!loading && !error && models.length === 0 && (
        <div className="empty"><p className="sub">暂无模型资料。可在「供应商」页添加，或导入社区模型目录。</p></div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 10 }}>
        {models.map((m) => (
          <div key={m.catalog_id} className="card" style={{ padding: 12 }}>
            <div className="spread">
              <div className="row" style={{ gap: 8, alignItems: "center" }}>
                {m.official_icon_url
                  ? <img src={m.official_icon_url} alt={m.display_name} style={{ width: 28, height: 28, borderRadius: 6, objectFit: "cover" }} />
                  : <div className="card-icon fallback" style={{ width: 28, height: 28, fontSize: 13 }}>{(m.display_name || "?").slice(0, 1)}</div>}
                <b>{m.display_name}</b>
              </div>
              <SourceBadge source={m.source === "official" ? "official" : "community"} />
            </div>
            <div className="sub" style={{ fontSize: 11, marginTop: 2 }}>
              {v(m.provider_id)} · {v(m.family)} · {v(m.availability_status)}
            </div>
            <div style={{ marginTop: 6, fontSize: 12, display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 8px", lineHeight: 1.6 }}>
              <span className="sub">上下文窗口</span><span>{m.context_window ? Number(m.context_window).toLocaleString() : "未登记"}</span>
              <span className="sub">最大输出</span><span>{m.max_output_tokens ? Number(m.max_output_tokens).toLocaleString() : "未登记"}</span>
              <span className="sub">输入模态</span><span>{(m.input_modalities || []).join(", ") || "未登记"}</span>
              <span className="sub">输出模态</span><span>{(m.output_modalities || []).join(", ") || "未登记"}</span>
            </div>
            <div style={{ marginTop: 6, display: "flex", gap: 4, flexWrap: "wrap" }}>
              {(m.capability_tags || []).map((t) => (
                <span key={t} className="tag" style={{ fontSize: 10, background: "var(--surface-2)", color: "var(--fg)" }}>{t}</span>
              ))}
              {(m.task_tags || []).map((t) => (
                <span key={t} className="tag blue" style={{ fontSize: 10 }}>{t}</span>
              ))}
            </div>
            {m.official_url && <div style={{ marginTop: 6, fontSize: 11 }}><a href={m.official_url} target="_blank" rel="noreferrer">官方网站 ↗</a></div>}
          </div>
        ))}
      </div>
    </div>
  );
}
