import { useEffect, useState } from "react";
import { api, type ModelEntry } from "../api";
import { Empty } from "../components";

const PROVIDERS = ["", "deepseek-official", "maas-icompify"];
const AVAL = ["", "available"];

export function ModelsPage() {
  const [provider, setProvider] = useState("");
  const [availability, setAvailability] = useState("");
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setErr(null);
    api.models({ provider, availability }).then((d) => setModels(d.models)).catch((e) => setErr(String(e)));
  }, [provider, availability]);

  return (
    <div>
      <div className="section-title">社区模型目录</div>
      <div className="layout" style={{ marginBottom: 16, gridTemplateColumns: "200px 1fr" }}>
        <aside className="filter-col">
          <h4>供应商</h4>
          {PROVIDERS.map((p) => <label key={p}><input type="radio" name="p" checked={provider === p} onChange={() => setProvider(p)} /> {p || "全部"}</label>)}
          <h4 style={{ marginTop: 16 }}>可用状态</h4>
          {AVAL.map((a) => <label key={a}><input type="radio" name="a" checked={availability === a} onChange={() => setAvailability(a)} /> {a || "全部"}</label>)}
        </aside>
        <div className="list-meta">共 {models.length} 条 · 数据来源：社区 backend /models</div>
      </div>
      {err && <Empty>加载失败：{err}</Empty>}
      {models.length === 0 && <Empty>暂无模型数据</Empty>}
      <div className="grid">
        {models.map((m) => (
          <div key={m.model_id} className="card">
            <div className="card-head">
              <div className="card-icon fallback">{m.display_name.slice(0, 1)}</div>
              <div className="card-title">
                <div className="card-name">{m.display_name || m.model_id}</div>
                <div className="card-meta"><span className="muted">{m.provider_id}</span> · <span className="muted">{m.family}</span></div>
              </div>
            </div>
            <div className="card-desc">
              上下文窗口：{m.context_window ? m.context_window.toLocaleString() : "未登记"} · 最大输出：{m.max_output_tokens ? m.max_output_tokens.toLocaleString() : "未登记"}
            </div>
            <div className="card-desc">
              输入模态：{(m.input_modalities || []).join(", ") || "未登记"} · 输出模态：{(m.output_modalities || []).join(", ") || "未登记"}
            </div>
            <div className="card-tags">{(m.capability_tags || []).map((t) => <span key={t} className="tag">{t}</span>)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
