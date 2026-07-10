import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type ResourceDetail } from "../api";
import { DownloadButton, Empty, Md, SourceBadge } from "../components";

export function ResourceDetailPage() {
  const { id = "" } = useParams();
  const [d, setD] = useState<ResourceDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.resource(id).then(setD).catch((e) => setErr(String(e))); }, [id]);

  if (err) return <Empty>加载失败：{err}</Empty>;
  if (!d) return null;
  return (
    <div>
      <div className="detail-head">
        <div className="card-icon fallback" style={{ width: 56, height: 56, fontSize: 26 }}>{(d.display_name || d.name).slice(0, 1)}</div>
        <div style={{ flex: 1 }}>
          <h1>{d.display_name || d.name}</h1>
          <div className="card-meta">
            <SourceBadge source={d.source} verified={d.verified} />
            <span className="muted">v{d.version}</span> · <span className="muted">{d.resource_type}</span> · <span className="muted">↓{d.download_count}</span>
            {d.publisher && <span className="muted">· {d.publisher}</span>}
          </div>
        </div>
        <DownloadButton id={d.id} />
      </div>

      <div className="tabs"><div className="tab active">说明 / README</div></div>

      <div className="layout" style={{ gridTemplateColumns: "1fr 280px" }}>
        <div className="card"><Md src={d.readme || "_（暂无 README）_"} /></div>
        <div>
          <div className="side">
            <dl>
              <dt>ID</dt><dd className="mono">{d.id}</dd>
              <dt>版本</dt><dd>{d.version}</dd>
              <dt>标签</dt><dd>{(d.tags || []).map((t) => <span key={t} className="tag">{t}</span>)}</dd>
              <dt>分类</dt><dd>{(d.categories || []).join(", ") || "未分类"}</dd>
              <dt>许可证</dt><dd>{d.license || "未登记"}</dd>
              <dt>SHA-256</dt><dd className="mono" style={{ fontSize: 11, wordBreak: "break-all" }}>{d.checksum_sha256 || "—"}</dd>
            </dl>
          </div>
          {(d.files || []).length > 0 && (
            <div className="side">
              <h4 style={{ marginTop: 0 }}>文件</h4>
              {(d.files || []).map((f) => <div key={f.name} className="muted" style={{ fontSize: 13 }}>{f.name} · {f.size}B</div>)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
