/** FusionPage — R13-7 /fusion 页面真实化（3 子 tab）。
 * 融合(Fusion) 是模型能力/策略，不作为特殊流程/阶段（D-035）。默认关闭，可手动触发。
 * 遵循 09-聚合页红线：默认关闭 / 失败不阻塞 / 输出标记 enhanced_evidence 非 validated / 不展示密钥。
 *
 * 3 子 tab（Q-FUS-3 approved）：
 *   1. 聚合模型管理 — Profile 卡片 + 启用/禁用 + 手动触发 + 查看最近触发
 *   2. 配置 — Panel / Judge / Synthesizer 配置（校验：防递归 / 异构 / 预算 / 超时）
 *   3. 触发历史 — 每次触发记录 + 单次详情（Judge JSON 五字段可视化 / degraded / Key 无效）
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { Icon } from '../../components/ui/Icon';
import { Modal } from '../../components/ui/Modal';
import { StatusBadge } from '../../components/ui/StatusBadge';
import {
  listFusionProfiles, createFusionProfile, updateFusionProfile,
  toggleFusionProfile, triggerFusion, listFusionRuns, getFusionRun,
  type FusionProfile, type FusionRun,
} from '../../services/fusionService';
import { FusionRunDetail } from './components/FusionRunDetail';
import { FusionConfigPanel } from './components/FusionConfigPanel';

type Tab = 'profiles' | 'config' | 'history';

const STYLE: Record<string, { label: string }> = {
  budget: { label: '保守' }, balanced: { label: '平衡' }, frontier: { label: '激进' },
};

export function FusionPage() {
  const [tab, setTab] = useState<Tab>('profiles');
  const [profiles, setProfiles] = useState<FusionProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [configProfile, setConfigProfile] = useState<FusionProfile | null>(null);
  const [historyProfile, setHistoryProfile] = useState<FusionProfile | null>(null);
  const [runs, setRuns] = useState<FusionRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [selectedRun, setSelectedRun] = useState<FusionRun | undefined>(undefined);

  const [triggering, setTriggering] = useState<string | null>(null);

  async function refresh() {
    setLoading(true); setError(null);
    try { setProfiles(await listFusionProfiles()); }
    catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }

  useEffect(() => { refresh(); }, []);

  async function refreshRuns(profileId: string) {
    setRunsLoading(true);
    try {
      const r = await listFusionRuns(profileId);
      setRuns(r.runs || []);
    } catch { setRuns([]); }
    finally { setRunsLoading(false); }
  }

  useEffect(() => {
    if (historyProfile && tab === 'history') refreshRuns(historyProfile.fusion_profile_id);
  }, [historyProfile, tab]);

  async function handleToggle(p: FusionProfile) {
    try { await toggleFusionProfile(p.fusion_profile_id); await refresh(); }
    catch (e) { setError((e as Error).message); }
  }

  async function handleTrigger(p: FusionProfile) {
    setTriggering(p.fusion_profile_id); setError(null);
    try {
      await triggerFusion(p.fusion_profile_id, { message: '手动触发聚合验证' });
      await refresh();
      // 切换到历史 tab 并刷新
      setHistoryProfile(p); setTab('history');
    } catch (e) { setError((e as Error).message); }
    finally { setTriggering(null); }
  }

  async function handleCreateProfile() {
    // 默认创建一个最小 Profile（default-off, manual），用户到 tab 2 配置。
    try {
      await createFusionProfile({
        name: `聚合模型 ${profiles.length + 1}`,
        panel_participants: [],
        judge: { profile_ref: '' },
        synthesizer: { profile_ref: '' },
        global_config: { trigger: 'manual', style: 'balanced', self_moa_enabled: true },
      });
      await refresh();
      setTab('profiles');
    } catch (e) { setError((e as Error).message); }
  }

  async function handleSelectRun(runId: string) {
    try { setSelectedRun(await getFusionRun(runId)); }
    catch { setSelectedRun(undefined); }
  }

  const enabledCount = profiles.filter(p => p.enabled).length;

  return (
    <div>
      <div className="spread">
        <h1>聚合</h1>
        <button className="btn sm" onClick={handleCreateProfile}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <Icon name="add" size={14} />新建聚合模型
        </button>
      </div>
      <p className="sub">
        Fusion / SuperModel 聚合模型配置。Fusion 是模型能力/策略，不作为特殊流程/阶段（D-035）。
        默认关闭，可手动触发。
        <Link to="/models" style={{ marginLeft: 8, fontSize: 12 }}>→ 查看可参与聚合的模型</Link>
      </p>
      <div className="banner info" style={{ marginBottom: 14 }}>
        Fusion 不阻塞基础 Flow；失败不影响主流程；输出作为增强证据进入 Artifact / Trace；
        不直接执行代码；不替代人工 Gate。聚合通过(R13-6)与普通模型一致的方式提供给 ModelGateway。
      </div>

      {error && <div className="banner warn" style={{ marginBottom: 12 }}>{error}</div>}

      {/* 3 子 tab（Q-FUS-3 approved）*/}
      <div className="row" style={{ gap: 8, marginBottom: 14 }}>
        {([['profiles', '聚合模型管理', 'layers'], ['config', '配置', 'settings'], ['history', '触发历史', 'trace']] as const).map(
          ([key, label, icon]) => (
            <button key={key} className={`btn sm ${tab === key ? '' : 'ghost'}`} onClick={() => setTab(key)}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <Icon name={icon as any} size={14} />{label}
            </button>
          )
        )}
      </div>

      {loading && <div className="empty"><p className="sub">加载中…</p></div>}

      {/* ── Tab 1: 聚合模型管理 ── */}
      {tab === 'profiles' && !loading && (
        <div className="cardgrid">
          {profiles.length === 0 && (
            <div className="empty"><p className="sub">暂无聚合模型。点击「新建聚合模型」创建第一个。</p></div>
          )}
          {profiles.map(p => {
            return (
              <div key={p.fusion_profile_id} className="card" style={{ padding: 14 }}>
                <div className="spread">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Icon name="fusion" size={16} style={{ color: 'var(--violet,#8b5cf6)' }} />
                    <b>{p.name}</b>
                    <span className="tag" style={{ fontSize: 11, background: p.enabled ? 'var(--green,#22c55e)' : 'var(--surface-2)',
                      color: p.enabled ? '#fff' : 'var(--fg)' }}>
                      {p.enabled ? '已启用' : '默认关闭'}
                    </span>
                  </div>
                  <StatusBadge label={p.enabled ? '可用' : '未启用'} tone={p.enabled ? 'green' : 'grey'} />
                </div>
                <div className="sub" style={{ fontSize: 12, marginTop: 6 }}>
                  {p.panel_participants.length} 个参与模型 · Judge ·
                  {String(p.judge?.profile_ref || '—').split('/').pop()} · Synthesizer：
                  {String(p.synthesizer?.profile_ref || '—').split('/').pop()}
                  <br />
                  风格：{STYLE[p.style]?.label || p.style} · 触发：{p.trigger} ·
                  阶段：{p.enabled_stages.length ? p.enabled_stages.join(',') : '仅手动'}
                </div>
                <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <button className="btn sm" onClick={() => { setConfigProfile(p); }}>
                    <Icon name="edit" size={13} />配置
                  </button>
                  <button className="btn sm" disabled={p.panel_participants.length === 0 || triggering === p.fusion_profile_id}
                    onClick={() => handleTrigger(p)}
                    title={p.panel_participants.length === 0 ? '请先在配置中添加参与模型' : '手动触发'}>
                    <Icon name={triggering === p.fusion_profile_id ? 'refresh' : 'run'} size={13} />
                    {triggering === p.fusion_profile_id ? '聚合中…' : '触发'}
                  </button>
                  <button className="btn sm ghost" onClick={() => handleToggle(p)}>
                    <Icon name={p.enabled ? 'close' : 'key'} size={13} />
                    {p.enabled ? '禁用' : '启用'}
                  </button>
                  <button className="btn sm ghost" onClick={() => { setHistoryProfile(p); setTab('history'); }}>
                    <Icon name="trace" size={13} />触发历史
                  </button>
                </div>
              </div>
            );
          })}
          <div className="sub" style={{ fontSize: 11, gridColumn: '1 / -1', textAlign: 'right' }}>
            共 {profiles.length} 个 · 已启用 {enabledCount}
          </div>
        </div>
      )}

      {/* ── Tab 2: 配置 ── */}
      {tab === 'config' && !loading && (
        <div className="card" style={{ padding: 16 }}>
          <b style={{ fontSize: 14 }}>聚合策略配置</b>
          <p className="sub" style={{ marginTop: 4 }}>
            选择一个 Profile 配置 Panel（多模型审议）/ Judge（评判）/ Synthesizer（综合）。
            配置变更写入 Audit；synthesizer 不含工具配置（方案 E）。
          </p>
          <div className="grid" style={{ gap: 8, marginTop: 10 }}>
            {profiles.length === 0 && <div className="empty"><p className="sub">暂无聚合模型，先到「聚合模型管理」创建。</p></div>}
            {profiles.map(p => (
              <div key={p.fusion_profile_id} className="card" style={{ padding: 12 }}>
                <div className="spread">
                  <div className="row" style={{ gap: 8 }}>
                    <Icon name="fusion" size={14} /><b>{p.name}</b>
                    <span className="tag" style={{ fontSize: 11 }}>{STYLE[p.style]?.label || p.style}</span>
                  </div>
                  <button className="btn sm" onClick={() => setConfigProfile(p)}><Icon name="edit" size={13} />编辑</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Tab 3: 触发历史 ── */}
      {tab === 'history' && !loading && (
        <div className="grid" style={{ gap: 12 }}>
          <div className="card" style={{ padding: 14 }}>
            <div className="spread">
              <div className="row" style={{ gap: 8 }}>
                <Icon name="trace" size={16} /><b>{historyProfile?.name || '触发历史'}</b>
              </div>
              <select className="btn sm" style={{ minWidth: 160 }}
                value={historyProfile?.fusion_profile_id || ''}
                onChange={e => { const p = profiles.find(x => x.fusion_profile_id === e.target.value) || null;
                  setHistoryProfile(p); setSelectedRun(undefined); }}>
                <option value="">选择 Profile…</option>
                {profiles.map(p => <option key={p.fusion_profile_id} value={p.fusion_profile_id}>{p.name}</option>)}
              </select>
            </div>
            <div style={{ marginTop: 10 }}>
              {runsLoading ? <div className="empty"><p className="sub">加载中…</p></div> : (
                runs.length === 0 ? <div className="empty"><p className="sub">暂无触发记录。</p></div> : (
                  <div style={{ display: 'grid', gap: 6 }}>
                    {runs.map(r => (
                      <button key={r.fusion_run_id} className="card" style={{
                        padding: 10, textAlign: 'left', width: '100%', cursor: 'pointer',
                        borderColor: selectedRun?.fusion_run_id === r.fusion_run_id ? 'var(--blue,#3b82f6)' : undefined,
                      }} onClick={() => handleSelectRun(r.fusion_run_id)}>
                        <div className="spread">
                          <div className="row" style={{ gap: 6 }}>
                            <span className="tag" style={{ fontSize: 11, textTransform: 'uppercase', background:
                              r.status === 'completed' ? 'var(--green,#22c55e)' : r.status === 'degraded' ? 'var(--amber,#f59e0b)' : 'var(--red,#ef4444)',
                              color: r.status === 'degraded' ? '#1a1207' : '#fff' }}>{r.status}</span>
                            <span className="tag" style={{ fontSize: 11 }}>{r.strategy}</span>
                            {r.degraded && <span className="tag" style={{ fontSize: 11 }}>⚠ {r.degrade_reason || '降级'}</span>}
                          </div>
                          <span className="sub" style={{ fontSize: 11 }}>{new Date(r.created_at).toLocaleString()}</span>
                        </div>
                        <div className="sub" style={{ fontSize: 11, marginTop: 2 }}>
                          {r.participants.length} 参与 · {Math.round(r.latency_sum_ms)}ms · {r.trace_refs.length} trace
                        </div>
                      </button>
                    ))}
                  </div>
                )
              )}
            </div>
          </div>
          <div className="card" style={{ padding: 14 }}>
            <b style={{ fontSize: 14, display: 'block', marginBottom: 8 }}>单次运行详情</b>
            <FusionRunDetail run={selectedRun} />
          </div>
        </div>
      )}

      {/* 配置弹窗 WP-7.2 子 tab 2 详情面板 */}
      <Modal open={!!configProfile} onClose={() => setConfigProfile(null)}
        title={configProfile ? `配置：${configProfile.name}` : '配置'} width={680}>
        {configProfile && (
          <FusionConfigPanel profile={configProfile}
            onSaved={async (body) => {
              await updateFusionProfile(configProfile.fusion_profile_id, body);
              setConfigProfile(null); await refresh(); }}
            onClose={() => setConfigProfile(null)} />
        )}
      </Modal>
    </div>
  );
}
