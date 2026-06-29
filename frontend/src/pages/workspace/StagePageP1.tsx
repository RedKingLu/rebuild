/** StagePageP1 — R9-3G-C: P1 full-stack profiling stage page with 4 card areas.
 *  Data from GET /api/projects/{id}/profiling-summary.
 *  R9-5-8 T2/T3: identification items + Evidence Gaps come from the backend
 *  (PROFILING_ITEMS single source + uncertainty_manifest), not a hardcoded list.
 */
import { useState, useEffect } from 'react';

interface Props {
  projectId: string;
  stageStatus?: string;
  onReExecute?: () => void;
}

export function StagePageP1({ projectId, stageStatus, onReExecute }: Props) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedItem, setExpandedItem] = useState<string | null>(null);
  const [reProfiling, setReProfiling] = useState(false);

  const isChangesRequested = stageStatus === 'changes_requested';
  const isBlocked = stageStatus === 'blocked';

  useEffect(() => {
    setLoading(true);
    fetch(`/api/projects/${projectId}/profiling-summary`)
      .then(r => r.json())
      .then(d => {
        setData(d?.data || d);
        setLoading(false);
      })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [projectId]);

  const handleReExecute = async () => {
    setReProfiling(true);
    try {
      await fetch(`/api/projects/${projectId}/profile`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      // Reload data
      const resp = await fetch(`/api/projects/${projectId}/profiling-summary`);
      const d = await resp.json();
      setData(d?.data || d);
      if (onReExecute) onReExecute();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setReProfiling(false);
    }
  };

  if (loading) return <div style={{ padding: 12, fontSize: 13, color: 'var(--color-text-muted)' }}>加载 P1 识别结果…</div>;
  if (error) return <div style={{ padding: 12, fontSize: 13, color: 'var(--red)' }}>加载失败: {error}</div>;

  const available = data?.available !== false;
  const summary = data?.summary || '';
  const artifacts: string[] = data?.artifacts || [];
  // R9-5-8 T2: authoritative identification items from the backend (single source)
  const items: { key: string; label: string; exists: boolean }[] = data?.items || [];
  // R9-5-8 T3: Evidence Gaps source = uncertainty_manifest content (not inferred)
  const uncertainty = data?.uncertainty || null;
  const completedCount = items.filter(i => i.exists).length;

  // Extract tech stack info from summary markdown
  const techLines = summary.split('\n').filter((l: string) =>
    l.includes('语言') || l.includes('框架') || l.includes('构建') || l.includes('Language') || l.includes('Framework') || l.includes('Build')
  );

  return (
    <div style={{ fontSize: 13 }}>
      <h3 style={{ marginBottom: 14 }}>P1 建档 — 全量识别</h3>

      {/* R9-3G-D: Rework / Blocked status banners */}
      {isChangesRequested && (
        <div style={{
          padding: '10px 14px', marginBottom: 12, background: 'var(--orange-soft, #fff3e0)',
          border: '1px solid var(--orange, #e67e22)', borderRadius: 6, fontSize: 12,
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span style={{ flex: 1 }}>阶段需要返工。请重新执行 P1 全量识别。</span>
          <button className="btn sm" style={{ background: 'var(--orange, #e67e22)', color: '#fff' }}
            onClick={handleReExecute} disabled={reProfiling}>
            {reProfiling ? '识别中…' : '重新执行'}
          </button>
        </div>
      )}
      {isBlocked && (
        <div style={{
          padding: '10px 14px', marginBottom: 12, background: 'var(--red-soft, #ffebee)',
          border: '1px solid var(--red)', borderRadius: 6, fontSize: 12, color: 'var(--red)',
        }}>
          阶段已被拒绝。请查看 Gate 决策原因，联系管理员或重新执行。
        </div>
      )}

      {!available && (
        <div style={{ padding: 12, background: 'var(--color-surface-subtle)', borderRadius: 6, marginBottom: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>
          全量识别尚未执行。请先完成 P0 并批准 Gate 以触发 P1 profiling。
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}>

        {/* Card 1: 技术栈 */}
        <div style={{ padding: 14, background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10 }}>技术栈</div>
          {techLines.length > 0 ? (
            <div style={{ fontSize: 12, lineHeight: 1.8 }}>
              {techLines.map((l: string, i: number) => (
                <div key={i} style={{
                  display: 'inline-block', padding: '2px 8px', margin: '2px 4px 2px 0',
                  background: 'var(--color-primary-soft)', borderRadius: 12, fontSize: 11,
                  color: 'var(--color-primary)',
                }}>{l.replace(/^[-*#]+\s*/, '')}</div>
              ))}
            </div>
          ) : (
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
              {available ? '从 profiling_summary 中解析技术栈信息...' : '暂无数据'}
            </div>
          )}
          {/* Show raw summary if available */}
          {available && summary && (
            <details style={{ marginTop: 8 }}>
              <summary style={{ fontSize: 11, cursor: 'pointer', color: 'var(--color-text-muted)' }}>完整摘要</summary>
              <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto', marginTop: 4, padding: 8, background: 'var(--color-surface-subtle)', borderRadius: 4 }}>
                {summary.slice(0, 2000)}
              </pre>
            </details>
          )}
        </div>

        {/* Card 2: 识别项（后端权威清单，单一来源） */}
        <div style={{ padding: 14, background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10 }}>识别项（{items.length}）</div>
          <div style={{ fontSize: 12 }}>
            {items.map((item, i) => (
              <div key={item.key}
                onClick={() => setExpandedItem(expandedItem === item.key ? null : item.key)}
                style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '4px 8px', marginBottom: 2, borderRadius: 4, cursor: 'pointer',
                  background: expandedItem === item.key ? 'var(--color-primary-soft)' : 'transparent',
                  fontSize: 11,
                }}>
                <span>{i + 1}. {item.label}</span>
                <span style={{
                  color: item.exists ? 'var(--green)' : 'var(--color-text-muted)',
                  fontWeight: item.exists ? 600 : 400,
                }}>
                  {item.exists ? '✅' : '待识别'}
                </span>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 8 }}>
            完成: {completedCount} / {items.length}
          </div>
        </div>

        {/* Card 3: 产物列表 */}
        <div style={{ padding: 14, background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10 }}>产物列表</div>
          {artifacts.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>暂无产物</div>
          ) : (
            <div style={{ fontSize: 11, maxHeight: 240, overflow: 'auto' }}>
              {artifacts.map((a: string, i: number) => (
                <div key={i} style={{ padding: '3px 0', fontFamily: 'monospace', borderBottom: '1px solid var(--color-border)' }}>
                  artifacts/{a}
                </div>
              ))}
            </div>
          )}
          {available && <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 8 }}>共 {artifacts.length} 个产物文件</div>}
        </div>

        {/* Card 4: Evidence Gaps（读 uncertainty_manifest，非前端推断） */}
        <div style={{ padding: 14, background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10 }}>Evidence Gaps</div>
          {(() => {
            const gaps: any[] = Array.isArray(uncertainty) ? uncertainty
              : (uncertainty?.gaps || uncertainty?.items || uncertainty?.uncertainties || []);
            if (!uncertainty) {
              return <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>识别未执行或无 uncertainty_manifest，无 Gap 数据。</div>;
            }
            if (gaps.length === 0) {
              return <div style={{ fontSize: 12, color: 'var(--green)' }}>✅ 无登记的不确定项（uncertainty_manifest 为空）。</div>;
            }
            return (
              <div style={{ fontSize: 11, maxHeight: 220, overflow: 'auto' }}>
                {gaps.map((g: any, i: number) => (
                  <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid var(--color-border)' }}>
                    ⚠ {typeof g === 'string' ? g : (g.detail || g.type || g.description || JSON.stringify(g))}
                  </div>
                ))}
              </div>
            );
          })()}
        </div>
      </div>
    </div>
  );
}
