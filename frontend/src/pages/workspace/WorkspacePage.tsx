/** R8 Workspace — real API-driven workspace with file tree, editing, terminal, AET panels. */
import { useEffect, useState, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useWorkspaceStore, useSettingsStore, type ActivityType, type ExecMode } from '../../stores';
import { STAGE_LABELS, type StageId } from '../../types';
import { listCodingAgents, type CodingAgentInfo } from '../../services/integrationService';
import { GlobalMockBanner } from '../../components/ui/MockBanner';
import { Icon, type IconKey } from '../../components/ui/Icon';
import { FileTree } from './FileTree';
import { FileView } from './FileView';
import { MaterialTree } from './MaterialTree';
import { BottomDock } from './BottomDock';
import { InspectPanel, type InspectTab } from './InspectPanel';
import {
  fetchWorkspace, fetchFileTree, fetchMaterialTree, fetchSessions,
  type WorkspaceAggregate, type FileTreeResponse,
} from '../../services/workspaceService';

const STAGES: StageId[] = ['p0', 'p1', 'p2', 'p3', 'p4', 'p5', 'p6'];
const EXEC_MODES: [ExecMode, string, IconKey, string][] = [
  ['auto', '自动', 'run', 'Auto：Agent 审核计划，阶段内授权由 Hook+Policy+Auto Review 处理；高风险回用户（D-025）'],
  ['plan', '计划确认', 'success', 'Plan：用户审核 Stage/Task Plan，计划内动作经 Hook+Policy 放行，越界回用户（D-025）'],
  ['manual', '手动', 'gate', 'Manual：用户审核计划、授权动作与阶段晋级（D-025）'],
];

export function WorkspacePage() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const ws = useWorkspaceStore();
  const theme = useSettingsStore(s => s.theme);

  // ── R8: Real server state (replaces mock stores) ──
  const [data, setData] = useState<WorkspaceAggregate | null>(null);
  const [fileTree, setFileTree] = useState<FileTreeResponse | null>(null);
  const [materialTree, setMaterialTree] = useState<FileTreeResponse | null>(null);
  const [sessions, setSessions] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [usingMock, setUsingMock] = useState(false);
  const [onbDismissed, setOnbDismissed] = useState(false);

  // ── UI state ──
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [leftW, setLeftW] = useState(264);
  const [rightW, setRightW] = useState(320);
  const [bottomH, setBottomH] = useState(220);
  const [rightTab, setRightTab] = useState<InspectTab>('trace');
  const dragging = useRef<'left' | 'right' | 'bottom' | null>(null);

  // Output history (for terminal → output panel)
  const [outputHistory] = useState<Array<{
    cmd: string; exit_code: number; provider: string; elapsed_ms: number; stdout: string; stderr: string;
  }>>([]);

  const project = data?.project;
  const run = data?.active_run;

  // ── Data loading ──
  const loadData = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const [wsData, ft, mt, sess] = await Promise.all([
        fetchWorkspace(id),
        fetchFileTree(id).catch(() => null),
        fetchMaterialTree(id).catch(() => null),
        fetchSessions(id).catch(() => ({ sessions: [] })),
      ]);
      setData(wsData);
      setFileTree(ft);
      setMaterialTree(mt);
      setSessions(sess.sessions || []);
      setUsingMock(false);
    } catch (e: any) {
      setError(e.message || '加载失败');
      setUsingMock(true);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    if (id) ws.setProjectId(id);
    if (!ws.tabs.find(t => t.id === 'agent')) {
      ws.openTab({ id: 'agent', title: 'Agent 对话', kind: 'agent', closable: false });
    }
    loadData();
  }, [id]);

  // ── Resize handlers ──
  const onMouseMove = useCallback((e: MouseEvent) => {
    if (!dragging.current) return;
    if (dragging.current === 'left') { const w = Math.min(500, Math.max(150, e.clientX - 50)); setLeftW(w); }
    if (dragging.current === 'right') { const w = Math.min(600, Math.max(200, window.innerWidth - e.clientX)); setRightW(w); }
    if (dragging.current === 'bottom') { const h = Math.min(window.innerHeight * 0.55, Math.max(100, window.innerHeight - e.clientY)); setBottomH(h); }
  }, []);
  const onMouseUp = useCallback(() => { dragging.current = null; }, []);
  useEffect(() => {
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => { window.removeEventListener('mousemove', onMouseMove); window.removeEventListener('mouseup', onMouseUp); };
  }, [onMouseMove, onMouseUp]);

  // ── Actions ──
  const openStage = (stage: StageId) => {
    ws.openTab({ id: 'stage', title: STAGE_LABELS[stage], kind: 'stage', data: stage, closable: true });
  };
  const openFile = (path: string) => {
    ws.openTab({ id: 'file:' + path, title: path.split('/').pop() || path, kind: 'file', data: { path }, closable: true });
  };
  const closeTab = (tabId: string) => ws.closeTab(tabId);

  const handleExit = () => {
    const hasRunning = sessions.some(s => s.status === 'running');
    if (hasRunning) {
      if (!confirm(`当前有运行中的任务。\n退出 Workspace 不等于停止任务——低/中风险任务将继续后台执行。\n遇到 Gate、写盘或高风险动作时将暂停等待授权（D-052）。\n\n确定离开？`)) return;
    }
    nav('/projects');
  };

  const tab = ws.tabs.find(t => t.id === ws.activeTab);

  if (error && loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', flexDirection: 'column', gap: 12 }}>
        <div style={{ fontSize: 14, color: 'var(--red)' }}>Workspace 加载失败：{error}</div>
        <button className="btn" onClick={loadData}>重试</button>
        <button className="btn sm ghost" onClick={() => nav('/projects')}>返回项目列表</button>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <GlobalMockBanner />
      {/* Top Status Bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '0 12px', height: 48, background: 'var(--surface)', borderBottom: '1px solid var(--line)', fontSize: 13, flexShrink: 0, overflow: 'hidden' }}>
        <button className="btn sm ghost" title="返回平台（退出≠停止任务，D-052）" style={{ flexShrink: 0, gap: 2 }} onClick={handleExit}>
          <Icon name="chevronLeft" size={16} /> 返回平台
        </button>
        <b style={{ fontSize: 14, flexShrink: 0 }}>{project?.name || id}</b>
        {usingMock && <span className="tag" style={{ background: 'var(--amber)', fontSize: 10 }}>离线模式（使用缓存数据）</span>}

        {/* P0-P6 Node Rail */}
        <NodeRail run={run} onOpen={openStage} />

        {/* Status cluster */}
        <span className="row" style={{ marginLeft: 'auto', gap: 8, flexShrink: 0 }}>
          <StatusChip dot="var(--blue)" label={run?.current_stage ? `阶段 ${STAGE_LABELS[run.current_stage].split(' ')[0]}` : '阶段 未启动'} tone="blue" />
          <StatusChip dot={data?.active_gate ? 'var(--amber)' : 'var(--green)'} label={data?.active_gate ? '等待 Gate' : '无待决 Gate'} tone={data?.active_gate ? 'amber' : 'grey'} />
          <ModelGwChip />
          <ExecModeSwitch />
          <CodingAgentSelector />
          <button className="btn sm ghost" title="切换主题" style={{ flexShrink: 0 }} onClick={() => useSettingsStore.getState().setTheme(useSettingsStore.getState().theme === 'light' ? 'dark' : 'light')}>
            {theme === 'light' ? '☾' : '☀'}
          </button>
        </span>
      </div>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Activity Bar */}
        <div style={{ width: 50, minWidth: 50, background: 'var(--color-surface-subtle)', borderRight: '1px solid var(--color-border)', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '6px 0', gap: 2 }}>
          {([['stage', 'stage', '阶段'], ['files', 'files', '文件'], ['materials', 'folder', '材料'], ['git', 'git', 'Git'], ['remote', 'remote', '远程'], ['search', 'search', '搜索'], ['agent', 'agent', 'Agent']] as [string, IconKey, string][]).map(([key, icon, t]) => {
            const act = key as ActivityType;
            const isActive = sidebarOpen && ws.activity === act;
            return (
              <button key={key} title={t} onClick={() => {
                if (ws.activity === act && sidebarOpen) { setSidebarOpen(false); }
                else { ws.setActivity(act); setSidebarOpen(true); if (key === 'agent') ws.setActiveTab('agent'); }
              }}
                style={{ width: 38, height: 38, border: 'none', borderRadius: 6, background: isActive ? 'var(--color-primary-soft)' : 'transparent', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: isActive ? 'var(--color-primary)' : 'var(--color-text-muted)' }}>
                <Icon name={icon} size={22} />
              </button>
            );
          })}
          <div style={{ flex: 1 }} />
          <div style={{ width: 30, height: 1, background: 'var(--color-border)', margin: '2px 0' }} />
          {([['panelLeft', sidebarOpen, () => setSidebarOpen(!sidebarOpen), '左侧面板'],
             ['panelBottom', ws.bottomOpen, () => ws.setBottomOpen(!ws.bottomOpen), '底部面板'],
             ['panelRight', ws.rightOpen, () => ws.setRightOpen(!ws.rightOpen), '右侧检视']] as [IconKey, boolean, () => void, string][]).map(([icon, open, toggle, t]) => (
            <button key={icon} title={`${open ? '折叠' : '展开'}${t}`} onClick={toggle}
              style={{ width: 38, height: 38, border: 'none', borderRadius: 6, background: open ? 'var(--color-primary-soft)' : 'transparent', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: open ? 'var(--color-primary)' : 'var(--color-text-muted)' }}>
              <Icon name={icon} size={20} />
            </button>
          ))}
        </div>

        {/* Left Panel */}
        {sidebarOpen && (
          <div style={{ width: leftW, minWidth: 150, maxWidth: 500, borderRight: '1px solid var(--color-border)', overflow: 'auto', padding: 8, flexShrink: 0 }}>
            {ws.activity === 'stage' && <FlowRail run={run} onOpen={openStage} />}
            {ws.activity === 'files' && <FileTree tree={fileTree} loading={loading} onOpenFile={openFile} />}
            {ws.activity === 'materials' && <MaterialTree tree={materialTree} loading={loading} onOpenFile={openFile} />}
            {ws.activity === 'agent' && <div className="empty" style={{ fontSize: 12 }}>Agent 对话见中央常驻 Tab</div>}
            {ws.activity === 'git' && <div className="empty" style={{ fontSize: 12 }}>Git：基础占位（范围内入口）</div>}
            {ws.activity === 'remote' && <div className="empty" style={{ fontSize: 12 }}>远程：基础占位（范围内入口）</div>}
            {ws.activity === 'search' && <div className="empty" style={{ fontSize: 12 }}>搜索：基础占位（范围内入口）</div>}
          </div>
        )}

        {/* Resize handle */}
        {sidebarOpen && (
          <div style={{ width: 3, cursor: 'col-resize', background: 'transparent', flexShrink: 0 }}
            onMouseDown={e => { e.preventDefault(); dragging.current = 'left'; }} />
        )}

        {/* Center */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
          {/* Tab bar */}
          <div className="tabbar" style={{ display: 'flex', borderBottom: '1px solid var(--color-border)', background: 'var(--color-surface-subtle)', height: 32, alignItems: 'center', overflow: 'hidden', flexShrink: 0 }}>
            {ws.tabs.map(t => (
              <button key={t.id} className={`tab${ws.activeTab === t.id ? ' active' : ''}`}
                style={{ padding: '4px 12px', fontSize: 12, border: 'none', background: ws.activeTab === t.id ? 'var(--color-surface)' : 'transparent', cursor: 'pointer', borderRight: '1px solid var(--color-border)', color: ws.activeTab === t.id ? 'var(--color-text)' : 'var(--color-text-muted)', display: 'flex', alignItems: 'center', gap: 6 }}
                onClick={() => ws.setActiveTab(t.id)}>
                {t.title}
                {t.closable && <span style={{ fontSize: 14, lineHeight: 1 }} onClick={e => { e.stopPropagation(); closeTab(t.id); }}>×</span>}
              </button>
            ))}
          </div>

          {/* Gate banner */}
          {data?.active_gate && (
            <div style={{ padding: '8px 12px', background: 'var(--amber-soft, #fff8e1)', borderBottom: '1px solid var(--amber)', flexShrink: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>⚠ {data.active_gate.gate_type}</div>
              <div style={{ fontSize: 12, marginTop: 2 }}>{data.active_gate.summary} · 风险级别: {data.active_gate.risk_level || 'L0'}</div>
            </div>
          )}

          {/* R8: 首次进入诚实占位（非阻塞）。完整引导向导归 P0 接入阶段（R9，§25）；
              此处不做收集环境/模型/模式的模态，避免空壳。读取真实 onboarding_done。 */}
          {project && !project.onboarding_done && !onbDismissed && (
            <div style={{ padding: '6px 12px', background: 'var(--color-surface-subtle)', borderBottom: '1px solid var(--color-border)', flexShrink: 0, display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
              <Icon name="run" size={14} />
              <span style={{ color: 'var(--color-text-muted)' }}>
                项目已接入，工作区可直接使用；完整<b>引导向导</b>将在 P0 接入阶段（R9）开放。
              </span>
              <button className="btn sm ghost" style={{ marginLeft: 'auto', fontSize: 11 }} onClick={() => setOnbDismissed(true)}>不再提示</button>
            </div>
          )}

          {/* Center content */}
          <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
            {loading && <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>加载 Workspace…</div>}
            {!loading && tab?.kind === 'agent' && (
              <div style={{ fontSize: 13 }}>
                <h3>Agent 对话</h3>
                <p style={{ color: 'var(--color-text-muted)' }}>
                  项目：{project?.name || id} · 来源：{project?.source_type} · 状态：{project?.workspace_status || 'ready'}
                </p>
                <p style={{ color: 'var(--color-text-muted)', fontSize: 12 }}>
                  Agent 对话将在 P0-P6 编排阶段（R9）接入真实能力。当前展示项目上下文摘要。
                </p>
                {run && (
                  <div style={{ marginTop: 12, fontSize: 12, padding: 8, background: 'var(--color-surface-subtle)', borderRadius: 4 }}>
                    <div>Run: {run.run_id} · 状态: {run.run_status} · 当前阶段: {run.current_stage}</div>
                  </div>
                )}
              </div>
            )}
            {!loading && tab?.kind === 'stage' && (
              <div style={{ fontSize: 13 }}>
                <h3>{STAGE_LABELS[tab.data as StageId]}</h3>
                <p style={{ color: 'var(--color-text-muted)' }}>阶段页面将在 P0-P6 编排阶段（R9）接入真实业务数据。</p>
              </div>
            )}
            {!loading && tab?.kind === 'file' && (tab.data as any) && (
              <FileView
                projectId={id!}
                filePath={(tab.data as { path: string }).path}
                onClose={() => closeTab(tab.id)}
              />
            )}
          </div>

          {/* Bottom Dock */}
          {ws.bottomOpen && (
            <div style={{ height: bottomH, minHeight: 100, maxHeight: window.innerHeight * 0.55, borderTop: '1px solid var(--color-border)', background: 'var(--color-surface)', flexShrink: 0 }}>
              <BottomDock projectId={id!} outputHistory={outputHistory} sessions={sessions} />
            </div>
          )}
        </div>

        {/* Right resize handle */}
        {ws.rightOpen && (
          <div style={{ width: 3, cursor: 'col-resize', background: 'transparent', flexShrink: 0 }}
            onMouseDown={e => { e.preventDefault(); dragging.current = 'right'; }} />
        )}

        {/* Right Panel */}
        <div style={{ width: ws.rightOpen ? rightW : 36, minWidth: ws.rightOpen ? 200 : 36, maxWidth: 600, borderLeft: '1px solid var(--color-border)', display: 'flex', flexDirection: 'column', flexShrink: 0, transition: 'width .15s' }}>
          {!ws.rightOpen ? (
            <div style={{ writingMode: 'vertical-rl', padding: 8, fontSize: 11, color: 'var(--color-text-muted)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}
              onClick={() => ws.setRightOpen(true)}>
              证据 · 过程 · 审计
            </div>
          ) : (
            <>
              <div style={{ display: 'flex', gap: 2, padding: '4px 8px', borderBottom: '1px solid var(--color-border)', flexShrink: 0, overflow: 'hidden' }}>
                {([['gate', 'Gate'], ['evidence', '证据'], ['trace', '过程追踪'], ['audit', '审计']] as [InspectTab, string][]).map(([k, label]) => (
                  <button key={k} className={rightTab === k ? 'active' : ''}
                    style={{ padding: '3px 8px', fontSize: 11, border: 'none', background: rightTab === k ? 'var(--color-primary-soft)' : 'transparent', cursor: 'pointer', borderRadius: 4, color: rightTab === k ? 'var(--color-primary)' : 'var(--color-text-muted)' }}
                    onClick={() => setRightTab(k)}>{label}</button>
                ))}
              </div>
              <div style={{ flex: 1, overflow: 'auto', padding: 8 }}>
                <InspectPanel
                  tab={rightTab}
                  gates={data?.pending_gates || []}
                  activeGate={data?.active_gate || null}
                  artifacts={data?.recent_artifacts || []}
                  evidences={data?.pending_evidence_gaps || []}
                  traces={data?.recent_traces || []}
                  audits={data?.recent_audits || []}
                  loading={loading}
                />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────────────

function NodeRail({ run, onOpen }: { run: any; onOpen: (s: StageId) => void }) {
  const ss = run?.stage_status || {};
  return (
    <div style={{ display: 'flex', gap: 0, alignItems: 'center', flexShrink: 0 }}>
      {STAGES.map((s, i) => {
        const status = ss[s] || 'pending';
        const color = status === 'completed' ? 'var(--green)' : status === 'in_progress' ? 'var(--blue)' : status === 'waiting_gate' ? 'var(--amber)' : status === 'blocked' ? 'var(--red)' : 'var(--color-text-muted)';
        return (
          <div key={s} style={{ display: 'flex', alignItems: 'center' }}>
            {i > 0 && <div style={{ width: 12, height: 1, background: 'var(--color-border)' }} />}
            <span title={STAGE_LABELS[s]} onClick={() => onOpen(s)}
              style={{ width: 10, height: 10, borderRadius: '50%', background: color, cursor: 'pointer', border: '1px solid var(--color-border)' }} />
          </div>
        );
      })}
    </div>
  );
}

function FlowRail({ run, onOpen }: { run: any; onOpen: (s: StageId) => void }) {
  const ss = run?.stage_status || {};
  return (
    <div className="rail">
      <div style={{ marginBottom: 8 }}><b style={{ fontSize: 13 }}>P0-P6 主轴</b></div>
      {!run && <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>尚未启动 Run</div>}
      {STAGES.map(s => (
        <div key={s} className="stage" style={{ cursor: 'pointer', padding: '4px 0', fontSize: 12 }} onClick={() => onOpen(s)}>
          <span className="code" style={{ color: 'var(--color-primary)', fontWeight: 600 }}>{s.toUpperCase()}</span>
          <div>
            <div className="nm" style={{ fontSize: 13 }}>{STAGE_LABELS[s]}</div>
            <div style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>{ss[s] || 'pending'}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function StatusChip({ dot, label }: { dot: string; label: string; tone?: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, flexShrink: 0 }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: dot, display: 'inline-block' }} />
      {label}
    </span>
  );
}

function ModelGwChip() {
  const [gwStatus, setGwStatus] = useState<string>('loading');
  useEffect(() => {
    fetch('/api/model/status').then(r => r.json()).then(d => {
      setGwStatus(d?.data?.global_status || 'unknown');
    }).catch(() => setGwStatus('offline'));
  }, []);
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, flexShrink: 0 }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: gwStatus === 'healthy' ? 'var(--green)' : gwStatus === 'offline' ? 'var(--red)' : 'var(--amber)', display: 'inline-block' }} />
      网关: {gwStatus}
    </span>
  );
}

function ExecModeSwitch() {
  const execMode = useWorkspaceStore(s => s.execMode);
  const setExecMode = useWorkspaceStore(s => s.setExecMode);
  return (
    <span className="row" style={{ gap: 1, flexShrink: 0 }}>
      {EXEC_MODES.map(([mode, label, icon, title]) => (
        <button key={mode} title={title}
          className="btn sm ghost"
          style={{ fontSize: 11, padding: '2px 6px', background: execMode === mode ? 'var(--color-primary-soft)' : 'transparent', color: execMode === mode ? 'var(--color-primary)' : 'var(--color-text-muted)' }}
          onClick={() => setExecMode(mode)}>
          <Icon name={icon} size={12} /> {label}
        </button>
      ))}
    </span>
  );
}

/** Coding agent selector (D-078 / R8-5 stub).
 * R11 will wire selection to LangGraph P4 invocation via CodingAgentAdapter.
 */
function CodingAgentSelector() {
  const [agents, setAgents] = useState<CodingAgentInfo[]>([]);
  const [selected, setSelected] = useState<string>('platform');

  useEffect(() => {
    listCodingAgents().then(setAgents).catch(() => {});
  }, []);

  const typeLabel: Record<string, string> = {
    opencode_cli: 'OpenCode', qcode_cli: 'qcode', openai_compat: '自定义', platform_agent: '平台',
  };

  return (
    <span title="编程 Agent 选择（D-078）：R11 实现真实调用" style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 4 }}>
      <Icon name="agent" size={13} style={{ color: 'var(--color-text-muted)' }} />
      <select
        value={selected}
        onChange={e => setSelected(e.target.value)}
        style={{ fontSize: 11, padding: '2px 4px', background: 'transparent', border: '1px solid var(--color-border)', borderRadius: 4, color: 'var(--color-text-muted)', cursor: 'pointer' }}
      >
        <option value="platform">平台自有 Agent</option>
        {agents.filter(a => a.enabled).map(a => (
          <option key={a.agent_id} value={a.agent_id}>{a.name} ({typeLabel[a.agent_type] ?? a.agent_type})</option>
        ))}
      </select>
    </span>
  );
}
