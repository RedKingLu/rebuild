/** KnowledgePage — 知识库（Phase 12 重设计：文档中心）。
 *
 * V26 风格：文档列表 + 内容查看面板。左侧文档树，右侧内容预览。
 * 平台自带文档 + 用户上传资料。支持全文查看、搜索、分页。
 * 中文优先，前后端联调。
 */
import { useState, useEffect, useCallback } from 'react';
import { listResources, deleteResource, importResourceFile, type ResourceEntry } from '../../services/resourceService';
import { Icon } from '../../components/ui/Icon';

export function KnowledgePage() {
  const [items, setItems] = useState<ResourceEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ResourceEntry | null>(null);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 10;

  const fetchData = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const resp = await listResources({ type: 'knowledge' });
      setItems(resp.resources || []);
    } catch (e) { setError(e instanceof Error ? e.message : '加载知识库失败'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const filtered = search
    ? items.filter(i => i.name.includes(search) || (i.description || '').includes(search))
    : items;
  const totalPages = Math.ceil(filtered.length / PAGE_SIZE) || 1;

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; if (!file) return;
    try { await importResourceFile(file, 'knowledge'); fetchData(); }
    catch (err) { alert('上传失败：' + (err instanceof Error ? err.message : '未知错误')); }
    e.target.value = '';
  };

  const handleDelete = async (k: ResourceEntry) => {
    if (!confirm(`确认删除「${k.name}」？`)) return;
    await deleteResource(k.resource_id);
    if (selected?.resource_id === k.resource_id) setSelected(null);
    fetchData();
  };

  const platformDocs = items.filter(i => i.source_type === 'seed');
  const userDocs = items.filter(i => i.source_type !== 'seed');

  if (loading) return <div className="card"><p>正在加载知识库…</p></div>;
  if (error) return <div className="card"><p className="err">{error}</p><button className="btn sm" onClick={fetchData}>重试</button></div>;

  return (
    <div style={{ display: 'flex', gap: 16, height: 'calc(100vh - 120px)' }}>
      {/* 左侧：文档列表 */}
      <div style={{ width: 380, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div className="spread">
          <h1 style={{ margin: 0, fontSize: 18 }}>知识库</h1>
          <span className="sub" style={{ fontSize: 12 }}>{items.length} 个文档</span>
        </div>

        {/* 搜索 + 上传 */}
        <div className="row" style={{ gap: 6 }}>
          <input className="inp" style={{ flex: 1 }} value={search} onChange={e => { setSearch(e.target.value); setPage(0); }}
            placeholder="搜索文档…" />
          <label className="btn sm ghost" style={{ cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <Icon name="import" size={14} />上传
            <input type="file" accept=".json,.md,.txt,.pdf" style={{ display: 'none' }} onChange={handleUpload} />
          </label>
        </div>

        {/* 平台文档区 */}
        {platformDocs.length > 0 && (
          <div>
            <div className="sub" style={{ fontSize: 11, marginBottom: 4, textTransform: 'uppercase' }}>平台文档（{platformDocs.length}）</div>
            {platformDocs.map(d => (
              <DocItem key={d.resource_id} doc={d} active={selected?.resource_id === d.resource_id}
                onClick={() => setSelected(d)} />
            ))}
          </div>
        )}

        {/* 用户文档区 */}
        {userDocs.length > 0 && (
          <div>
            <div className="sub" style={{ fontSize: 11, marginBottom: 4, marginTop: 8, textTransform: 'uppercase' }}>我的文档（{userDocs.length}）</div>
            {userDocs.map(d => (
              <DocItem key={d.resource_id} doc={d} active={selected?.resource_id === d.resource_id}
                onClick={() => setSelected(d)} onDelete={() => handleDelete(d)} />
            ))}
          </div>
        )}

        {items.length === 0 && <div className="empty"><p className="sub">知识库为空。上传 Markdown/JSON/文本 文件开始。</p></div>}

        {/* 分页 */}
        {filtered.length > PAGE_SIZE && (
          <div className="row" style={{ justifyContent: 'center', gap: 6, marginTop: 'auto' }}>
            <button className="btn sm ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>◀</button>
            <span className="sub" style={{ fontSize: 11 }}>{page + 1} / {totalPages}</span>
            <button className="btn sm ghost" disabled={(page + 1) * PAGE_SIZE >= filtered.length} onClick={() => setPage(page + 1)}>▶</button>
          </div>
        )}
      </div>

      {/* 右侧：内容查看 */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {selected ? (
          <div className="card" style={{ padding: 20, height: '100%' }}>
            <div className="spread" style={{ marginBottom: 12 }}>
              <div>
                <h2 style={{ margin: 0, fontSize: 16 }}>{selected.name}</h2>
                <div className="sub" style={{ fontSize: 11, marginTop: 4 }}>
                  版本 {selected.version || '1.0'} · 来源 {selected.source_type || 'local'} · 风险 {selected.risk_level || 'L1'} · {selected.created_at ? new Date(selected.created_at).toLocaleDateString('zh-CN') : ''}
                </div>
              </div>
              <div className="row" style={{ gap: 6 }}>
                <span className="tag" style={{ fontSize: 11, background: selected.status === 'active' ? 'var(--green)' : 'var(--gray)', color: '#fff' }}>
                  {selected.status === 'active' ? '活跃' : selected.status}
                </span>
                {selected.source_type !== 'seed' && (
                  <button className="btn sm ghost" onClick={() => handleDelete(selected)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    <Icon name="delete" size={13} />删除
                  </button>
                )}
              </div>
            </div>
            <div style={{ borderTop: '1px solid var(--line)', paddingTop: 12 }}>
              {selected.description ? (
                <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.8, fontSize: 14 }}>{selected.description}</div>
              ) : (
                <div className="sub">暂无内容。此文档可能仅包含元数据，正文待补充。</div>
              )}
            </div>
            {selected.source_path_or_ref && (
              <div className="sub" style={{ marginTop: 16, fontSize: 11, borderTop: '1px solid var(--line)', paddingTop: 8 }}>
                来源引用：<code>{selected.source_path_or_ref}</code>
              </div>
            )}
          </div>
        ) : (
          <div className="card" style={{ padding: 40, textAlign: 'center', height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ fontSize: 48, marginBottom: 16, opacity: 0.3 }}>📚</div>
            <h3 style={{ margin: 0 }}>知识库</h3>
            <p className="sub" style={{ maxWidth: 400, marginTop: 8 }}>
              左侧选择文档查看内容。支持 Markdown、纯文本、JSON 格式。
              平台自带迁移指南、规范文档；您也可以上传自己的资料。
            </p>
            <div className="statgrid" style={{ marginTop: 20, width: '100%', maxWidth: 500 }}>
              <div className="card statcard"><b>平台文档</b><div className="snum">{platformDocs.length}</div></div>
              <div className="card statcard"><b>我的文档</b><div className="snum">{userDocs.length}</div></div>
              <div className="card statcard"><b>总计</b><div className="snum">{items.length}</div></div>
              <div className="card statcard"><b>状态</b><div className="snum" style={{ fontSize: 15 }}>可用</div><div className="slabel">SQLite 存储</div></div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function DocItem({ doc, active, onClick, onDelete }: {
  doc: ResourceEntry; active: boolean; onClick: () => void; onDelete?: () => void;
}) {
  return (
    <div onClick={onClick} style={{
      padding: '8px 10px', cursor: 'pointer', borderRadius: 6, fontSize: 13,
      background: active ? 'var(--blue-bg, var(--surface-2))' : 'transparent',
      border: active ? '1px solid var(--accent-ink)' : '1px solid transparent',
      marginBottom: 2, transition: 'background 0.1s',
    }}>
      <div className="row" style={{ gap: 6, alignItems: 'center' }}>
        <span style={{ flex: 1, fontWeight: active ? 700 : 400 }}>{doc.name}</span>
        {onDelete && (
          <button className="btn sm ghost" style={{ fontSize: 10, padding: '0 4px' }}
            onClick={e => { e.stopPropagation(); onDelete(); }}>✕</button>
        )}
      </div>
      <div className="sub" style={{ fontSize: 11, marginTop: 1 }}>
        {doc.description?.slice(0, 60) || '暂无摘要'} · {doc.version || '1.0'}
      </div>
    </div>
  );
}
