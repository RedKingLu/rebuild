/** CasesPage — 案例库（Phase 12 真实化）。
 *
 * 内容来源：原资源页面的案例 tab，现迁移至主界面案例页面。
 * 操作：导入（本地/社区）、删除。Case 不具备执行权（read_only）。
 * 中文优先，前后端联调。
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { listResources, deleteResource, importResourceFile, type ResourceEntry } from '../../services/resourceService';
import { Icon } from '../../components/ui/Icon';

export function CasesPage() {
  const [cases, setCases] = useState<ResourceEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const PAGE_SIZE = 10;
  const fileRef = useRef<HTMLInputElement>(null);

  const fetchCases = useCallback(async (pg = 0) => {
    setLoading(true); setError(null);
    try {
      const resp = await listResources({ type: 'case' });
      const all = resp.resources || [];
      setTotal(all.length);
      setCases(all.slice(pg * PAGE_SIZE, (pg + 1) * PAGE_SIZE));
      setPage(pg);
    } catch (e) { setError(e instanceof Error ? e.message : '加载案例失败'); }
    finally { setLoading(false); }
  }, []);

  const fetchPage = (pg: number) => fetchCases(pg);

  useEffect(() => { fetchCases(); }, [fetchCases]);

  const handleDelete = async (c: ResourceEntry) => {
    if (!confirm(`确认删除案例「${c.name}」？`)) return;
    await deleteResource(c.resource_id);
    fetchCases();
  };

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; if (!file) return;
    try { await importResourceFile(file, 'case'); fetchCases(); }
    catch (err) { alert('导入失败：' + (err instanceof Error ? err.message : '未知错误')); }
    e.target.value = '';
  };

  if (loading) return <div className="card"><p>正在加载案例…</p></div>;
  if (error) return <div className="card"><p className="err">错误：{error}</p><button className="btn sm" onClick={() => fetchCases()}>重试</button></div>;

  return (
    <div>
      <div className="spread">
        <h1>案例库</h1>
        <span className="sub" style={{ fontSize: 13 }}>迁移案例参考 · 只读不执行 · 数据来源：<code>/api/resources?type=case</code></span>
      </div>
      <div className="row" style={{ marginTop: 14, marginBottom: 10, alignItems: 'center', gap: 8 }}>
        <b style={{ fontSize: 14 }}>案例列表（{cases.length} 个）</b>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          <button className="btn sm ghost" onClick={() => fileRef.current?.click()} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="import" size={14} />导入案例（本地）</button>
          <button className="btn sm ghost" onClick={() => fetchCases()} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="refresh" size={14} />刷新</button>
        </span>
      </div>
      <input ref={fileRef} type="file" accept=".json" style={{ display: 'none' }} onChange={handleImportFile} />

      <div className="grid" style={{ gap: 10 }}>
        {cases.map(c => (
          <div key={c.resource_id} className="card" style={{ padding: 14 }}>
            <div className="spread">
              <div className="row" style={{ gap: 8 }}>
                <b>{c.name}</b>
                <span className="tag" style={{ fontSize: 11, background: 'var(--blue)', color: '#fff' }}>案例</span>
                <span className="tag" style={{ fontSize: 11, background: 'var(--amber)', color: '#fff' }}>只读</span>
                <span className="tag" style={{ fontSize: 11 }}>{c.risk_level || 'L1'}</span>
              </div>
              <span className="tag" style={{ fontSize: 11 }}>{c.status}</span>
            </div>
            <div className="sub" style={{ fontSize: 12, marginTop: 4 }}>{c.description?.slice(0, 200) || '暂无描述'}</div>
            {c.source_path_or_ref && <div className="sub" style={{ fontSize: 11, marginTop: 2 }}>来源：{c.source_path_or_ref}</div>}
            <div className="row" style={{ marginTop: 10, gap: 6 }}>
              <button className="btn sm ghost" onClick={() => handleDelete(c)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="delete" size={13} />删除</button>
              <span className="sub" style={{ fontSize: 11, marginLeft: 6 }}>案例不可执行（仅参考）</span>
            </div>
          </div>
        ))}
        {cases.length === 0 && <div className="empty"><p className="sub">暂无案例。点击「导入案例」上传 JSON 文件。</p></div>}
        {total > PAGE_SIZE && (
          <div className="row" style={{ justifyContent: 'center', gap: 8, marginTop: 12, alignItems: 'center' }}>
            <button className="btn sm ghost" disabled={page === 0} onClick={() => fetchPage(page - 1)}>◀ 上一页</button>
            <span className="sub" style={{ fontSize: 12 }}>第 {page + 1} 页 / 共 {Math.ceil(total / PAGE_SIZE)} 页（{total} 条）</span>
            <button className="btn sm ghost" disabled={(page + 1) * PAGE_SIZE >= total} onClick={() => fetchPage(page + 1)}>下一页 ▶</button>
          </div>
        )}
      </div>
    </div>
  );
}
