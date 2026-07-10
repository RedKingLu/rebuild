import { useEffect, useState } from "react";
import { api, type ResourceCard, type ResourceListResponse } from "../api";
import { Card, Empty } from "../components";

const TYPES = ["", "case", "tool", "template", "knowledge", "skill"];
const SOURCES = ["", "official", "community"];

export function ResourcesPage() {
  const [type, setType] = useState("");
  const [source, setSource] = useState("");
  const [data, setData] = useState<ResourceListResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  function load() {
    setErr(null);
    api.resources({ type: type || "", source: source || "" }).then(setData).catch((e) => setErr(String(e)));
  }
  useEffect(load, [type, source]);

  return (
    <div className="layout">
      <aside className="filter-col">
        <h4>类型</h4>
        {TYPES.map((t) => <label key={t}><input type="radio" name="t" checked={type === t} onChange={() => setType(t)} /> {t || "全部"}</label>)}
        <h4 style={{ marginTop: 16 }}>来源</h4>
        {SOURCES.map((s) => <label key={s}><input type="radio" name="s" checked={source === s} onChange={() => setSource(s)} /> {s || "全部"}</label>)}
      </aside>
      <div>
        <div className="section-title">资源列表</div>
        <div className="list-meta">共 {data?.totalSize ?? 0} 条 · 来自社区 backend 真实 API</div>
        {err && <Empty>加载失败：{err}</Empty>}
        {data && data.resources.length === 0 && <Empty>暂无资源</Empty>}
        <div className="grid">{(data?.resources ?? []).map((c) => <Card key={c.id} c={c} />)}</div>
      </div>
    </div>
  );
}
