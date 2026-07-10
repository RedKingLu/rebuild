import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type NewsItem, type ResourceCard, type StatusResponse } from "../api";

export function HomePage() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [latest, setLatest] = useState<ResourceCard[]>([]);
  useEffect(() => {
    api.status().then(setStatus).catch(() => {});
    api.news().then((d) => setNews(d.news)).catch(() => {});
    api.resources({ size: "6" }).then((d) => setLatest(d.resources)).catch(() => {});
  }, []);

  const cats = [
    { to: "/resources?type=case", label: "案例" },
    { to: "/resources?type=tool", label: "工具" },
    { to: "/resources?type=template", label: "模板" },
    { to: "/resources?type=knowledge", label: "知识" },
    { to: "/resources?type=skill", label: "Skill" },
  ];

  return (
    <div>
      <section className="hero">
        <h1>信创迁移资源社区</h1>
        <p>本地可运行的 rebuild 社区骨架（R15），资源检索、详情、下载、模型资料库与评测展示均来自真实社区服务 API。</p>
        <div className="hero-cta">
          <Link to="/resources">浏览资源</Link>
          <Link to="/models" style={{ background: "rgba(255,255,255,.15)", color: "#fff" }}>查看模型</Link>
        </div>
      </section>

      <div className="grid" style={{ marginBottom: 24 }}>
        {cats.map((c) => <Link key={c.to} to={c.to} className="card" style={{ textAlign: "center" }}><div style={{ fontSize: 22 }}>·</div><div>{c.label}</div></Link>)}
      </div>

      <div className="section-title">最新资源</div>
      <div className="grid">
        {latest.map((c) => (
          <Link key={c.id} to={`/resources/${c.id}`} className="card">
            <div className="card-head">
              <div className="card-icon fallback">{(c.display_name || c.name).slice(0, 1)}</div>
              <div className="card-title">
                <div className="card-name">{c.display_name || c.name}</div>
                <div className="card-meta"><span className={"badge " + (c.source === "official" ? "badge-official" : "badge-community")}>{c.source === "official" ? "官方" : "社区"}</span> <span className="muted">↓{c.download_count}</span></div>
              </div>
            </div>
          </Link>
        ))}
      </div>

      {news.length > 0 && (
        <>
          <div className="section-title">社区公告</div>
          {news.map((n) => (
            <div key={n.id} className="news">
              <h4>{n.title}</h4>
              <div className="muted">{n.summary}</div>
            </div>
          ))}
        </>
      )}

      {status && <div className="list-meta" style={{ marginTop: 24 }}>服务版本：{status.version} · {status.resource_count} 资源 · {status.model_count} 模型</div>}
    </div>
  );
}
