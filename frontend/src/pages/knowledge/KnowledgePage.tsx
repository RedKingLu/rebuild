/** KnowledgePage — 知识库（R15-4-C7：知识包 + Markdown 正文安全渲染；R16-E2：全文搜索）。
 *
 * - 知识包 zip 导入（manifest.json + 多 .md 文档 → 多个 Knowledge 资源）。
 * - 单文件上传（.md/.txt/.json/.pdf）。
 * - 搜索：R16-E2 接线真实后端 /api/knowledge/search（全文检索 name+description+正文，
 *   后端 SQLite LIKE 检索 + snippet + 按 score 排序）；前端不再做静态过滤（红线 #4）。
 * - 右侧正文详情：Markdown 安全渲染（react-markdown + remark-gfm + rehype-sanitize 防 XSS）。
 * - 按 knowledge_package 分组；SourceBadge 来源徽章。
 * - 中文优先，前后端联调。
 */
import { useState, useEffect, useCallback } from "react";
import {
  listResources, deleteResource, importResourceFile, fetchContent,
  importKnowledgePackage, searchKnowledge,
  type ResourceEntry, type KnowledgeSearchResult,
} from "../../services/resourceService";
import { Icon } from "../../components/ui/Icon";
import { SourceBadge } from "../../components/SourceBadge";
import { MarkdownView } from "../../components/MarkdownView";

export function KnowledgePage() {
  const [items, setItems] = useState<ResourceEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ResourceEntry | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  // R16-E2：搜索结果状态（有 query 时使用；空 query 时回退 items 列表分组的展示）
  const [searchResults, setSearchResults] = useState<KnowledgeSearchResult[] | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const resp = await listResources({ type: "knowledge" });
      setItems(resp.resources || []);
    } catch (e) { setError(e instanceof Error ? e.message : "加载知识库失败"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // R16-E2：有搜索词时调后端全文搜索；清空时回退列表
  useEffect(() => {
    const q = search.trim();
    if (!q) { setSearchResults(null); return; }
    let alive = true;
    setSearchLoading(true);
    searchKnowledge(q).then((d) => {
      if (alive) setSearchResults(d.results || []);
    }).catch(() => {
      if (alive) setSearchResults([]);
    }).finally(() => {
      if (alive) setSearchLoading(false);
    });
    return () => { alive = false; };
  }, [search]);

  const loadContent = useCallback(async (id: string) => {
    try {
      const c = await fetchContent(id);
      setContent(c.content || null);
    } catch { setContent(null); }
  }, []);

  useEffect(() => {
    if (selected) loadContent(selected.resource_id);
  }, [selected, loadContent]);

  // 搜索结果视图（R16-E2）：按 score 排序+snippet 展示，点击加载 Markdown 正文。
  const searching = search.trim().length > 0;

  // 非搜索视图：按 knowledge_package 分组。
  const groups: Record<string, ResourceEntry[]> = {};
  if (!searching) {
    for (const it of items) {
      const tm = (it.type_metadata || {}) as Record<string, unknown>;
      const g = (tm.knowledge_package as string) || (it.source_type === "seed" ? "平台文档" : "我的文档");
      groups[g] = groups[g] || [];
      groups[g].push(it);
    }
  }

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; if (!file) return;
    setBusy(true);
    try {
      if (file.name.endsWith(".zip")) {
        await importKnowledgePackage(file);
      } else {
        await importResourceFile(file, "knowledge");
      }
      await fetchData();
    } catch (err) { alert("导入失败：" + (err instanceof Error ? err.message : "未知错误")); }
    finally { setBusy(false); e.target.value = ""; }
  };

  const handleDelete = async (k: ResourceEntry) => {
    if (!confirm(`确认软删除「${k.name}」？`)) return;
    await deleteResource(k.resource_id);
    if (selected?.resource_id === k.resource_id) { setSelected(null); setContent(null); }
    await fetchData();
  };

  if (loading) return <div className="card"><p>正在加载知识库…</p></div>;
  if (error) return <div className="card"><p style={{ color: "var(--red)" }}>{error}</p><button className="btn sm" onClick={fetchData}>重试</button></div>;

  return (
    <div style={{ display: "flex", gap: 16, height: "calc(100vh - 120px)" }}>
      {/* 左：文档列表 */}
      <div style={{ width: 380, flexShrink: 0, display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="spread">
          <h1 style={{ margin: 0, fontSize: 18 }}>知识库</h1>
          <span className="sub" style={{ fontSize: 12 }}>{items.length} 个文档</span>
        </div>
        <div className="row" style={{ gap: 6 }}>
          <input className="inp" style={{ flex: 1 }} value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索文档标题、摘要或正文…" />
          <label className="btn sm ghost" style={{ cursor: "pointer" }}>
            <Icon name="import" size={14} />{busy ? "导入中…" : "导入知识包"}
            <input type="file" accept=".zip,.md,.txt,.json,.pdf" style={{ display: "none" }} onChange={handleUpload} disabled={busy} />
          </label>
        </div>
        {/* R16-E2：搜索提示 */}
        {searching && (
          <div className="sub" style={{ fontSize: 11, margin: "2px 0" }}>
            {searchLoading ? "搜索中…" : `后端全文检索：${searchResults?.length ?? 0} 条结果`}
          </div>
        )}
        {items.length === 0 && !searching && <div className="empty"><p className="sub">知识库为空。点击「导入知识包」导入 .zip 知识包，或上传单个 Markdown 文件。</p></div>}
        {/* R16-E2：搜索结果列表（按 score 排序，展示 snippet）*/}
        {searching && searchResults && (
          <div>
            {searchResults.length === 0 && <div className="empty"><p className="sub">未找到匹配「{search.trim()}」的文档。</p></div>}
            {searchResults.map((r) => (
              <SearchResultItem key={r.resource_id} result={r}
                active={selected?.resource_id === r.resource_id}
                onClick={() => {
                  // 搜索结果点击：构造轻量 ResourceEntry 供右侧正文加载
                  const minimal: ResourceEntry = {
                    resource_id: r.resource_id, resource_type: "knowledge", name: r.name,
                    description: r.description, version: "1.0", source_type: r.source_type,
                    source_trust_level: "", source_path_or_ref: r.body_path ?? null,
                    risk_level: "L0", status: "active", permission_scope: "",
                    capabilities: null, allowed_actions: null, blocked_actions: null,
                    type_metadata: r.body_path ? { body_path: r.body_path } : null,
                    source_status: "real", capability_status: "", enabled: true,
                    created_at: null, updated_at: null,
                  };
                  setSelected(minimal);
                }} />
            ))}
          </div>
        )}
        {!searching && Object.entries(groups).map(([g, docs]) => (
          <div key={g}>
            <div className="sub" style={{ fontSize: 11, marginBottom: 4, textTransform: "uppercase" }}>{g}（{docs.length}）</div>
            {docs.map((d) => (
              <DocItem key={d.resource_id} doc={d} active={selected?.resource_id === d.resource_id}
                onClick={() => setSelected(d)} onDelete={() => handleDelete(d)} />
            ))}
          </div>
        ))}
      </div>

      {/* 右：正文详情 */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {selected ? (
          <div className="card" style={{ padding: 20, height: "100%" }}>
            <div className="spread" style={{ marginBottom: 12 }}>
              <div>
                <h2 style={{ margin: 0, fontSize: 16 }}>{selected.name}</h2>
                <div className="card-meta" style={{ marginTop: 4, display: "flex", gap: 8, alignItems: "center" }}>
                  <SourceBadge source={selected.source_type === "seed" ? "official" : "local"} />
                  <span className="sub" style={{ fontSize: 11 }}>v{(selected as ResourceEntry).version || "1.0"} · {(selected as ResourceEntry).source_type}</span>
                </div>
              </div>
              <button className="btn sm ghost" onClick={() => handleDelete(selected)}>软删除</button>
            </div>
            <div style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
              {content ? <MarkdownView src={content} /> : (
                <div className="sub" style={{ whiteSpace: "pre-wrap" }}>
                  {selected.description || "暂无正文。"}
                  <div style={{ marginTop: 8, fontSize: 11 }}>（此文档为标题/元数据条目，无 Markdown 正文；可从原文重新导入以填充正文。）</div>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="card" style={{ padding: 40, textAlign: "center", height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
            <Icon name="knowledge" size={48} />
            <h3 style={{ margin: 12, marginBottom: 4 }}>知识库</h3>
            <p className="sub" style={{ maxWidth: 400 }}>
              左侧选择文档查看 Markdown 正文（安全渲染）。支持导入 .zip 知识包（含 manifest.json + 多 .md 文档），或上传单个 Markdown 文件。
            </p>
            <div style={{ marginTop: 16, fontSize: 12, color: "var(--ink-3)" }}>
              文档已迁移：原「文档」(Docs/Help) 入口下线，内容以知识包形式提供（R15-4）。
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function SearchResultItem({ result, active, onClick }: {
  result: KnowledgeSearchResult; active: boolean; onClick: () => void;
}) {
  return (
    <div onClick={onClick} style={{
      padding: "8px 10px", cursor: "pointer", borderRadius: 6, fontSize: 13,
      background: active ? "var(--blue-bg, var(--surface-2))" : "transparent",
      border: active ? "1px solid var(--accent-ink)" : "1px solid transparent",
      marginBottom: 2,
    }}>
      <div className="row" style={{ gap: 6, alignItems: "center" }}>
        <span style={{ flex: 1, fontWeight: active ? 700 : 400 }}>{result.name}</span>
        <span className="tag grey" style={{ fontSize: 10 }}>score {result.score.toFixed(1)}</span>
      </div>
      {result.snippet && (
        <div className="sub" style={{ fontSize: 11, marginTop: 2, maxHeight: 32, overflow: "hidden" }}>
          {result.snippet.slice(0, 180)}
        </div>
      )}
    </div>
  );
}

function DocItem({ doc, active, onClick, onDelete }: {
  doc: ResourceEntry; active: boolean; onClick: () => void; onDelete?: () => void;
}) {
  return (
    <div onClick={onClick} style={{
      padding: "8px 10px", cursor: "pointer", borderRadius: 6, fontSize: 13,
      background: active ? "var(--blue-bg, var(--surface-2))" : "transparent",
      border: active ? "1px solid var(--accent-ink)" : "1px solid transparent",
      marginBottom: 2,
    }}>
      <div className="row" style={{ gap: 6, alignItems: "center" }}>
        <span style={{ flex: 1, fontWeight: active ? 700 : 400 }}>{doc.name}</span>
        {doc.source_type !== "seed" && onDelete && (
          <button className="btn sm ghost" style={{ fontSize: 10, padding: "0 4px" }}
            onClick={(e) => { e.stopPropagation(); onDelete(); }}>✕</button>
        )}
      </div>
      <div className="sub" style={{ fontSize: 11, marginTop: 1 }}>
        {doc.description?.slice(0, 60) || "暂无摘要"}
      </div>
    </div>
  );
}
