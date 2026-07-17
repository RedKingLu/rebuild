/** StagePageP0 — R9-3G-B: P0 stage detail page with 4 card areas.
 *  Data from workspace aggregate: project, active_run, traces, audits, file_index.
 */
import { useState, useEffect } from 'react';

interface Props {
  projectId: string;
  project: any;
  run: any;
  traces: any[];
  audits: any[];
  fileIndex: any[];
  /** Callback to re-execute P0 (re-open onboarding wizard) */
  onReExecute?: () => void;
}

export function StagePageP0({ projectId, project, run, traces, audits, fileIndex, onReExecute }: Props) {
  const [sourceIndex, setSourceIndex] = useState<any>(null);
  // R9-5-8 T4: core artifacts + REAL existence from the backend (not hardcoded)
  const [coreArtifacts, setCoreArtifacts] = useState<{ name: string; label: string; exists: boolean }[]>([]);

  const stageStatus = run?.stage_status?.p0 || 'pending';
  const isChangesRequested = stageStatus === 'changes_requested';
  const isBlocked = stageStatus === 'blocked';

  useEffect(() => {
    fetch(`/api/projects/${projectId}/source-index`)
      .then(r => r.json())
      .then(d => setSourceIndex(d?.data || d))
      .catch(() => {});
    fetch(`/api/projects/${projectId}/stage-artifacts/p0`)
      .then(r => r.json())
      .then(d => setCoreArtifacts((d?.data || d)?.artifacts || []))
      .catch(() => {});
  }, [projectId]);

  // Count files from file_index
  const fileCount = fileIndex?.reduce?.((acc: number, r: any) => acc + (r.children?.length || 0), 0) || 0;
  const evidenceCount = project?.evidence_gap_count || 0;

  return (
    <div style={{ fontSize: 13 }}>
      <h3 style={{ marginBottom: 14 }}>P0 接入</h3>

      {/* R9-3G-D: Rework / Blocked status banners */}
      {isChangesRequested && (
        <div style={{
          padding: '10px 14px', marginBottom: 12, background: 'var(--orange-soft, #fff3e0)',
          border: '1px solid var(--orange, #e67e22)', borderRadius: 6, fontSize: 12,
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span style={{ flex: 1 }}>阶段需要返工。请重新执行 P0 接入流程。</span>
          {onReExecute && (
            <button className="btn sm" style={{ background: 'var(--orange, #e67e22)', color: '#fff' }}
              onClick={onReExecute}>重新执行</button>
          )}
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

      {/* Card grid: 2x2 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}>

        {/* Card 1: 接入状态 */}
        <div style={{ padding: 14, background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10 }}>接入状态</div>
          <div style={{ fontSize: 12, color: 'var(--color-text-muted)', lineHeight: 1.8 }}>
            <div>引导完成: <span style={{ color: project?.onboarding_done ? 'var(--green)' : 'var(--amber)', fontWeight: 600 }}>
              {project?.onboarding_done ? '是' : '否'}
            </span></div>
            <div>工作区状态: {project?.workspace_status || '—'}</div>
            <div>来源类型: {project?.source_type || '—'}</div>
            <div>编码 Agent: {project?.coding_agent_ref || '平台默认'}</div>
            {run && (
              <>
                <div style={{ marginTop: 6, borderTop: '1px solid var(--color-border)', paddingTop: 6 }}>
                  <div>Run: {run.run_id}</div>
                  <div>Run 状态: {run.run_status}</div>
                  <div>阶段状态: {run.stage_status?.p0 || 'pending'}</div>
                </div>
              </>
            )}
          </div>
        </div>

        {/* Card 2: 源码导入摘要 */}
        <div style={{ padding: 14, background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10 }}>源码导入</div>
          <div style={{ fontSize: 12, color: 'var(--color-text-muted)', lineHeight: 1.8 }}>
            <div>来源类型: {project?.source_type || '—'}</div>
            <div>文件数: {fileCount}</div>
            {sourceIndex && (
              <>
                <div>索引状态: {sourceIndex.materialization_status || '—'}</div>
                {(sourceIndex.directory_count != null) && <div>目录数: {sourceIndex.directory_count}</div>}
                {Array.isArray(sourceIndex.key_files) && sourceIndex.key_files.length > 0 &&
                  <div>关键文件: {sourceIndex.key_files.length} 个</div>}
              </>
            )}
            {!sourceIndex && <div style={{ color: 'var(--color-text-muted)', fontStyle: 'italic' }}>source_index 尚未生成（需要完成 materialize）</div>}
          </div>
        </div>

        {/* Card 3: 产物 */}
        <div style={{ padding: 14, background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10 }}>产物</div>
          <div style={{ fontSize: 12, color: 'var(--color-text-muted)', lineHeight: 1.8 }}>
            <div>Evidence 候选数: {evidenceCount}</div>
            <div style={{ marginTop: 6 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>核心产物:</div>
              {coreArtifacts.length === 0 ? (
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>加载中…</div>
              ) : coreArtifacts.map(a => (
                <div key={a.name} style={{ fontSize: 11, fontFamily: 'monospace', marginBottom: 2,
                  display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                  <span>artifacts/{a.name}</span>
                  <span style={{ color: a.exists ? 'var(--green)' : 'var(--color-text-muted)' }}>
                    {a.exists ? '✅' : '未生成'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Card 4: 最近活动 */}
        <div style={{ padding: 14, background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10 }}>最近活动</div>
          <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
            <div style={{ marginBottom: 8 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>Trace ({traces?.length || 0})</div>
              {(traces || []).slice(0, 5).map((t: any, i: number) => (
                <div key={i} style={{ fontSize: 11, marginBottom: 2, display: 'flex', justifyContent: 'space-between' }}>
                  <span>{t.trace_type || t.action}</span>
                  <span style={{ color: 'var(--color-text-muted)' }}>{t.created_at?.slice?.(11, 19) || ''}</span>
                </div>
              ))}
              {(!traces || traces.length === 0) && <div style={{ fontSize: 11 }}>暂无</div>}
            </div>
            <div>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>Audit ({audits?.length || 0})</div>
              {(audits || []).slice(0, 5).map((a: any, i: number) => (
                <div key={i} style={{ fontSize: 11, marginBottom: 2, display: 'flex', justifyContent: 'space-between' }}>
                  <span>{a.audit_type || a.action}</span>
                  <span style={{ color: 'var(--color-text-muted)' }}>{a.decision}</span>
                </div>
              ))}
              {(!audits || audits.length === 0) && <div style={{ fontSize: 11 }}>暂无</div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
