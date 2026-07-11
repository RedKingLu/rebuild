/** DocsPage (R16-E1): community online documentation list.

 * Read-only list of Markdown docs served by community-backend /docs.
 * Sits in the independent community portal (NOT the platform's Knowledge /
 * Docs/Help) — so it does not revive any main-platform Docs/Help entry.
 */
import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { api, type DocItem, type DocListResponse } from "../api";
import { Empty } from "../components";

export function DocsPage() {
  const [data, setData] = useState<DocListResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cat, setCat] = useState<string | undefined>(undefined);

  const load = (category?: string) => {
    api.docs(category)
      .then(setData)
      .catch((e) => setErr(String(e)));
  };

  useEffect(() => { load(cat); }, [cat]);

  const categories = Array.from(new Set((data?.docs || []).map((d) => d.category).filter(Boolean))).sort();

  if (err) return <Empty>加载失败：{err}</Empty>;
  if (!data) return <div className="card"><p>正在加载文档…</p></div>;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: 16 }}>
      <aside>
        <div className="side">
          <h4 style={{ marginTop: 0 }}>分类</h4>
          <div className="tag-list">
            <span className={"tag" + (cat === undefined ? " active" : "")} style={{ cursor: "pointer" }}
              onClick={() => setCat(undefined)}>全部 ({data.total})</span>
            {categories.map((c) => (
              <span key={c} className={"tag" + (cat === c ? " active" : "")} style={{ cursor: "pointer" }}
                onClick={() => setCat(c)}>{c}</span>
            ))}
          </div>
        </div>
      </aside>
      <div>
        <h1 style={{ fontSize: 20, margin: "0 0 12px" }}>在线文档</h1>
        {data.docs.length === 0 && <Empty>暂无文档。</Empty>}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 12 }}>
          {data.docs.map((d) => (
            <div key={d.slug} className="card" style={{ padding: 14 }}>
              <div className="spread">
                <NavLink to={`/docs/${d.slug}`} style={{ fontWeight: 600 }}>{d.title}</NavLink>
                <span className="tag grey">{d.source === "official" ? "官方" : "社区"}</span>
              </div>
              <p className="card-desc" style={{ fontSize: 13 }}>{d.summary}</p>
              <div className="card-tags">{(d.tags || []).map((t) => <span key={t} className="tag">{t}</span>)}</div>
              <div className="sub" style={{ fontSize: 11 }}>{d.category} · v{d.version}</div>
            </div>
          ))}
        </div>
        <p className="sub" style={{ fontSize: 11, marginTop: 16, color: "var(--ink-3)" }}>
          文档由社区发布侧提供，属于 rebuild 社区独立内容，与主平台知识库相互独立。
        </p>
      </div>
    </div>
  );
}
