import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type ResourceDetail, type VersionListResponse } from "../api";
import { DownloadButton, Empty, Md, SourceBadge } from "../components";

// 复制导入链接按钮（R15-R16 返工 A4）：复制该资源 /download 的绝对 URL，
// 供用户粘贴回 rebuild 主平台的「资源」页导入。
function CopyImportLink({ id }: { id: string }) {
  const [copied, setCopied] = useState(false);
  // 绝对 URL：基于 VITE_COMMUNITY_BASE / 同源解析，保证粘贴到主平台后可直接下载。
  const importUrl = new URL(api.downloadUrl(id), window.location.origin).toString();
  async function copy() {
    try {
      await navigator.clipboard.writeText(importUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // 剪贴板不可用时退回选中提示，不静默失败
      window.prompt("请手动复制导入链接：", importUrl);
    }
  }
  return (
    <button className="btn btn-sm" type="button" onClick={copy} title="复制该资源的导入链接">
      {copied ? "已复制导入链接" : "复制导入链接"}
    </button>
  );
}

export function ResourceDetailPage() {
  const { id = "" } = useParams();
  const [d, setD] = useState<ResourceDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ver, setVer] = useState<VersionListResponse | null>(null);
  useEffect(() => { api.resource(id).then(setD).catch((e) => setErr(String(e))); }, [id]);
  useEffect(() => { api.versions(id).then(setVer).catch(() => setVer(null)); }, [id]);

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
        <div style={{ display: "flex", gap: 8 }}>
          <CopyImportLink id={d.id} />
          <DownloadButton id={d.id} />
        </div>
      </div>

      <div className="muted" style={{ fontSize: 13, margin: "8px 0 4px" }}>
        导入方式：① 复制导入链接 → 回到 rebuild 平台 →「资源」→ 粘贴此链接导入（导入即可用）；② 或点「下载」导出离线 zip，在主平台上传导入。
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
          {ver && ver.versions.length > 0 && (
            <div className="side">
              <h4 style={{ marginTop: 0 }}>版本历史</h4>
              {ver.versions.map((v) => {
                const isCurrent = v.version === ver.current_version;
                return (
                  <div key={v.version} style={{ marginBottom: 8, fontSize: 13 }}>
                    <div>
                      <span>v{v.version}</span>
                      {isCurrent
                        ? <span className="badge badge-official" style={{ marginLeft: 6 }}>当前</span>
                        : <span className="muted" style={{ marginLeft: 6 }}>仅供追溯</span>}
                    </div>
                    {isCurrent ? (
                      <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                        <CopyImportLink id={d.id} />
                        <DownloadButton id={d.id} />
                      </div>
                    ) : (
                      <div className="muted" style={{ marginTop: 2 }}>历史版本仅供追溯，当前仅可导入最新版。</div>
                    )}
                  </div>
                );
              })}
              {ver.note && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{ver.note}</div>}
            </div>
          )}
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
