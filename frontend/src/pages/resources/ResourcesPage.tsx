/** ResourcesPage — 资源与能力中心 R7 重构。
 *
 * 4 个 Tab：Agent · Skill · MCP · 其他资源
 * 创建（Skill/其他资源）：名称 + 分类 + zip 上传
 * 导入（所有板块）：仅官方社区 URL
 * 导出/启用禁用/编辑/删除：Agent · Skill · 其他资源
 */
import { useState, useEffect, useCallback } from 'react';
import {
  listAgents, createAgent, updateAgent, deleteAgent,
  importAgentFromUrl, exportAgent, toggleAgentEnabled,
  type AgentDef,
} from '../../services/agentService';
import {
  listSkills, updateSkill, deleteSkill,
  uploadSkillZip, exportSkill, toggleSkill, importSkillFromUrl,
  type SkillDef,
} from '../../services/skillService';
import {
  listResources, updateResource, deleteResource,
  uploadResourceZip, exportResource, toggleResource, importResourceFromUrl,
  getRegistrySummary,
  RESOURCE_TYPE_LABELS, RISK_LABELS,
  type ResourceEntry, type RegistrySummary,
} from '../../services/resourceService';
import {
  listMCPServers, createMCPServer, updateMCPServer, deleteMCPServer, testMCPConnection,
  importMCPFromUrl, GITHUB_MCP_PRESET,
  type MCPServer, type MCPServerTool,
} from '../../services/mcpService';
import { Icon } from '../../components/ui/Icon';
import { listProfiles, type ModelProfileInfo } from '../../services/modelService';

type Tab = 'agents' | 'skills' | 'mcp' | 'other';

const TYPE_GROUPS: Record<string, string[]> = {
  mcp: ['mcp'],
  other: ['tool', 'hook', 'policy', 'deterministic_transformer', 'execution_provider', 'template', 'case', 'expert_agent'],
};

// R7: Added 'case' (案例) and 'expert_agent' (专家Agent) to other tab.
// 'template' and 'deterministic_transformer' were already present.
// All 12 resource types from the backend registry are now accessible.

const AGENT_CATEGORY_LABEL: Record<string, string> = { system: '系统内置', expert: '专家' };
const STATUS_LABEL: Record<string, string> = { active: '活跃', draft: '草稿', disabled: '已禁用', deprecated: '已废弃' };

const SKILL_CATS = ['', 'common', 'p0', 'p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'other'];
const SKILL_CAT_CN: Record<string, string> = {
  '': '全部', common: '通用', other: '其他',
  p0: 'P0 接入', p1: 'P1 建档', p2: 'P2 评估',
  p3: 'P3 规划', p4: 'P4 执行', p5: 'P5 验证', p6: 'P6 交付',
};

// ── 通用 Modal 框 ────────────────────────────────────────────────────────
function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.35)', zIndex: 1100, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }} onClick={onClose}>
      <div className="card" style={{ width: 'min(600px, 96vw)', maxHeight: '90vh', overflow: 'auto', padding: 20 }} onClick={e => e.stopPropagation()}>
        <div className="spread" style={{ marginBottom: 12 }}>
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button className="btn sm ghost" onClick={onClose} aria-label="关闭"><Icon name="close" size={16} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

function F({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><label className="sub" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>{label}</label>{children}</div>;
}

// ── 社区 URL 导入弹窗 ────────────────────────────────────────────────────
function ImportFromUrlModal({ title, placeholder, onImport, onClose }: {
  title: string;
  placeholder?: string;
  onImport: (url: string) => Promise<void>;
  onClose: () => void;
}) {
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handle = async () => {
    if (!url.trim().startsWith('https://')) { setError('请输入 HTTPS 格式的官方社区链接'); return; }
    setLoading(true); setError(null);
    try {
      await onImport(url.trim());
      onClose();
    } catch (e) { setError(e instanceof Error ? e.message : '导入失败'); }
    finally { setLoading(false); }
  };

  return (
    <Modal title={title} onClose={onClose}>
      <div style={{ display: 'grid', gap: 12 }}>
        <div className="sub" style={{ fontSize: 12, padding: '8px 10px', background: 'var(--surface-2)', borderRadius: 6 }}>
          仅支持从官方社区导入。请粘贴官方社区资源链接，后台自动下载并解压存储。
        </div>
        <F label="官方社区链接 *">
          <input className="inp" value={url} onChange={e => setUrl(e.target.value)}
            placeholder={placeholder || 'https://community.example.com/resources/xxx.zip'}
            onKeyDown={e => { if (e.key === 'Enter') handle(); }} />
        </F>
        {error && <div className="err">⚠ {error}</div>}
        <div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}>
          <button className="btn ghost" onClick={onClose} disabled={loading}>取消</button>
          <button className="btn" onClick={handle} disabled={loading || !url.trim()}>
            {loading ? '导入中…' : '确认导入'}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ── Agent Modal ──────────────────────────────────────────────────────────
function AgentModal({ agent, onClose, onSaved }: { agent?: AgentDef; onClose: () => void; onSaved: () => void }) {
  const isEdit = !!agent;
  const isSystem = agent?.category === 'system';
  const [name, setName] = useState(agent?.name || '');
  const [agentType] = useState(agent?.agent_type || 'expert');
  const [responsibilities, setResp] = useState(agent?.responsibilities || '');
  const [forbidden, setForbidden] = useState(agent?.forbidden || '');
  const [customPrompt, setPrompt] = useState(agent?.custom_prompt || '');
  const [boundSkills, setBS] = useState(agent?.bound_skills?.join(', ') || '');
  const [boundTools, setBT] = useState(agent?.bound_tools?.join(', ') || '');
  const [boundMcps, setBM] = useState(agent?.bound_mcps?.join(', ') || '');
  const [modelRef, setModelRef] = useState(agent?.model_policy_ref || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [availSkills, setAvailSkills] = useState<SkillDef[]>([]);
  const [availTools, setAvailTools] = useState<ResourceEntry[]>([]);
  const [availProfiles, setAvailProfiles] = useState<ModelProfileInfo[]>([]);
  const [ddOpen, setDdOpen] = useState('');

  useEffect(() => {
    listSkills().then(r => setAvailSkills(r.skills || [])).catch(() => {});
    listResources({ type: 'tool' }).then(r => setAvailTools(r.resources || [])).catch(() => {});
    listProfiles().then(r => setAvailProfiles(r.data.profiles || [])).catch(() => {});
  }, []);

  const ComboInput = ({ label, value, onChange, options, ddKey }: {
    label: string; value: string; onChange: (v: string) => void;
    options: { id: string; name: string }[]; ddKey: string;
  }) => (
    <F label={label}>
      <div style={{ position: 'relative' }}>
        <div style={{ display: 'flex', gap: 4 }}>
          <input className="inp" style={{ flex: 1 }} value={value} onChange={e => onChange(e.target.value)}
            placeholder="手动输入 ID 或从下拉选择（逗号分隔多个）" />
          <button type="button" className="btn sm ghost" onClick={() => setDdOpen(ddOpen === ddKey ? '' : ddKey)}>
            {ddOpen === ddKey ? '▲' : '▼'} 选择
          </button>
        </div>
        {ddOpen === ddKey && options.length > 0 && (
          <div className="card" style={{ position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10, maxHeight: 200, overflow: 'auto', marginTop: 2, padding: 6 }}>
            {options.map(o => (
              <div key={o.id} className="row" style={{ gap: 6, padding: '4px 6px', cursor: 'pointer', fontSize: 12, borderRadius: 4 }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface-2)')}
                onMouseLeave={e => (e.currentTarget.style.background = '')}
                onClick={() => {
                  const cur = value ? value.split(',').map(s => s.trim()).filter(Boolean) : [];
                  if (!cur.includes(o.id)) onChange([...cur, o.id].join(', '));
                  setDdOpen('');
                }}>
                <span style={{ flex: 1 }}>{o.name}</span>
                <code style={{ fontSize: 10, color: 'var(--sub)' }}>{o.id}</code>
              </div>
            ))}
          </div>
        )}
      </div>
    </F>
  );

  const save = async () => {
    if (!name.trim()) { setError('名称不能为空'); return; }
    setSaving(true); setError(null);
    try {
      const data: Partial<AgentDef> = {
        name: name.trim(), agent_type: agentType, responsibilities, forbidden,
        custom_prompt: customPrompt || null,
        bound_skills: boundSkills ? boundSkills.split(',').map(s => s.trim()).filter(Boolean) : null,
        bound_tools: boundTools ? boundTools.split(',').map(s => s.trim()).filter(Boolean) : null,
        bound_mcps: boundMcps ? boundMcps.split(',').map(s => s.trim()).filter(Boolean) : null,
        model_policy_ref: modelRef || null,
      };
      if (isEdit && agent) await updateAgent(agent.agent_id, data);
      else await createAgent({ ...data, category: 'expert' });
      onSaved(); onClose();
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败'); }
    finally { setSaving(false); }
  };

  return (
    <Modal title={isEdit ? `编辑 Agent：${agent?.name}` : '创建 Agent'} onClose={onClose}>
      <div style={{ display: 'grid', gap: 10 }}>
        <F label="名称 *"><input className="inp" value={name} onChange={e => setName(e.target.value)} disabled={isSystem} /></F>
        {isSystem && <div className="sub" style={{ fontSize: 11, color: 'var(--amber)' }}>系统内置 Agent，不可修改 Prompt 和绑定资源</div>}
        <F label="职责描述"><textarea className="inp" rows={2} value={responsibilities} onChange={e => setResp(e.target.value)} disabled={isSystem} /></F>
        <F label="禁止事项"><textarea className="inp" rows={2} value={forbidden} onChange={e => setForbidden(e.target.value)} disabled={isSystem} /></F>
        {!isSystem && <>
          <F label="自定义 Prompt"><textarea className="inp" rows={4} value={customPrompt} onChange={e => setPrompt(e.target.value)} /></F>
          <ComboInput label="绑定 Skill" value={boundSkills} onChange={setBS} options={availSkills.map(s => ({ id: s.skill_id, name: s.name }))} ddKey="skills" />
          <ComboInput label="绑定工具" value={boundTools} onChange={setBT} options={availTools.map(r => ({ id: r.resource_id, name: r.name }))} ddKey="tools" />
          <F label="绑定 MCP（逗号分隔 ID）"><input className="inp" value={boundMcps} onChange={e => setBM(e.target.value)} /></F>
        </>}
        <F label="默认模型（自定义模式 · 留空=系统默认）">
          <select className="inp" value={modelRef} onChange={e => setModelRef(e.target.value)}>
            <option value="">（系统默认策略）</option>
            {availProfiles.map(p => (
              <option key={p.profile_id} value={p.profile_id}>
                {p.display_name || p.model_name}（{p.provider_id}）{p.status === 'configured' ? '' : ' · 未配置'}
              </option>
            ))}
          </select>
        </F>
        <div className="muted" style={{ fontSize: 12, marginTop: -4 }}>
          自定义模式下该 Agent 使用此模型；模型不可用时按策略自动回退到下一个可用模型。
        </div>
        {error && <div className="err">⚠ {error}</div>}
        <div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}>
          <button className="btn ghost" onClick={onClose} disabled={saving}>取消</button>
          <button className="btn" onClick={save} disabled={saving}>{saving ? '保存中…' : '保存'}</button>
        </div>
      </div>
    </Modal>
  );
}

// ── Skill Modal（简化为 名称 + 分类 + zip 上传）────────────────────────
function SkillModal({ skill, onClose, onSaved }: { skill?: SkillDef; onClose: () => void; onSaved: () => void }) {
  const isEdit = !!skill;
  const [name, setName] = useState(skill?.name || '');
  const [cat, setCat] = useState(skill?.category || 'common');
  const [desc, setDesc] = useState(skill?.description || '');
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (!name.trim()) { setError('名称不能为空'); return; }
    if (!isEdit && !zipFile) { setError('请上传 zip 文件'); return; }
    setSaving(true); setError(null);
    try {
      if (!isEdit && zipFile) {
        await uploadSkillZip(name.trim(), cat, desc.trim(), zipFile);
      } else if (isEdit && skill) {
        await updateSkill(skill.skill_id, { name: name.trim(), category: cat, description: desc.trim() });
      }
      onSaved(); onClose();
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败'); }
    finally { setSaving(false); }
  };

  return (
    <Modal title={isEdit ? `编辑 Skill：${skill?.name}` : '创建 Skill'} onClose={onClose}>
      <div style={{ display: 'grid', gap: 12 }}>
        <F label="名称 *"><input className="inp" value={name} onChange={e => setName(e.target.value)} /></F>
        <F label="分类 *">
          <select className="inp" value={cat} onChange={e => setCat(e.target.value)}>
            {SKILL_CATS.filter(c => c !== '').map(c => (
              <option key={c} value={c}>{SKILL_CAT_CN[c] || c}</option>
            ))}
          </select>
        </F>
        <F label="描述"><textarea className="inp" rows={3} value={desc} onChange={e => setDesc(e.target.value)} /></F>
        {!isEdit && (
          <F label="Skill 包（zip）*">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <label className="btn sm ghost" style={{ cursor: 'pointer' }}>
                <Icon name="import" size={13} /> 选择 zip 文件
                <input type="file" accept=".zip" style={{ display: 'none' }}
                  onChange={e => setZipFile(e.target.files?.[0] || null)} />
              </label>
              {zipFile && <span className="sub" style={{ fontSize: 12 }}>{zipFile.name}</span>}
            </div>
            <div className="sub" style={{ fontSize: 11, marginTop: 4 }}>zip 包将自动解压存储到 source/skills/{cat}/{name}/</div>
          </F>
        )}
        {error && <div className="err">⚠ {error}</div>}
        <div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}>
          <button className="btn ghost" onClick={onClose} disabled={saving}>取消</button>
          <button className="btn" onClick={save} disabled={saving}>{saving ? '保存中…' : '保存'}</button>
        </div>
      </div>
    </Modal>
  );
}

// ── Resource Modal（其他资源 — 名称 + 分类 + zip 上传）──────────────────
function ResourceModal({ resource, defaultType, onClose, onSaved }: {
  resource?: ResourceEntry; defaultType?: string; onClose: () => void; onSaved: () => void;
}) {
  const isEdit = !!resource;
  const [name, setName] = useState(resource?.name || '');
  const [rtype, setRtype] = useState(resource?.resource_type || defaultType || 'tool');
  const [desc, setDesc] = useState(resource?.description || '');
  const [risk, setRisk] = useState(resource?.risk_level || 'L1');
  const [status, setS] = useState(resource?.status || 'draft');
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (!name.trim()) { setError('名称不能为空'); return; }
    if (!isEdit && !zipFile) { setError('请上传 zip 文件'); return; }
    setSaving(true); setError(null);
    try {
      if (!isEdit && zipFile) {
        await uploadResourceZip(name.trim(), rtype, desc.trim(), risk, zipFile);
      } else if (isEdit && resource) {
        await updateResource(resource.resource_id, { name: name.trim(), description: desc.trim(), risk_level: risk, status });
      }
      onSaved(); onClose();
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败'); }
    finally { setSaving(false); }
  };

  return (
    <Modal title={isEdit ? `编辑：${resource?.name}` : '创建资源'} onClose={onClose}>
      <div style={{ display: 'grid', gap: 10 }}>
        <F label="名称 *"><input className="inp" value={name} onChange={e => setName(e.target.value)} /></F>
        <F label="资源类型">
          <select className="inp" value={rtype} onChange={e => setRtype(e.target.value)} disabled={isEdit}>
            <option value="tool">工具</option><option value="hook">Hook</option>
            <option value="policy">策略</option><option value="deterministic_transformer">转换器</option>
            <option value="execution_provider">执行器</option>
            <option value="mcp">MCP</option><option value="template">模板</option>
          </select>
        </F>
        <F label="风险等级">
          <select className="inp" value={risk} onChange={e => setRisk(e.target.value)}>
            {Object.entries(RISK_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </F>
        {isEdit && (
          <F label="状态">
            <select className="inp" value={status} onChange={e => setS(e.target.value)}>
              <option value="draft">草稿</option><option value="active">活跃</option>
              <option value="disabled">已禁用</option><option value="read_only">只读</option>
            </select>
          </F>
        )}
        <F label="描述"><textarea className="inp" rows={3} value={desc} onChange={e => setDesc(e.target.value)} /></F>
        {!isEdit && (
          <F label="资源包（zip）*">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <label className="btn sm ghost" style={{ cursor: 'pointer' }}>
                <Icon name="import" size={13} /> 选择 zip 文件
                <input type="file" accept=".zip" style={{ display: 'none' }}
                  onChange={e => setZipFile(e.target.files?.[0] || null)} />
              </label>
              {zipFile && <span className="sub" style={{ fontSize: 12 }}>{zipFile.name}</span>}
            </div>
            <div className="sub" style={{ fontSize: 11, marginTop: 4 }}>zip 包将自动解压存储到 source/resources/{name}/</div>
          </F>
        )}
        {error && <div className="err">⚠ {error}</div>}
        <div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}>
          <button className="btn ghost" onClick={onClose} disabled={saving}>取消</button>
          <button className="btn" onClick={save} disabled={saving}>{saving ? '保存中…' : '保存'}</button>
        </div>
      </div>
    </Modal>
  );
}

// ── MCP Modal ────────────────────────────────────────────────────────────
function MCPModal({ server, onClose, onSaved }: { server?: MCPServer; onClose: () => void; onSaved: () => void }) {
  const isEdit = !!server;
  const [name, setName] = useState(server?.name || '');
  const [desc, setDesc] = useState(server?.description || '');
  const [transport, setTransport] = useState<'stdio' | 'sse'>(server?.transport || 'stdio');
  const [command, setCmd] = useState(server?.command || '');
  const [argsStr, setArgsStr] = useState(server?.args?.join(' ') || '');
  const [envStr, setEnvStr] = useState(server?.env_vars ? Object.entries(server.env_vars).map(([k, v]) => `${k}=${v}`).join('\n') : '');
  const [sseUrl, setSseUrl] = useState(server?.sse_url || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applyGitHubPreset = () => {
    setName(GITHUB_MCP_PRESET.name); setDesc(GITHUB_MCP_PRESET.description);
    setTransport('stdio'); setCmd(GITHUB_MCP_PRESET.command);
    setArgsStr(GITHUB_MCP_PRESET.args?.join(' ') || '');
    setEnvStr('GITHUB_PERSONAL_ACCESS_TOKEN=');
  };

  const save = async () => {
    if (!name.trim()) { setError('名称不能为空'); return; }
    setSaving(true); setError(null);
    try {
      const args = argsStr.trim() ? argsStr.trim().split(/\s+/) : [];
      const env_vars: Record<string, string> = {};
      if (envStr.trim()) envStr.trim().split('\n').forEach(line => {
        const eq = line.indexOf('=');
        if (eq > 0) env_vars[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
      });
      const data = { name: name.trim(), description: desc.trim(), transport,
        command: command.trim() || null, args: args.length > 0 ? args : null,
        env_vars: Object.keys(env_vars).length > 0 ? env_vars : null,
        sse_url: sseUrl.trim() || null };
      if (isEdit && server) await updateMCPServer(server.mcp_id, data);
      else await createMCPServer(data);
      onSaved(); onClose();
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败'); }
    finally { setSaving(false); }
  };

  return (
    <Modal title={isEdit ? `编辑 MCP：${server?.name}` : '添加 MCP 服务器'} onClose={onClose}>
      <div style={{ display: 'grid', gap: 10 }}>
        {!isEdit && <button className="btn sm" onClick={applyGitHubPreset} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="robot" size={14} />一键填入 GitHub MCP 配置</button>}
        <F label="名称 *"><input className="inp" value={name} onChange={e => setName(e.target.value)} placeholder="如：GitHub MCP" /></F>
        <F label="描述"><input className="inp" value={desc} onChange={e => setDesc(e.target.value)} /></F>
        <F label="传输方式"><select className="inp" value={transport} onChange={e => setTransport(e.target.value as 'stdio' | 'sse')}>
          <option value="stdio">stdio（子进程）</option><option value="sse">SSE（HTTP）</option>
        </select></F>
        {transport === 'stdio' ? <>
          <F label="命令"><input className="inp" value={command} onChange={e => setCmd(e.target.value)} placeholder="如：npx 或 uvx" /></F>
          <F label="参数（空格分隔）"><input className="inp" value={argsStr} onChange={e => setArgsStr(e.target.value)} /></F>
          <F label="环境变量（每行 KEY=VALUE）"><textarea className="inp" rows={4} value={envStr} onChange={e => setEnvStr(e.target.value)} style={{ fontFamily: 'monospace', fontSize: 11 }} /></F>
        </> : (
          <F label="SSE URL"><input className="inp" value={sseUrl} onChange={e => setSseUrl(e.target.value)} placeholder="https://mcp-server.example.com/sse" /></F>
        )}
        {error && <div className="err">⚠ {error}</div>}
        <div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}>
          <button className="btn ghost" onClick={onClose} disabled={saving}>取消</button>
          <button className="btn" onClick={save} disabled={saving}>{saving ? '保存中…' : '保存'}</button>
        </div>
      </div>
    </Modal>
  );
}

// ── 辅助 ─────────────────────────────────────────────────────────────────
function Pager({ page, total, size, onPage }: { page: number; total: number; size: number; onPage: (p: number) => void }) {
  const pages = Math.ceil(total / size) || 1;
  return (
    <div className="row" style={{ justifyContent: 'center', gap: 8, marginTop: 12, alignItems: 'center' }}>
      <button className="btn sm ghost" disabled={page === 0} onClick={() => onPage(page - 1)}>◀ 上一页</button>
      <span className="sub" style={{ fontSize: 12 }}>第 {page + 1} 页 / 共 {pages} 页（{total} 条）</span>
      <button className="btn sm ghost" disabled={(page + 1) * size >= total} onClick={() => onPage(page + 1)}>下一页 ▶</button>
    </div>
  );
}

function StatCard({ active, onClick, title, value, label }: { active: boolean; onClick: () => void; title: string; value: string; label: string }) {
  return <button className="card statcard" onClick={onClick} style={{ textAlign: 'left', cursor: 'pointer', border: active ? '2px solid var(--accent-ink)' : '1px solid var(--line)', background: active ? 'var(--blue-bg, var(--surface-2))' : 'var(--surface)', width: '100%' }}>
    <b>{title}</b><div className="snum">{value}</div><div className="slabel">{label}</div>
  </button>;
}

// ═════════════════════════════════════════════════════════════════════════
// ResourcesPage
// ═════════════════════════════════════════════════════════════════════════

export function ResourcesPage() {
  const PAGE = 10;
  const [tab, setTab] = useState<Tab>('agents');

  // Agent state
  const [agents, setAgents] = useState<AgentDef[]>([]);
  const [agentTot, setAgentTot] = useState(0);
  const [agentPg, setAgentPg] = useState(0);

  // Skill state
  const [skills, setSkills] = useState<SkillDef[]>([]);
  const [skillTot, setSkillTot] = useState(0);
  const [skillPg, setSkillPg] = useState(0);
  const [skillCat, setSkillCat] = useState('');

  // MCP state
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([]);
  const [mcpTot, setMcpTot] = useState(0);
  const [mcpPg, setMcpPg] = useState(0);
  const [testingMCP, setTestingMCP] = useState<string | null>(null);

  // Other resources state
  const [allResources, setAllResources] = useState<ResourceEntry[]>([]);
  const [resTot, setResTot] = useState(0);
  const [resPg, setResPg] = useState(0);
  const [resTypeF, setResTypeF] = useState('');

  const [, setSummary] = useState<RegistrySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Modal state
  const [showCreateAgent, setShowCreateAgent] = useState(false);
  const [editAgent, setEditAgent] = useState<AgentDef | null>(null);
  const [showCreateSkill, setShowCreateSkill] = useState(false);
  const [editSkill, setEditSkill] = useState<SkillDef | null>(null);
  const [showCreateResource, setShowCreateResource] = useState(false);
  const [editResource, setEditResource] = useState<ResourceEntry | null>(null);
  const [defaultResourceType, setDefaultResourceType] = useState('tool');
  const [showCreateMCP, setShowCreateMCP] = useState(false);
  const [editMCP, setEditMCP] = useState<MCPServer | null>(null);

  // Import URL modal state
  const [importModal, setImportModal] = useState<{
    type: 'agent' | 'skill' | 'mcp' | 'resource' | 'case';
    title: string;
  } | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [aR, sR, rR, mcpR, sumR] = await Promise.all([
        listAgents(undefined, undefined, PAGE, 0),
        listSkills('P', undefined, PAGE, 0),
        listResources({ limit: 200, offset: 0 }),
        listMCPServers(PAGE, 0).catch(() => ({ servers: [], total: 0 })),
        getRegistrySummary().catch(() => null),
      ]);
      setAgents(aR.agents || []); setAgentTot(aR.total || 0); setAgentPg(0);
      setSkills(sR.skills || []); setSkillTot(sR.total || 0); setSkillPg(0);
      setAllResources(rR.resources || []); setResTot(rR.total || 0); setResPg(0);
      setMcpServers(mcpR.servers || []); setMcpTot(mcpR.total || 0); setMcpPg(0);
      setSummary(sumR);
    } catch (e) { setError(e instanceof Error ? e.message : '加载失败'); }
    finally { setLoading(false); }
  }, [PAGE]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const fetchAgentsPg = async (pg: number) => {
    const r = await listAgents(undefined, undefined, PAGE, pg * PAGE);
    setAgents(r.agents || []); setAgentTot(r.total || 0); setAgentPg(pg);
  };
  const fetchSkillsPg = async (pg: number, cat?: string) => {
    const c = cat !== undefined ? cat : skillCat;
    if (cat !== undefined) setSkillCat(c);
    const r = await listSkills('P', c || undefined, PAGE, pg * PAGE);
    setSkills(r.skills || []); setSkillTot(r.total || 0); setSkillPg(pg);
  };
  const fetchMcpPg = async (pg: number) => {
    const r = await listMCPServers(PAGE, pg * PAGE).catch(() => ({ servers: [], total: 0 }));
    setMcpServers(r.servers || []); setMcpTot(r.total || 0); setMcpPg(pg);
  };
  const fetchResPg = async (pg: number, t?: string) => {
    const tp = t !== undefined ? t : resTypeF;
    if (t !== undefined) setResTypeF(tp);
    // 全部（无 type 过滤）：其他资源 tab 按 type 分组是客户端过滤，须一次载入全部资源
    // （否则 PAGE 上限会把排序靠后的 tool 等类型截断，导致"工具 tab 什么都没有"）。
    // 选中具体 type 时走后端 type 过滤 + 分页。
    const r = await listResources(tp ? { type: tp, limit: PAGE, offset: pg * PAGE } : { limit: 200, offset: 0 });
    setAllResources(r.resources || []); setResTot(r.total || 0); setResPg(pg);
  };

  const delAgent = async (a: AgentDef) => {
    if (a.category === 'system') { alert('系统 Agent 不可删除'); return; }
    if (!confirm(`确认删除 Agent「${a.name}」？`)) return;
    await deleteAgent(a.agent_id); fetchData();
  };
  const handleToggleAgent = async (a: AgentDef) => {
    if (a.category === 'system') return;
    await toggleAgentEnabled(a.agent_id); fetchAgentsPg(agentPg);
  };
  const delSkill = async (s: SkillDef) => {
    if (!confirm(`确认删除 Skill「${s.name}」？`)) return;
    await deleteSkill(s.skill_id); fetchSkillsPg(skillPg);
  };
  const handleToggleSkill = async (s: SkillDef) => {
    await toggleSkill(s.skill_id); fetchSkillsPg(skillPg);
  };
  const delResource = async (r: ResourceEntry) => {
    if (!confirm(`确认删除「${r.name}」？`)) return;
    await deleteResource(r.resource_id); fetchResPg(resPg);
  };
  const handleToggleResource = async (r: ResourceEntry) => {
    const res = await toggleResource(r.resource_id);
    if (res?.data?.status === 'awaiting_approval') {
      alert(res.data.message || '该资源是安全关键资源，禁用需人工审批，已创建审批请求。');
    }
    fetchResPg(resPg);
  };

  const handleImportFromUrl = async (url: string) => {
    if (!importModal) return;
    switch (importModal.type) {
      case 'agent': await importAgentFromUrl(url); break;
      case 'skill': await importSkillFromUrl(url); break;
      case 'mcp': await importMCPFromUrl(url); break;
      case 'case':
      case 'resource': await importResourceFromUrl(url); break;
    }
    fetchData();
  };

  const mcpConnected = mcpServers.filter(s => s.status === 'connected').length;
  const otherR = allResources.filter(r => TYPE_GROUPS.other.includes(r.resource_type));
  const sysA = agents.filter(a => a.category === 'system');
  const expA = agents.filter(a => a.category === 'expert');

  if (loading) return <div className="card"><p>正在加载资源数据…</p></div>;
  if (error) return <div className="card"><p className="err">错误：{error}</p><button className="btn sm" onClick={fetchData}>重试</button></div>;

  return (
    <div>
      <div className="spread">
        <h1>资源与能力中心</h1>
        <span className="sub" style={{ fontSize: 13 }}>Agent · Skill · MCP · 工具/Hook/策略</span>
      </div>

      <div className="statgrid" style={{ marginTop: 10 }}>
        <StatCard active={tab === 'agents'} onClick={() => setTab('agents')} title="Agent" value={`${agentTot}`} label={`系统 ${sysA.length} · 专家 ${expA.length}`} />
        <StatCard active={tab === 'skills'} onClick={() => setTab('skills')} title="Skill" value={`${skillTot}`} label="P 系列 · 产品流程 Skill" />
        <StatCard active={tab === 'mcp'} onClick={() => setTab('mcp')} title="MCP" value={`${mcpTot}`} label={`已连接 ${mcpConnected} · 共 ${mcpTot}`} />
        <StatCard active={tab === 'other'} onClick={() => setTab('other')} title="其他资源" value={`${otherR.length}`} label="工具 · Hook · 策略 · 转换器" />
      </div>

      {/* 操作栏 */}
      <div className="row" style={{ marginTop: 14, marginBottom: 10, alignItems: 'center' }}>
        <b style={{ fontSize: 14 }}>{{ agents: 'Agent 管理', skills: 'Skill 管理', mcp: 'MCP 管理', other: '其他资源' }[tab]}</b>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          {tab === 'agents' && <>
            <button className="btn sm" onClick={() => setShowCreateAgent(true)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="add" size={14} />创建 Agent</button>
            <button className="btn sm ghost" onClick={() => setImportModal({ type: 'agent', title: '从官方社区导入 Agent' })} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="import" size={14} />导入 Agent</button>
          </>}
          {tab === 'skills' && <>
            <button className="btn sm" onClick={() => setShowCreateSkill(true)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="add" size={14} />创建 Skill</button>
            <button className="btn sm ghost" onClick={() => setImportModal({ type: 'skill', title: '从官方社区导入 Skill' })} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="import" size={14} />导入 Skill</button>
          </>}
          {tab === 'mcp' && <>
            <button className="btn sm" onClick={() => setShowCreateMCP(true)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="add" size={14} />添加 MCP</button>
            <button className="btn sm ghost" onClick={() => setImportModal({ type: 'mcp', title: '从官方社区导入 MCP' })} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="import" size={14} />导入 MCP</button>
          </>}
          {tab === 'other' && <>
            <button className="btn sm" onClick={() => { setDefaultResourceType('tool'); setShowCreateResource(true); }} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="add" size={14} />创建资源</button>
            <button className="btn sm ghost" onClick={() => setImportModal({ type: 'resource', title: '从官方社区导入资源' })} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="import" size={14} />导入资源</button>
          </>}
          <button className="btn sm ghost" onClick={fetchData} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="refresh" size={14} />刷新</button>
        </span>
      </div>

      {/* Agent Tab */}
      {tab === 'agents' && <div className="grid" style={{ gap: 10 }}>
        {agents.map(a => {
          const isSys = a.category === 'system';
          return <div key={a.agent_id} className="card" style={{ padding: 14 }}>
            <div className="spread">
              <div className="row" style={{ gap: 8 }}>
                <b>{a.name}</b>
                <span className="tag" style={{ fontSize: 11, background: isSys ? 'var(--blue)' : 'var(--green)', color: '#fff' }}>{AGENT_CATEGORY_LABEL[a.category]}</span>
                {!a.enabled && <span className="tag" style={{ fontSize: 11, background: 'var(--red)', color: '#fff' }}>已禁用</span>}
              </div>
              <span className="tag" style={{ fontSize: 11 }}>{STATUS_LABEL[a.status] || a.status}</span>
            </div>
            <div className="sub" style={{ fontSize: 12, marginTop: 4 }}>{a.responsibilities?.slice(0, 120) || '暂无职责描述'}</div>
            {(a.bound_skills?.length || a.bound_tools?.length || a.bound_mcps?.length) ? (
              <div className="sub" style={{ fontSize: 11, marginTop: 4 }}>
                {a.bound_skills?.length ? `Skill:${a.bound_skills.length} ` : ''}
                {a.bound_tools?.length ? `工具:${a.bound_tools.length} ` : ''}
                {a.bound_mcps?.length ? `MCP:${a.bound_mcps.length}` : ''}
              </div>
            ) : null}
            <div className="row" style={{ marginTop: 10, gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <button className="btn sm ghost" onClick={() => setEditAgent(a)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="edit" size={13} />编辑</button>
              <button className="btn sm ghost" onClick={() => exportAgent(a.agent_id, a.name)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="export" size={13} />导出</button>
              {!isSys && <>
                <button className="btn sm ghost" onClick={() => handleToggleAgent(a)}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: a.enabled ? 'var(--green)' : 'var(--red)' }}>
                  {a.enabled ? '禁用' : '启用'}
                </button>
                <button className="btn sm ghost" onClick={() => delAgent(a)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="delete" size={13} />删除</button>
              </>}
              {isSys && <span className="sub" style={{ fontSize: 11 }}>系统内置（不可删除）</span>}
            </div>
          </div>;
        })}
        {agents.length === 0 && <div className="empty"><p className="sub">暂无 Agent。</p></div>}
        {agentTot > PAGE && <Pager page={agentPg} total={agentTot} size={PAGE} onPage={fetchAgentsPg} />}
      </div>}

      {/* Skill Tab */}
      {tab === 'skills' && <>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
          {SKILL_CATS.map(c => (
            <button key={c} className={`btn sm ${skillCat === c ? '' : 'ghost'}`}
              onClick={() => fetchSkillsPg(0, c)}>
              {SKILL_CAT_CN[c] || c || '全部'}
            </button>
          ))}
        </div>
        <div className="grid" style={{ gap: 10 }}>
          {skills.map(s => (
            <div key={s.skill_id} className="card" style={{ padding: 14 }}>
              <div className="spread">
                <div className="row" style={{ gap: 8 }}>
                  <b>{s.name}</b>
                  <span className="tag" style={{ fontSize: 11, background: 'var(--green)', color: '#fff' }}>{SKILL_CAT_CN[s.category] || s.category}</span>
                  {!s.enabled && <span className="tag" style={{ fontSize: 11, background: 'var(--red)', color: '#fff' }}>已禁用</span>}
                </div>
                <span className="tag" style={{ fontSize: 11 }}>{s.status}</span>
              </div>
              <div className="sub" style={{ fontSize: 12, marginTop: 4 }}>{s.description?.slice(0, 150) || '暂无描述'}</div>
              {s.directory_path && <div className="sub" style={{ fontSize: 11, marginTop: 2 }}><code>{s.directory_path}</code></div>}
              <div className="row" style={{ marginTop: 10, gap: 6, flexWrap: 'wrap' }}>
                <button className="btn sm ghost" onClick={() => setEditSkill(s)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="edit" size={13} />编辑</button>
                <button className="btn sm ghost" onClick={() => exportSkill(s.skill_id, s.name)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="export" size={13} />导出</button>
                <button className="btn sm ghost" onClick={() => handleToggleSkill(s)}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: s.enabled ? 'var(--green)' : 'var(--red)' }}>
                  {s.enabled ? '禁用' : '启用'}
                </button>
                <button className="btn sm ghost" onClick={() => delSkill(s)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="delete" size={13} />删除</button>
              </div>
            </div>
          ))}
          {skills.length === 0 && <div className="empty"><p className="sub">暂无 Skill。点击「创建 Skill」或「导入 Skill」添加。</p></div>}
          {skillTot > PAGE && <Pager page={skillPg} total={skillTot} size={PAGE} onPage={fetchSkillsPg} />}
        </div>
      </>}

      {/* MCP Tab */}
      {tab === 'mcp' && <div className="grid" style={{ gap: 10 }}>
        {mcpServers.map(s => {
          const isConnected = s.status === 'connected';
          const isTesting = testingMCP === s.mcp_id;
          return (
            <div key={s.mcp_id} className="card" style={{ padding: 14 }}>
              <div className="spread">
                <div className="row" style={{ gap: 8 }}>
                  <b>{s.name}</b>
                  <span className="tag" style={{ fontSize: 11, background: 'var(--violet)', color: '#fff' }}>MCP</span>
                  <span className="tag" style={{ fontSize: 11, background: isConnected ? 'var(--green)' : s.status === 'error' ? 'var(--red)' : 'var(--gray)', color: '#fff' }}>
                    {isConnected ? '已连接' : s.status === 'error' ? '错误' : s.status === 'connecting' ? '连接中…' : '未连接'}
                  </span>
                  <span className="tag" style={{ fontSize: 11 }}>{s.transport}</span>
                </div>
              </div>
              <div className="sub" style={{ fontSize: 12, marginTop: 4 }}>{s.description || '暂无描述'}</div>
              {s.command && <div className="sub" style={{ fontSize: 11, marginTop: 2 }}><code>{s.command} {s.args?.join(' ') || ''}</code></div>}
              {s.error_message && <div className="err" style={{ fontSize: 11, marginTop: 4 }}>{s.error_message.slice(0, 200)}</div>}
              {s.tools && s.tools.length > 0 && (
                <div style={{ marginTop: 6, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {s.tools.map((t: MCPServerTool) => (
                    <span key={t.name} className="tag" style={{ fontSize: 10, background: 'var(--surface-2)', color: 'var(--fg)' }} title={t.description}>{t.name}</span>
                  ))}
                </div>
              )}
              <div className="row" style={{ marginTop: 10, gap: 6 }}>
                <button className="btn sm" onClick={async () => { setTestingMCP(s.mcp_id); try { await testMCPConnection(s.mcp_id); await fetchData(); } finally { setTestingMCP(null); } }} disabled={isTesting}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                  <Icon name="plug" size={13} />{isTesting ? '测试中…' : '连通测试'}
                </button>
                <button className="btn sm ghost" onClick={() => setEditMCP(s)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="edit" size={13} />编辑</button>
                <button className="btn sm ghost" onClick={() => { if (confirm(`删除 MCP「${s.name}」？`)) { deleteMCPServer(s.mcp_id); fetchData(); } }} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="delete" size={13} />删除</button>
              </div>
            </div>
          );
        })}
        {mcpServers.length === 0 && <div className="empty"><p className="sub">暂无 MCP 服务器。</p></div>}
        {mcpTot > PAGE && <Pager page={mcpPg} total={mcpTot} size={PAGE} onPage={fetchMcpPg} />}
      </div>}

      {/* Other Tab */}
      {tab === 'other' && <>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
          {['', 'tool', 'hook', 'policy', 'deterministic_transformer', 'execution_provider', 'template'].map(t => (
            <button key={t} className={`btn sm ${resTypeF === t ? '' : 'ghost'}`}
              onClick={() => fetchResPg(0, t)}>
              {t === '' ? '全部' : ({ tool: '工具', hook: 'Hook', policy: '策略', deterministic_transformer: '转换器', execution_provider: '执行器', template: '模板' } as Record<string, string>)[t] || t}
            </button>
          ))}
        </div>
        <div className="grid" style={{ gap: 10 }}>
          {otherR.map(r => (
            <div key={r.resource_id} className="card" style={{ padding: 14 }}>
              <div className="spread">
                <div className="row" style={{ gap: 8 }}>
                  <b>{r.name}</b>
                  <span className="tag" style={{ fontSize: 11, background: 'var(--surface-2)', color: 'var(--fg)' }}>{RESOURCE_TYPE_LABELS[r.resource_type] || r.resource_type}</span>
                  <span className="tag" style={{ fontSize: 11 }}>{RISK_LABELS[r.risk_level] || r.risk_level}</span>
                  {!r.enabled && <span className="tag" style={{ fontSize: 11, background: 'var(--red)', color: '#fff' }}>已禁用</span>}
                </div>
                <span className="tag" style={{ fontSize: 11 }}>{r.status}</span>
              </div>
              <div className="sub" style={{ fontSize: 12, marginTop: 4 }}>{r.description?.slice(0, 150) || '暂无描述'}</div>
              <div className="row" style={{ marginTop: 10, gap: 6, flexWrap: 'wrap' }}>
                <button className="btn sm ghost" onClick={() => setEditResource(r)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="edit" size={13} />编辑</button>
                <button className="btn sm ghost" onClick={() => exportResource(r.resource_id, r.name)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="export" size={13} />导出</button>
                <button className="btn sm ghost" onClick={() => handleToggleResource(r)}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: r.enabled ? 'var(--green)' : 'var(--red)' }}>
                  {r.enabled ? '禁用' : '启用'}
                </button>
                <button className="btn sm ghost" onClick={() => delResource(r)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="delete" size={13} />删除</button>
              </div>
            </div>
          ))}
          {otherR.length === 0 && <div className="empty"><p className="sub">暂无其他资源。</p></div>}
          {/* 仅在选中具体 type（后端分页）时显示分页；"全部"为客户端分组，一次载入全部 */}
          {resTypeF && resTot > PAGE && <Pager page={resPg} total={resTot} size={PAGE} onPage={fetchResPg} />}
        </div>
      </>}

      {/* Modals */}
      {showCreateAgent && <AgentModal onClose={() => setShowCreateAgent(false)} onSaved={fetchData} />}
      {editAgent && <AgentModal agent={editAgent} onClose={() => setEditAgent(null)} onSaved={fetchData} />}
      {showCreateSkill && <SkillModal onClose={() => setShowCreateSkill(false)} onSaved={fetchData} />}
      {editSkill && <SkillModal skill={editSkill} onClose={() => setEditSkill(null)} onSaved={fetchData} />}
      {showCreateResource && <ResourceModal defaultType={defaultResourceType} onClose={() => setShowCreateResource(false)} onSaved={fetchData} />}
      {editResource && <ResourceModal resource={editResource} onClose={() => setEditResource(null)} onSaved={fetchData} />}
      {showCreateMCP && <MCPModal onClose={() => setShowCreateMCP(false)} onSaved={fetchData} />}
      {editMCP && <MCPModal server={editMCP} onClose={() => setEditMCP(null)} onSaved={fetchData} />}
      {importModal && (
        <ImportFromUrlModal
          title={importModal.title}
          placeholder="https://community.rebuild.ai/resources/xxx.zip"
          onImport={handleImportFromUrl}
          onClose={() => setImportModal(null)}
        />
      )}
    </div>
  );
}
