/** DocDetailPage (R16-E1): community online documentation detail.

 * Renders trusted release-side Markdown seed via the lightweight Md component
 * (with simple HTML escaping). Content is 发布侧 seed only — for untrusted
 * markdown, plug DOMPurify at the Md boundary.
 */
import { useEffect, useState } from "react";
import { useParams, NavLink } from "react-router-dom";
import { api, type DocDetail } from "../api";
import { Empty, Md } from "../components";

export function DocDetailPage() {
  const { slug = "" } = useParams();
  const [doc, setDoc] = useState<DocDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.doc(slug).then(setDoc).catch((e) => setErr(String(e)));
  }, [slug]);

  if (err) return <Empty>加载失败：{err}</Empty>;
  if (!doc) return <div className="card"><p>正在加载文档…</p></div>;

  return (
    <div style={{ maxWidth: 820, margin: "0 auto" }}>
      <div style={{ marginBottom: 12 }}>
        <NavLink to="/docs" className="sub" style={{ fontSize: 12 }}>← 返回文档列表</NavLink>
      </div>
      <div className="card" style={{ padding: 24 }}>
        <div className="spread" style={{ alignItems: "flex-start" }}>
          <div style={{ flex: 1 }}>
            <h1 style={{ margin: 0, fontSize: 22 }}>{doc.title}</h1>
            <div className="card-meta" style={{ marginTop: 6 }}>
              <span className="tag">{doc.category || "未分类"}</span>
              <span className="tag">{doc.source === "official" ? "官方" : "社区"}</span>
              <span className="muted" style={{ fontSize: 12 }}>v{doc.version}</span>
            </div>
          </div>
        </div>
        {doc.summary && <p className="sub" style={{ fontStyle: "italic", marginTop: 12 }}>{doc.summary}</p>}
        <div style={{ borderTop: "1px solid var(--line)", marginTop: 16, paddingTop: 16 }}>
          <Md src={doc.body_markdown || "_（暂无正文）_"} />
        </div>
        <div className="card-tags" style={{ marginTop: 20 }}>
          {(doc.tags || []).map((t) => <span key={t} className="tag">{t}</span>)}
        </div>
      </div>
      <p className="sub" style={{ fontSize: 11, marginTop: 12, color: "var(--ink-3)" }}>
        本文档属 rebuild 社区独立内容，与主平台知识库相互独立。
      </p>
    </div>
  );
}
