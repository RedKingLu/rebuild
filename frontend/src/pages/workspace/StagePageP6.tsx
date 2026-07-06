/** StagePageP6 — R12-3-C10: P6 交付阶段页，展示交付包 + 报告 + 索引 + 下载入口 + P6 最终 Gate。
 *  数据来自真实后端：
 *   - GET /api/projects/{id}/runs/{run_id}/p6/package → DeliveryPackage 完整 JSON
 *   - GET /api/projects/{id}/gates/active + GatePanel → P6 最终 Gate
 *   - GET /api/projects/{id}/runs/{run_id}/p6/download?path=... → 下载单个文件
 *
 *  硬要求：中文优先 / Icon.tsx 线性图标 / 真实数据不挂 mock 横幅 / source 不可下载 */
import { useState, useEffect } from 'react';
import { Icon } from '../../components/ui/Icon';

interface Props {
  projectId: string;
  runId?: string;
  stageStatus?: string;
  onReExecute?: () => void;
}

type GateInfo = { gate_id: string; gate_status: string; summary: string; stage?: string } | null;

const card: React.CSSProperties = {
  padding: 14, background: 'var(--color-surface)',
  border: '1px solid var(--color-border)', borderRadius: 8,
};
const cardTitle: React.CSSProperties = {
  fontWeight: 600, fontSize: 14, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6,
};
const muted: React.CSSProperties = { fontSize: 12, color: 'var(--color-text-muted)' };

export function StagePageP6({ projectId, runId = '', stageStatus: _stageStatus, onReExecute: _onReExecute }: Props) {
  const [pkg, setPkg] = useState<any>(null);
  const [gate, setGate] = useState<GateInfo>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const rid = runId || '';
      const pkgRes = rid ? fetch(`/api/projects/${projectId}/runs/${rid}/p6/package`) : Promise.resolve(null);
      const gateRes = fetch(`/api/projects/${projectId}/gates/active`);
      const [p, g] = await Promise.all([pkgRes, gateRes]);
      if (p && p.ok) setPkg((await p.json()).data || (await p.json()));
      if (g.ok) {
        const gd = (await g.json()).data || (await g.json());
        setGate(gd && gd.gate_id ? gd : null);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [projectId, runId]);

  const isGateOpen = gate && gate.gate_status === 'waiting_decision';
  const manifest = pkg?.delivery_manifest || {};
  const riskManifest = pkg?.risk_manifest || {};
  const hashManifest = pkg?.hash_manifest || {};
  const deliveryReport = pkg?.p6_delivery_report || {};
  const desensitization = pkg?.desensitization || {};

  async function downloadFile(path: string) {
    try {
      const url = `/api/projects/${projectId}/runs/${runId || ''}/p6/download?path=${encodeURIComponent(path)}`;
      const res = await fetch(url);
      if (!res.ok) { alert(`下载失败：HTTP ${res.status}`); return; }
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = path.split('/').pop() || 'file';
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e: any) {
      alert(`下载失败：${e.message}`);
    }
  }

  const downloadAll = async () => {
    const files = [...(manifest.output_code || []), ...(manifest.patches || [])];
    for (const f of files) {
      await downloadFile(f.path);
      // Small delay to avoid browser blocking multiple downloads
      await new Promise(r => setTimeout(r, 300));
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, fontSize: 13 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, ...card }}>
        <Icon name="artifact" size={18} style={{ color: 'var(--color-primary)' }} />
        <strong>P6 交付</strong>
        {pkg && !loading && (
          <span style={{ marginLeft: 'auto', fontSize: 11, padding: '2px 8px', borderRadius: 4,
            background: riskManifest.has_blocking ? 'var(--red)' : 'var(--green)',
            color: '#fff', fontWeight: 600 }}>
            {riskManifest.has_blocking ? '有阻断' : '可交付'}
          </span>
        )}
      </div>

      {error && <div style={{ ...card, color: 'var(--red)' }}>加载失败：{error}</div>}
      {loading && <div style={{ ...card }}>加载中…</div>}
      {!loading && !pkg && !error && (
        <div style={{ ...card, color: 'var(--color-text-muted)' }}>
          P5 完成后，P6 将在此展示可交付的产出。
        </div>
      )}

      {pkg && (
        <>
          {/* 1. 交付报告 */}
          <div style={card}>
            <div style={cardTitle}>
              <Icon name="stage" size={14} style={{ color: 'var(--color-primary)' }} />
              交付报告
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 10 }}>
              <div style={{ textAlign: 'center', padding: 8, background: 'var(--color-bg)', borderRadius: 6 }}>
                <div style={{ fontSize: 20, fontWeight: 700 }}>{deliveryReport.summary?.output_code_files ?? 0}</div>
                <div style={muted}>产出文件</div>
              </div>
              <div style={{ textAlign: 'center', padding: 8, background: 'var(--color-bg)', borderRadius: 6 }}>
                <div style={{ fontSize: 20, fontWeight: 700 }}>{deliveryReport.summary?.patch_files ?? 0}</div>
                <div style={muted}>补丁文件</div>
              </div>
              <div style={{ textAlign: 'center', padding: 8, background: 'var(--color-bg)', borderRadius: 6 }}>
                <div style={{ fontSize: 20, fontWeight: 700, color: riskManifest.risk_count > 0 ? 'var(--amber)' : 'var(--green)' }}>
                  {riskManifest.risk_count ?? 0}
                </div>
                <div style={muted}>风险项</div>
              </div>
              <div style={{ textAlign: 'center', padding: 8, background: 'var(--color-bg)', borderRadius: 6 }}>
                <div style={{ fontSize: 20, fontWeight: 700, color: desensitization.ok ? 'var(--green)' : 'var(--amber)' }}>
                  {desensitization.ok ? '✓' : '⚠'}
                </div>
                <div style={muted}>脱敏扫描</div>
              </div>
            </div>
            {(deliveryReport.notes || []).length > 0 && (
              <ul style={{ margin: '10px 0 0', paddingLeft: 18, fontSize: 11, color: 'var(--color-text-muted)' }}>
                {deliveryReport.notes.map((n: string, i: number) => <li key={i}>{n}</li>)}
              </ul>
            )}
          </div>

          {/* 2. 交付包目录树 + 下载 */}
          <div style={card}>
            <div style={cardTitle}>
              <Icon name="files" size={14} style={{ color: 'var(--color-primary)' }} />
              交付包目录树
              {(manifest.output_code?.length > 0 || manifest.patches?.length > 0) ? (
                <button onClick={downloadAll} style={{ marginLeft: 'auto', fontSize: 11, padding: '3px 10px',
                  background: 'var(--color-primary)', color: '#fff', border: 'none', borderRadius: 4,
                  cursor: 'pointer' }}>
                  全部下载
                </button>
              ) : null}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {/* output_code */}
              <div style={{ fontWeight: 600, fontSize: 12, marginTop: 4 }}>📁 output_code/</div>
              {(manifest.output_code || []).map((f: any) => (
                <div key={f.path} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 8px',
                  background: 'var(--color-bg)', borderRadius: 4, fontSize: 11 }}>
                  <Icon name="artifact" size={12} style={{ color: 'var(--color-primary)' }} />
                  <span style={{ flex: 1 }}>{f.path}</span>
                  <code style={{ fontSize: 9, color: 'var(--color-text-muted)' }}>{f.sha256?.slice(0, 8)}…</code>
                  <button onClick={() => downloadFile(f.path)} style={{ background: 'none', border: '1px solid var(--color-border)',
                    borderRadius: 3, padding: '1px 6px', cursor: 'pointer', fontSize: 10 }}>
                    下载
                  </button>
                </div>
              ))}
              <div style={{ fontWeight: 600, fontSize: 12, marginTop: 8 }}>📁 patches/</div>
              {(manifest.patches || []).map((f: any) => (
                <div key={f.path} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 8px',
                  background: 'var(--color-bg)', borderRadius: 4, fontSize: 11 }}>
                  <Icon name="edit" size={12} style={{ color: 'var(--color-primary)' }} />
                  <span style={{ flex: 1 }}>{f.path}</span>
                  <code style={{ fontSize: 9, color: 'var(--color-text-muted)' }}>{f.sha256?.slice(0, 8)}…</code>
                  <button onClick={() => downloadFile(f.path)} style={{ background: 'none', border: '1px solid var(--color-border)',
                    borderRadius: 3, padding: '1px 6px', cursor: 'pointer', fontSize: 10 }}>
                    下载
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* 3. hash_manifest */}
          {(hashManifest.files || []).length > 0 && (
            <div style={card}>
              <div style={cardTitle}>
                <Icon name="key" size={14} style={{ color: 'var(--color-primary)' }} />
                SHA-256 校验（{hashManifest.file_count} 文件）
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {hashManifest.files.map((f: any) => (
                  <div key={f.path} style={{ fontSize: 10, fontFamily: 'var(--mono)', display: 'flex', gap: 6 }}>
                    <span style={{ color: 'var(--color-text-muted)', minWidth: 200 }}>{f.path}</span>
                    <span>{f.sha256}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 4. risk_manifest */}
          <div style={{ ...card, borderColor: riskManifest.risk_count > 0 ? 'var(--amber)' : undefined }}>
            <div style={cardTitle}>
              <Icon name="warning" size={14} style={{ color: 'var(--amber)' }} />
              风险清单（{riskManifest.risk_count} 项）
            </div>
            {riskManifest.risk_count === 0
              ? <div style={muted}>无风险项。</div>
              : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {riskManifest.risks.map((r: any, i: number) => (
                    <div key={i} style={{ fontSize: 12, padding: '4px 8px',
                      background: r.blocking ? '#fef2f2' : '#fffbeb', borderRadius: 4 }}>
                      <strong>{r.type}</strong>
                      {r.description ? <span style={{ marginLeft: 6 }}>{r.description}</span> : null}
                      {r.blocking ? <span style={{ marginLeft: 6, color: 'var(--red)', fontSize: 10 }}>阻塞</span> : null}
                    </div>
                  ))}
                </div>
              )}
          </div>

          {/* 5. 脱敏扫描结果 */}
          <div style={card}>
            <div style={cardTitle}>
              <Icon name="key" size={14} style={{ color: desensitization.ok ? 'var(--green)' : 'var(--amber)' }} />
              脱敏扫描（D-032）
            </div>
            {desensitization.ok
              ? <div style={{ color: 'var(--green)', fontSize: 12 }}>✓ 未检测到明文密钥</div>
              : (
                <div style={{ fontSize: 11, color: 'var(--amber)' }}>
                  ⚠ 检测到 {desensitization.issues_count} 项潜在风险：
                  {(desensitization.issues || []).slice(0, 5).map((issue: any, i: number) => (
                    <div key={i} style={{ marginTop: 2 }}>{issue.path}: {issue.pattern}（{issue.count} 处）</div>
                  ))}
                </div>
              )}
          </div>

          {/* 6. P6 最终 Gate */}
          <div style={card}>
            <div style={cardTitle}>
              <Icon name="gate" size={14} style={{ color: 'var(--color-primary)' }} />
              P6 最终 Gate（用户授权）
            </div>
            {isGateOpen ? (
              <div style={{ padding: 10, background: '#fffbeb', borderRadius: 6, fontSize: 12 }}>
                <p>Gate <code>{gate.gate_id}</code> 等待决策。</p>
                {gate.summary && <p style={muted}>{gate.summary}</p>}
                {riskManifest.has_blocking && (
                  <p style={{ color: 'var(--red)', fontWeight: 500 }}>⚠ 存在阻断项，需处理后方可交付。</p>
                )}
                {!desensitization.ok && (
                  <p style={{ color: 'var(--amber)', fontWeight: 500 }}>⚠ 脱敏扫描发现风险，请确认。</p>
                )}
              </div>
            ) : (
              <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                {pkg.p6_final_gate_id
                  ? `最终 Gate ${pkg.p6_final_gate_id} 已创建（待用户批准）。`
                  : 'Gate 尚未创建（需 P5 先通过验证）。'}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
