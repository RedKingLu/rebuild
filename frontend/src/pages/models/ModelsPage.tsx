/** ModelsPage — R5-4 重设计：模型与供应商管理。
 *
 * 遵循 文档/06-UX与前端/06-模型资源可视化与真实能力标记（14 能力标记/脱敏/mock 区分）
 * 与 07-视觉系统与前端文案（中文优先）。参照 产物/草稿 截图 1/2/4。
 * 平台助手已移至全局浮动弹窗（PlatformAssistant），本页不再内嵌助手对话。
 */
import { useState, useEffect, useCallback } from 'react';
import {
  listProviders, listProfiles, listStrategies,
  selfTest, listCalls, deleteProvider, deleteStrategy, getUsage,
  CAPABILITY_MARKERS, listModelEvaluations,
  type ProviderInfo, type ModelProfileInfo, type StrategyInfo,
  type SelfTestResult, type CallLogEntry, type UsageInfo,
} from '../../services/modelService';
import { AddProviderModal } from '../../components/models/AddProviderModal';
import { EditProviderModal } from '../../components/models/EditProviderModal';
import { StrategyEditModal } from '../../components/models/StrategyEditModal';
import { CreateStrategyModal } from '../../components/models/CreateStrategyModal';
import { Icon } from '../../components/ui/Icon';
import { ModelCatalogTab } from '../../components/models/ModelCatalogTab';
import { ModelEvalTab } from '../../components/models/ModelEvalTab';

const CRED_LABEL: Record<string, string> = {
  configured: '已配置', missing: '未配置', invalid: '凭据无效', redacted: '已脱敏', not_checked: '未检测',
};
const KEY_SOURCE_LABEL: Record<string, string> = {
  env: '环境变量', generic_fallback: '通用兜底', in_memory: '进程内存（重启失效）', none: '无',
};
// UX-1: call-log source codes → 中文标签. "api" = 工作区 Agent 执行链路（agent_loop 流式调用）。
const SOURCE_LABELS: Record<string, string> = {
  api: '工作区', platform_assistant: '平台助手', self_test: '连通自测',
};

// 真实能力标记徽章（颜色+中文文字双通道，06 §18）
function CapabilityBadge({ marker }: { marker: string }) {
  const m = CAPABILITY_MARKERS[marker] || CAPABILITY_MARKERS.unknown;
  return (
    <span className="tag" style={{ backgroundColor: m.color, color: '#fff', fontSize: 12 }}>{m.label}</span>
  );
}

type Tab = 'providers' | 'catalog' | 'eval' | 'strategies' | 'usage';

export function ModelsPage() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [profiles, setProfiles] = useState<ModelProfileInfo[]>([]);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [calls, setCalls] = useState<CallLogEntry[]>([]);
  const [evalCount, setEvalCount] = useState(0);
  const [usage, setUsage] = useState<UsageInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, SelfTestResult>>({});
  const [testing, setTesting] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('providers');
  const [showAdd, setShowAdd] = useState(false);
  const [editProvider, setEditProvider] = useState<ProviderInfo | null>(null);
  const [editStrategy, setEditStrategy] = useState<StrategyInfo | null>(null);
  const [showCreateStrategy, setShowCreateStrategy] = useState(false);
  // catalogFilter moved into ModelCatalogTab (R15-4-C9).
  const [callPage, setCallPage] = useState(0);
  const [callTotal, setCallTotal] = useState(0);
  const PAGE_SIZE = 10;

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [provResp, profResp, stratResp, callsResp, usageResp] = await Promise.all([
        listProviders(), listProfiles(), listStrategies(), listCalls(PAGE_SIZE, 0), getUsage(),
      ]);
      setProviders(provResp.data?.providers || []);
      setProfiles(profResp.data?.profiles || []);
      setStrategies(stratResp.data?.strategies || []);
      setCalls(callsResp.data?.calls || []);
      setCallTotal(callsResp.data?.total || 0);
      setCallPage(0);
      setUsage(usageResp.data || null);
      // eval results (display-only); failure is non-fatal
      try { listModelEvaluations().then((d) => setEvalCount(d.total)).catch(() => {}); }
      catch { /* ignore */ }
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载模型数据失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleSelfTest = async (providerId: string) => {
    setTesting(providerId);
    try {
      const resp = await selfTest(providerId);
      setTestResults(prev => ({ ...prev, [providerId]: resp.data as SelfTestResult }));
      await fetchData();
    } catch (e) {
      setTestResults(prev => ({ ...prev, [providerId]: { provider_id: providerId, status: 'error', error_message: String(e) } as SelfTestResult }));
    } finally {
      setTesting(null);
    }
  };

  const handleDelete = async (p: ProviderInfo) => {
    const warn = p.origin === 'seed'
      ? `⚠ 内置供应商「${p.provider_name}」将从当前进程移除（重启后从 YAML 恢复）。确认删除？`
      : `确认删除供应商「${p.provider_name}」？此操作不可恢复。`;
    if (!confirm(warn)) return;
    await deleteProvider(p.provider_id);
    await fetchData();
  };

  const handleDeleteStrategy = async (s: StrategyInfo) => {
    if (s.strategy_id === 'system-default') {
      alert('系统默认策略不可删除');
      return;
    }
    if (!confirm(`确认删除策略「${s.strategy_id}」？`)) return;
    const resp = await deleteStrategy(s.strategy_id);
    if (resp.data?.removed) {
      await fetchData();
    } else {
      alert('删除失败：策略不存在或不可删除');
    }
  };

  const handleEdit = (p: ProviderInfo) => {
    setEditProvider(p);
  };

  const fetchCallsPage = async (page: number) => {
    const resp = await listCalls(PAGE_SIZE, page * PAGE_SIZE);
    setCalls(resp.data?.calls || []);
    setCallTotal(resp.data?.total || 0);
    setCallPage(page);
  };

  const configuredCount = providers.filter(p => p.credential_status === 'configured').length;
  const reachableCount = providers.filter(p => p.capability_marker === 'real_available').length;
  const defaultStrategy = strategies.find(s => s.strategy_id === 'system-default');

  if (loading) return <div className="card"><p>正在加载模型数据…</p></div>;
  if (error) return <div className="card"><p className="err">错误：{error}</p><button className="btn sm" onClick={fetchData}>重试</button></div>;

  return (
    <div>
      {/* 头部 */}
      <div className="spread">
        <h1>模型与供应商</h1>
        <span className="sub" style={{ fontSize: 13 }}>
          ModelGateway + LiteLLM 适配层（D-039）· 数据来源：<code>/api/model/*</code>（真实接入）
        </span>
      </div>

      {/* 概览统计（同时作为 tab 切换入口，点击跳转对应面板） */}
      <div className="statgrid" style={{ marginTop: 10 }}>
        <StatCard active={tab === 'providers'} onClick={() => setTab('providers')}
          title="供应商" value={`${configuredCount}`} suffix={` / ${providers.length}`}
          label={`已配置 / 总数 · ${reachableCount} 个已连通`} />
        <StatCard active={tab === 'catalog'} onClick={() => setTab('catalog')}
          title="模型" value={`${profiles.length}`} label="可用模型 Profile（点击查看目录）" />
        <StatCard active={tab === 'eval'} onClick={() => setTab('eval')}
          title="评测" value={`${evalCount}`} label="导入的 Agent×Model 评测结果（展示）" />
        <StatCard active={tab === 'strategies'} onClick={() => setTab('strategies')}
          title="默认模型" value={defaultStrategy?.default_profile_ref || '—'} valueSize={14}
          label="系统默认策略（点击查看）" />
        <StatCard active={tab === 'usage'} onClick={() => setTab('usage')}
          title="调用记录" value={`${usage?.total_calls ?? calls.length}`}
          label={`总调用 · 完成 ${usage?.completed_calls ?? 0} · 失败 ${usage?.failed_calls ?? 0}`} />
      </div>

      {/* 操作条：仅保留当前面板标题 + 动作（tab 切换已由上方四个统计框承担） */}
      <div className="row" style={{ marginTop: 14, marginBottom: 10, alignItems: 'center' }}>
        <b style={{ fontSize: 14 }}>
          {{ providers: '服务商', catalog: '模型目录', eval: '评测结果', strategies: '模型策略', usage: '用量与调用' }[tab]}
        </b>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          {tab === 'providers' && <button className="btn sm" onClick={() => setShowAdd(true)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="add" size={14} />添加供应商</button>}
          {tab === 'strategies' && <button className="btn sm" onClick={() => setShowCreateStrategy(true)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="add" size={14} />创建策略</button>}
          <button className="btn sm ghost" onClick={fetchData} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="refresh" size={14} />刷新</button>
        </span>
      </div>

      {/* 服务商 */}
      {tab === 'providers' && (
        <div className="grid" style={{ gap: 10 }}>
          {providers.map(p => {
            const tr = testResults[p.provider_id];
            return (
              <div key={p.provider_id} className="card" style={{ padding: 14 }}>
                <div className="spread">
                  <div className="row" style={{ gap: 8 }}>
                    <span style={{ width: 30, height: 30, borderRadius: 8, background: 'var(--surface-2)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700 }}>
                      {p.provider_name.charAt(0).toUpperCase()}
                    </span>
                    <div>
                      <b>{p.provider_name}</b>
                      {p.origin === 'user' && <span className="tag" style={{ marginLeft: 6, fontSize: 11, background: 'var(--surface-2)', color: 'var(--fg)' }}>用户导入</span>}
                      {p.homepage && <div className="sub" style={{ fontSize: 11 }}><a href={p.homepage} target="_blank" rel="noreferrer">{p.homepage}</a></div>}
                    </div>
                  </div>
                  <CapabilityBadge marker={p.capability_marker} />
                </div>

                <div className="sub" style={{ fontSize: 12, marginTop: 6 }}>
                  {p.provider_type} · {p.api_format === 'anthropic' ? 'Anthropic 格式' : 'OpenAI 格式'} · {p.model_count} 个模型
                  · 凭据：{CRED_LABEL[p.credential_status] || p.credential_status}
                  {p.key_source && <>（{KEY_SOURCE_LABEL[p.key_source] || p.key_source}）</>}
                </div>
                <div className="sub" style={{ fontSize: 11, marginTop: 2, wordBreak: 'break-all' }}>
                  OpenAI 端点：{p.endpoint_openai || '—'}{p.endpoint_anthropic && <> · Anthropic：{p.endpoint_anthropic}</>}
                </div>
                {p.note && <div className="sub" style={{ fontSize: 11, marginTop: 2 }}>备注：{p.note}</div>}
                {p.last_checked_at && <div className="sub" style={{ fontSize: 11, marginTop: 2 }}>最近自测：{p.last_checked_at}</div>}
                {tr && (
                  <div style={{ marginTop: 6, fontSize: 12 }}>
                    {tr.status === 'reachable' ? '✅ 已连通' : `❌ ${tr.status}`}
                    {tr.latency_ms > 0 && ` · ${tr.latency_ms}ms`}
                    {tr.error_message && <span className="err"> · {tr.error_message}</span>}
                  </div>
                )}

                <div className="row" style={{ marginTop: 10, gap: 6 }}>
                  <button className="btn sm" onClick={() => handleSelfTest(p.provider_id)} disabled={testing === p.provider_id} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    <Icon name="plug" size={14} />{testing === p.provider_id ? '测试中…' : '连通自测'}
                  </button>
                  <button className="btn sm ghost" onClick={() => handleEdit(p)} title="编辑供应商信息、模型和 Key" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="edit" size={14} />编辑</button>
                  <button className="btn sm ghost" onClick={() => handleDelete(p)} title="删除供应商" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="delete" size={14} />删除</button>
                </div>
              </div>
            );
          })}
          {providers.length === 0 && (
            <div className="empty"><p className="sub">暂无供应商。点击右上角「+ 添加供应商」导入，或在 backend/.env 配置内置供应商的 Key。</p></div>
          )}
        </div>
      )}

      {/* 模型目录（R15-4-C9：持久化 ModelCatalog 数据源，非内存 Profile） */}
      {tab === 'catalog' && <ModelCatalogTab />}

      {/* 评测结果（R15-4-C10：导入评测结果展示，非自动评测引擎） */}
      {tab === 'eval' && <ModelEvalTab />}

      {/* 策略 */}
      {tab === 'strategies' && (
        <div className="grid" style={{ gap: 10 }}>
          {strategies.map(s => (
            <div key={s.strategy_id} className="card" style={{ padding: 12 }}>
              <div className="spread">
                <b>{s.strategy_id === 'system-default' ? '系统默认策略' : s.strategy_id}</b>
                <div className="row" style={{ gap: 6 }}>
                  <span className="tag" style={{ fontSize: 11 }}>{s.scope === 'system' ? '系统级' : s.scope}</span>
                  <button className="btn sm ghost" onClick={() => setEditStrategy(s)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="edit" size={13} />编辑</button>
                  {s.strategy_id !== 'system-default' && <button className="btn sm ghost" onClick={() => handleDeleteStrategy(s)} title="删除策略" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="delete" size={13} />删除</button>}
                </div>
              </div>
              <div className="sub" style={{ fontSize: 12, marginTop: 6, lineHeight: 1.7 }}>
                默认模型：{s.default_profile_ref || '—'}<br />
                Fallback（{s.fallback_policy === 'sequential' ? '顺序' : s.fallback_policy}）：{s.fallback_profile_refs.join('、') || '—'}<br />
                流式：{s.streaming_allowed ? '允许' : '禁用'} · Fusion：{s.fusion_allowed ? '允许' : '禁用'} · 工具调用：{s.tool_calling_allowed ? '允许' : '禁用'}<br />
                Trace 策略：{s.trace_policy} · 审计策略：{s.audit_policy}
              </div>
              <div className="sub" style={{ fontSize: 11, marginTop: 6, color: 'var(--amber)' }}>
                注：编辑开放「默认模型 + Fallback 链」；Fallback 为「选择期」回退（默认模型不可用时按链路选择），调用期为单供应商重试。阶段级 override 属 R9。
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 用量与调用 */}
      {tab === 'usage' && (
        <div>
          {/* 真实用量统计 */}
          <div className="statgrid" style={{ marginBottom: 12 }}>
            <div className="card statcard"><b>总请求</b><div className="snum">{usage?.total_calls ?? 0}</div><div className="slabel">完成 {usage?.completed_calls ?? 0} · 失败 {usage?.failed_calls ?? 0}</div></div>
            <div className="card statcard"><b>消耗 Token</b><div className="snum">{(usage?.total_tokens ?? 0).toLocaleString()}</div><div className="slabel">输入 {(usage?.prompt_tokens ?? 0).toLocaleString()} · 输出 {(usage?.completion_tokens ?? 0).toLocaleString()}</div></div>
            <div className="card statcard"><b>总成本</b><div className="snum" style={{ fontSize: 15 }}>暂不可用</div><div className="slabel">{usage?.cost_unavailable_reason || '无计价数据'}</div></div>
            <div className="card statcard"><b>缓存命中率</b><div className="snum">{usage?.cache_hit_rate != null ? `${usage.cache_hit_rate}%` : '—'}</div><div className="slabel">缓存命中 {usage?.cache_hit_tokens?.toLocaleString() ?? 0} / 输入 {usage?.prompt_tokens?.toLocaleString() ?? 0} tokens</div></div>
          </div>

          {/* 按供应商/模型 */}
          {usage && usage.by_provider.length > 0 && (
            <div className="card" style={{ padding: 12, marginBottom: 12 }}>
              <b style={{ fontSize: 13 }}>按供应商 / 模型统计</b>
              <div className="sub" style={{ fontSize: 12, marginTop: 6 }}>
                {usage.by_provider.map(b => <div key={b.provider_id}>供应商 {b.provider_id}：{b.calls} 次调用 · {b.total_tokens.toLocaleString()} tokens</div>)}
                {usage.by_model.map(b => <div key={b.model}>模型 {b.model}：{b.calls} 次 · {b.total_tokens.toLocaleString()} tokens</div>)}
              </div>
            </div>
          )}

          {/* 请求日志 */}
          <div className="sub" style={{ fontSize: 11, marginBottom: 8 }}>调用记录持久化至数据库。每页 {PAGE_SIZE} 条，共 {callTotal} 条。</div>
          {calls.length === 0 ? (
            <div className="empty"><p className="sub">暂无调用记录。尝试连通自测或使用平台助手。</p></div>
          ) : (
            <>
            <div className="grid" style={{ gap: 8 }}>
              {calls.map(c => (
                <div key={c.model_call_id} className="card" style={{ padding: 10, fontSize: 12 }}>
                  <div className="spread">
                    <code style={{ fontSize: 11 }}>{c.model_call_id?.slice(0, 8)}…</code>
                    <span className="tag" style={{ background: c.status === 'completed' ? 'var(--green)' : 'var(--red)', color: '#fff' }}>{c.status === 'completed' ? '成功' : c.status}</span>
                  </div>
                  <div className="sub" style={{ fontSize: 11 }}>
                    {c.selected_model} · {c.provider_id} · 来源 {SOURCE_LABELS[c.source] || c.source} · {c.latency_ms}ms
                  </div>
                  <div className="sub" style={{ fontSize: 11, marginTop: 2 }}>
                    输入 {c.usage_summary?.prompt_tokens?.toLocaleString() ?? 0}
                    · 输出 {c.usage_summary?.completion_tokens?.toLocaleString() ?? 0}
                    · 缓存命中 {c.usage_summary?.cache_hit_tokens?.toLocaleString() ?? 0}
                    · 总计 {c.usage_summary?.total_tokens?.toLocaleString() ?? 0} tokens
                  </div>
                </div>
              ))}
            </div>
            {/* Pagination */}
            <div className="row" style={{ justifyContent: 'center', gap: 8, marginTop: 12, alignItems: 'center' }}>
              <button className="btn sm ghost" disabled={callPage === 0} onClick={() => fetchCallsPage(callPage - 1)}>◀ 上一页</button>
              <span className="sub" style={{ fontSize: 12 }}>第 {callPage + 1} 页 / 共 {Math.ceil(callTotal / PAGE_SIZE) || 1} 页</span>
              <button className="btn sm ghost" disabled={(callPage + 1) * PAGE_SIZE >= callTotal} onClick={() => fetchCallsPage(callPage + 1)}>下一页 ▶</button>
            </div>
            </>
          )}
        </div>
      )}

      {showAdd && <AddProviderModal onClose={() => setShowAdd(false)} onCreated={fetchData} />}
      {editProvider && <EditProviderModal provider={editProvider} profiles={profiles} onClose={() => setEditProvider(null)} onSaved={fetchData} />}
      {editStrategy && <StrategyEditModal strategy={editStrategy} profiles={profiles} onClose={() => setEditStrategy(null)} onSaved={fetchData} />}
      {showCreateStrategy && <CreateStrategyModal profiles={profiles} onClose={() => setShowCreateStrategy(false)} onCreated={fetchData} />}
    </div>
  );
}

// 可点击的统计卡 —— 兼作 tab 切换入口（active 时高亮边框）
function StatCard({ active, onClick, title, value, suffix, valueSize, label }: {
  active: boolean; onClick: () => void; title: string; value: string; suffix?: string; valueSize?: number; label: string;
}) {
  return (
    <button className="card statcard" onClick={onClick}
      style={{
        textAlign: 'left', cursor: 'pointer', border: active ? '2px solid var(--accent-ink)' : '1px solid var(--line)',
        background: active ? 'var(--blue-bg, var(--surface-2))' : 'var(--surface)', width: '100%',
      }}>
      <b>{title}</b>
      <div className="snum" style={valueSize ? { fontSize: valueSize } : undefined}>{value}{suffix && <small>{suffix}</small>}</div>
      <div className="slabel">{label}</div>
    </button>
  );
}
