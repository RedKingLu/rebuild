import { useEffect, useState, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useProjectStore, useRunStore, useWorkspaceStore, useSettingsStore, type ActivityType, type ExecMode } from '../../stores';
import { useFileStore } from '../../stores';
import { STAGE_LABELS, type StageId } from '../../types';
import { GlobalMockBanner } from '../../components/ui/MockBanner';
import { Icon, type IconKey } from '../../components/ui/Icon';

const STAGES: StageId[] = ['p0', 'p1', 'p2', 'p3', 'p4', 'p5', 'p6'];
type DockTab = 'terminal' | 'output' | 'problems' | 'progress';
const EXEC_MODES: [ExecMode, string, IconKey, string][] = [
  ['auto', '自动', 'run', 'Auto：Agent 审核计划，阶段内授权由 Hook+Policy+Auto Review 处理；高风险回用户（D-025）'],
  ['plan', '计划确认', 'success', 'Plan：用户审核 Stage/Task Plan，计划内动作经 Hook+Policy 放行，越界回用户（D-025）'],
  ['manual', '手动', 'gate', 'Manual：用户审核计划、授权动作与阶段晋级（D-025）'],
];

export function WorkspacePage() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const project = useProjectStore(s => id ? s.getProject(id) : undefined);
  const runs = useRunStore(s => id ? s.getRunsByProject(id) : []);
  const run = runs[0] || null;
  const ws = useWorkspaceStore();
  const tree = useFileStore(s => s.tree);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [dockTab, setDockTab] = useState<DockTab>('terminal');
  // Resizable panel widths (with limits)
  const [leftW, setLeftW] = useState(264);
  const [rightW, setRightW] = useState(320);
  const [bottomH, setBottomH] = useState(220);
  const dragging = useRef<'left' | 'right' | 'bottom' | null>(null);

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

  useEffect(() => {
    if (id) ws.setProjectId(id);
    if (!ws.tabs.find(t => t.id === 'agent')) {
      ws.openTab({ id: 'agent', title: 'Agent 对话', kind: 'agent', closable: false });
    }
  }, [id]);

  const openStage = (stage: StageId) => {
    ws.openTab({ id: 'stage', title: STAGE_LABELS[stage], kind: 'stage', data: stage, closable: true });
  };

  const openFile = (path: string) => {
    ws.openTab({ id: 'file:' + path, title: path.split('/').pop() || path, kind: 'file', data: { path, content: '// 文件内容为 Mock 数据\n// ' + path, editable: path.startsWith('generated/') }, closable: true });
  };

  const closeTab = (tabId: string) => {
    ws.closeTab(tabId);
  };

  const tab = ws.tabs.find(t => t.id === ws.activeTab);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <GlobalMockBanner />
      {/* Top Status Bar — 返回 · 项目 · P0-P6 节点 · 阶段 · Gate · 网关/模型 · 执行模式（D-046/§4） */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '0 12px', height: 48, background: 'var(--surface)', borderBottom: '1px solid var(--line)', fontSize: 13, flexShrink: 0, overflow: 'hidden' }}>
        <button className="btn sm ghost" title="返回平台（退出≠停止任务，D-052）" style={{ flexShrink: 0, gap: 2 }} onClick={() => nav('/projects')}>
          <Icon name="chevronLeft" size={16} /> 返回平台
        </button>
        <b style={{ fontSize: 14, flexShrink: 0 }}>{project?.name || id}</b>

        {/* P0-P6 节点指示 */}
        <NodeRail run={run} onOpen={openStage} />

        {/* 右侧状态簇 */}
        <span className="row" style={{ marginLeft: 'auto', gap: 8, flexShrink: 0 }}>
          <StatusChip dot="var(--blue)" label={run?.current_stage ? `阶段 ${STAGE_LABELS[run.current_stage].split(' ')[0]}` : '阶段 未启动'} tone="blue" />
          <StatusChip dot={run?.active_gate ? 'var(--amber)' : 'var(--green)'} label={run?.active_gate ? '等待 Gate' : '无待决 Gate'} tone={run?.active_gate ? 'amber' : 'grey'} />
          <ModelGatewayChip />
          <ExecModeSwitch />
          <button className="btn sm ghost" title="切换主题" style={{ flexShrink: 0 }} onClick={() => useSettingsStore.getState().setTheme(useSettingsStore.getState().theme === 'light' ? 'dark' : 'light')}>
            {useSettingsStore.getState().theme === 'light' ? '☾' : '☀'}
          </button>
        </span>
      </div>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Activity Bar — 6 活动项 + 三区折叠按钮（左/下/右，图标一致，§3.2） */}
        <div style={{ width: 50, minWidth: 50, background: 'var(--color-surface-subtle)', borderRight: '1px solid var(--color-border)', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '6px 0', gap: 2 }}>
          {/* Activity icons */}
          {([['stage', 'stage', '阶段'], ['files', 'files', '文件'], ['git', 'git', 'Git'], ['remote', 'remote', '远程'], ['search', 'search', '搜索'], ['agent', 'agent', 'Agent']] as [ActivityType, IconKey, string][]).map(([key, icon, t]) => {
            const isActive = sidebarOpen && ws.activity === key;
            return (
              <button key={key} title={t} onClick={() => {
                if (ws.activity === key && sidebarOpen) { setSidebarOpen(false); }   // 再次点击当前 active 图标 → 折叠左侧（VSCode 行为，§3.3）
                else { ws.setActivity(key); setSidebarOpen(true); if (key === 'agent') ws.setActiveTab('agent'); }
              }}
                style={{ width: 38, height: 38, border: 'none', borderRadius: 6, background: isActive ? 'var(--color-primary-soft)' : 'transparent', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: isActive ? 'var(--color-primary)' : 'var(--color-text-muted)' }}>
                <Icon name={icon} size={22} />
              </button>
            );
          })}
          <div style={{ flex: 1 }} />
          {/* 三区折叠按钮 — 左 / 下 / 右，统一面板图标 */}
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
        {sidebarOpen && <div style={{ width: leftW, minWidth: leftW, overflow: 'hidden', transition: dragging.current ? 'none' : 'width .15s', background: 'var(--color-surface)', borderRight: '1px solid var(--color-border)', position: 'relative' }}>
          {/* Resize handle — right edge */}
          <div style={{ position: 'absolute', right: -3, top: 0, bottom: 0, width: 6, cursor: 'col-resize', zIndex: 10 }} onMouseDown={(e) => { e.preventDefault(); dragging.current = 'left'; }} />
          {ws.activity === 'stage' && <FlowRailView run={run} onOpen={openStage} />}
          {ws.activity === 'files' && <FileTreeView tree={tree} onOpen={openFile} />}
          {ws.activity === 'agent' && <div className="empty" style={{ fontSize: 12 }}>Agent 对话见中央常驻 Tab</div>}
          {['git', 'remote', 'search'].includes(ws.activity) && <PanelPlaceholder name={ws.activity === 'git' ? 'Git' : ws.activity === 'remote' ? '远程资源' : '搜索'} />}
        </div>}

        {/* Center */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* Gate Banner */}
          {run?.active_gate && (
            <div style={{ padding: '12px 20px', background: 'var(--amber-bg)', borderBottom: '1px solid var(--amber)', flexShrink: 0 }}>
              <div className="spread">
                <div>
                  <h3 style={{ color: 'var(--amber)', margin: 0 }}>⚠ {run.active_gate}</h3>
                  <div style={{ fontSize: 12, color: 'var(--ink-2)', marginTop: 4 }}>风险级别 L2 · 阶段晋级确认 · Mock 演示</div>
                </div>
                <div className="row">
                  <span className="tag amber">等待决策</span>
                  <button className="btn sm">批准</button>
                  <button className="btn sm ghost">拒绝</button>
                </div>
              </div>
            </div>
          )}

          {/* Tab Bar */}
          <div style={{ display: 'flex', background: 'var(--surface-2)', borderBottom: '1px solid var(--line)', overflowX: 'auto', flexShrink: 0 }}>
            {ws.tabs.map(t => (
              <button key={t.id}
                style={{ padding: '8px 14px', border: 'none', background: ws.activeTab === t.id ? 'var(--surface)' : 'transparent', borderBottom: ws.activeTab === t.id ? '2px solid var(--accent-ink)' : '2px solid transparent', fontSize: 13, cursor: 'pointer', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: 6 }}
                onClick={() => ws.setActiveTab(t.id)}>
                {t.title}
                {t.closable && <span style={{ fontSize: 14, color: 'var(--ink-3)' }} onClick={e => { e.stopPropagation(); closeTab(t.id); }}>✕</span>}
              </button>
            ))}
          </div>

          {/* Tab Content */}
          <div style={{ flex: 1, overflow: 'auto', padding: 20, position: 'relative' }}>
            {tab?.kind === 'stage' && <StageTabView stage={(tab.data as StageId)} run={run} />}
            {tab?.kind === 'file' && <FileViewTab data={tab.data as { path: string; content: string; editable: boolean }} />}
            {tab?.kind === 'artifact' && <ArtifactTabView data={tab.data} />}
            {tab?.kind === 'agent' && <AgentChatView projectId={id || ''} run={run} />}
            {!tab && <div className="empty" style={{ fontSize: 13, color: 'var(--ink-2)' }}>选择一个 Tab 查看内容</div>}
          </div>

          {/* Bottom Dock — Terminal / Output / Problems / Progress */}
          {ws.bottomOpen && (
            <div style={{ position: 'relative', flexShrink: 0 }}>
              {/* Resize handle — top edge of dock */}
              <div style={{ position: 'absolute', top: -3, left: 0, right: 0, height: 6, cursor: 'row-resize', zIndex: 10 }} onMouseDown={(e) => { e.preventDefault(); dragging.current = 'bottom'; }} />
              <BottomDockPanel run={run} dockTab={dockTab} setDockTab={setDockTab} height={bottomH} />
            </div>
          )}
        </div>

        {/* Right Panel — collapsible to 0, with thin toggle handle on left edge */}
        <div style={{ position: 'relative', width: ws.rightOpen ? rightW : 0, minWidth: ws.rightOpen ? rightW : 0, overflow: 'hidden', transition: dragging.current ? 'none' : 'width .15s', background: 'var(--color-surface)', borderLeft: ws.rightOpen ? '1px solid var(--color-border)' : 'none', display: 'flex', flexDirection: 'column' }}>
          {/* Thin toggle strip — only visible when collapsed */}
          {!ws.rightOpen && (
            <div onClick={() => ws.setRightOpen(true)} title="展开检视面板"
              style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 4, cursor: 'ew-resize', zIndex: 10, background: 'transparent' }}
              onMouseEnter={e => (e.target as HTMLElement).style.background = 'var(--color-primary-soft)'}
              onMouseLeave={e => (e.target as HTMLElement).style.background = 'transparent'}
            />
          )}
          {/* Resize handle — left edge */}
          {ws.rightOpen && <div style={{ position: 'absolute', left: -3, top: 0, bottom: 0, width: 6, cursor: 'col-resize', zIndex: 10 }} onMouseDown={(e) => { e.preventDefault(); dragging.current = 'right'; }} />}
          {ws.rightOpen && (
            <>
              <div style={{ display: 'flex', borderBottom: '1px solid var(--color-border)', alignItems: 'center' }}>
                {(['gate', 'evidence', 'trace', 'audit', 'context', 'resource', 'model'] as const).map(k => (
                  <button key={k} style={{ flex: 1, padding: '10px 6px', border: 'none', background: ws.rightTab === k ? 'var(--color-surface-subtle)' : 'transparent', borderBottom: ws.rightTab === k ? '2px solid var(--color-primary)' : '2px solid transparent', fontSize: 11, cursor: 'pointer' }}
                    onClick={() => ws.setRightTab(k)}>{k === 'gate' ? 'Gate' : k === 'evidence' ? '证据' : k === 'trace' ? 'Trace' : k === 'audit' ? 'Audit' : k === 'context' ? 'Ctx' : k === 'resource' ? 'Res' : 'Mdl'}</button>
                ))}
                <button onClick={() => ws.setRightOpen(false)} title="折叠检视面板"
                  style={{ width: 28, height: 28, border: 'none', borderRadius: 4, background: 'transparent', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--color-text-muted)', flexShrink: 0 }}>
                  <Icon name="chevronRight" size={16} />
                </button>
              </div>
              <div style={{ flex: 1, overflow: 'auto', padding: 12, fontSize: 13 }}>
                <RightInspectView tab={ws.rightTab} />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Sub-components ──

const STAGE_COLORS: Record<string, string> = { completed: 'var(--green)', in_progress: 'var(--blue)', waiting_gate: 'var(--amber)', blocked: 'var(--red)', failed: 'var(--red)', pending: 'var(--ink-3)', not_enabled: 'var(--line-2)', skipped: 'var(--line-2)' };

// P0-P6 节点指示条：彩色节点 + 连接线，当前阶段高亮（参照 230419 草图）
function NodeRail({ run, onOpen }: { run: ReturnType<typeof useRunStore.getState>['runs'][0] | null; onOpen: (s: StageId) => void }) {
  const cur = run?.current_stage;
  return (
    <div className="row" style={{ gap: 0, flexShrink: 1, overflow: 'hidden', minWidth: 0 }}>
      {STAGES.map((s, i) => {
        const st = run?.stage_status?.[s] || 'pending';
        const color = STAGE_COLORS[st] || 'var(--ink-3)';
        const isCur = s === cur;
        return (
          <div key={s} className="row" style={{ gap: 0, flexShrink: 0 }}>
            {i > 0 && <span style={{ width: 14, height: 2, background: 'var(--line)' }} />}
            <button title={`${STAGE_LABELS[s]} · ${st}`} onClick={() => onOpen(s)}
              style={{ display: 'flex', alignItems: 'center', gap: 4, border: 'none', background: isCur ? 'var(--blue-bg)' : 'transparent', borderRadius: 12, padding: '2px 8px', cursor: 'pointer' }}>
              <span style={{ width: 9, height: 9, borderRadius: '50%', background: color, flexShrink: 0 }} />
              <span style={{ fontSize: 12, fontWeight: isCur ? 700 : 500, color: isCur ? 'var(--accent-ink)' : 'var(--ink-2)' }}>{s.toUpperCase()}</span>
            </button>
          </div>
        );
      })}
    </div>
  );
}

function StatusChip({ dot, label, tone, title }: { dot: string; label: string; tone: string; title?: string }) {
  return (
    <span className={`tag ${tone}`} title={title} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: dot, flexShrink: 0 }} />{label}
    </span>
  );
}

// R5: ModelGateway chip — real status from /api/model/status
function ModelGatewayChip() {
  const [status, setStatus] = useState<string>('loading');
  const [detail, setDetail] = useState<string>('');
  useEffect(() => {
    let cancelled = false;
    fetch('/api/model/status')
      .then(r => r.json())
      .then(d => {
        if (cancelled) return;
        const s = d.data?.overall_status || 'unknown';
        setStatus(s);
        const c = d.data?.configured_providers || 0;
        const t = d.data?.total_providers || 0;
        setDetail(`${c}/${t} provider${t > 1 ? 's' : ''}`);
      })
      .catch(() => { if (!cancelled) { setStatus('not_connected'); setDetail('后端不可达'); } });
    return () => { cancelled = true; };
  }, []);
  const colorMap: Record<string, string> = { available: 'var(--green)', not_configured: 'var(--orange)', not_connected: 'var(--red)', degraded: 'var(--amber)', loading: 'var(--gray)' };
  return (
    <StatusChip
      dot={colorMap[status] || 'var(--gray)'}
      label={`网关 · ${detail || status}`}
      tone={status === 'available' ? 'green' : status === 'not_configured' ? 'amber' : 'grey'}
      title={`ModelGateway: ${status} · 来源: /api/model/status`}
    />
  );
}

// 三执行模式切换：自动 / 计划确认 / 手动（D-025；不改变阶段晋级必须人工授权）
function ExecModeSwitch() {
  const mode = useWorkspaceStore(s => s.execMode);
  const setMode = useWorkspaceStore(s => s.setExecMode);
  return (
    <span style={{ display: 'inline-flex', border: '1px solid var(--line)', borderRadius: 6, overflow: 'hidden', flexShrink: 0 }}>
      {EXEC_MODES.map(([m, label, icon, tip]) => {
        const active = mode === m;
        return (
          <button key={m} title={tip} onClick={() => setMode(m)}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 4, border: 'none', borderRight: m !== 'manual' ? '1px solid var(--line)' : 'none', padding: '4px 9px', fontSize: 12, fontWeight: active ? 700 : 500, cursor: 'pointer', background: active ? 'var(--accent-ink)' : 'transparent', color: active ? '#fff' : 'var(--ink-2)' }}>
            <Icon name={icon} size={13} />{label}
          </button>
        );
      })}
    </span>
  );
}

function PanelPlaceholder({ name }: { name: string }) {
  return (
    <div style={{ padding: 16 }}>
      <b style={{ fontSize: 13 }}>{name} 面板</b>
      <span className="tag placeholder-tag" style={{ marginLeft: 8 }}>占位</span>
      <p className="sub" style={{ marginTop: 8 }}>该面板为基础入口占位，真实接入为后续阶段。</p>
    </div>
  );
}

function FlowRailView({ run, onOpen }: { run: ReturnType<typeof useRunStore.getState>['runs'][0] | null; onOpen: (s: StageId) => void }) {
  const ss: Record<string, string> = run?.stage_status || {};
  return (
    <div style={{ padding: 12 }}>
      <b style={{ fontSize: 13 }}>P0-P6 主轴</b>
      <span className="tag violet" style={{ marginLeft: 8 }}>Mock</span>
      {!run && <div className="empty" style={{ padding: '16px 0', fontSize: 12 }}>无运行中任务</div>}
      {STAGES.map(s => (
        <div key={s} style={{ padding: '10px 8px', marginTop: 4, borderRadius: 6, cursor: 'pointer', border: '1px solid var(--line)', background: 'var(--surface)' }} onClick={() => onOpen(s)}>
          <div className="spread">
            <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--accent-ink)' }}>{s.toUpperCase()}</span>
            <StageStatusLabel status={ss[s] || 'pending'} />
          </div>
          <div style={{ fontSize: 12, color: 'var(--ink-2)', marginTop: 2 }}>{STAGE_LABELS[s]}</div>
        </div>
      ))}
    </div>
  );
}

function StageStatusLabel({ status }: { status: string }) {
  const map: Record<string, [string, string]> = {
    completed: ['green', '已完成'], in_progress: ['blue', '进行中'], waiting_gate: ['amber', '等待Gate'],
    blocked: ['red', '阻塞'], failed: ['red', '失败'], pending: ['grey', '待开始'],
    not_enabled: ['grey', '未启用'], skipped: ['grey', '已跳过'],
  };
  const [tone, label] = map[status] || ['grey', status];
  return <span className={`tag ${tone}`}>{label}</span>;
}

function FileTreeView({ tree, onOpen }: { tree: ReturnType<typeof useFileStore.getState>['tree']; onOpen: (path: string) => void }) {
  const renderNodes = (nodes: any[]): any => nodes.map((n: any) => (
    <div key={n.path}>
      <div style={{ padding: '3px 8px', cursor: n.type === 'file' ? 'pointer' : 'default', fontSize: 13, display: 'flex', alignItems: 'center', gap: 4 }}
        onClick={() => n.type === 'file' && onOpen(n.path)}>
        {n.type === 'dir' ? '📁' : '📄'} {n.name}
      </div>
      {n.children && <div style={{ paddingLeft: 14 }}>{renderNodes(n.children)}</div>}
    </div>
  ));
  return (
    <div style={{ padding: 12 }}>
      <b style={{ fontSize: 13 }}>工作区文件</b>
      <span className="tag violet" style={{ marginLeft: 8 }}>Mock</span>
      {tree.map((r: any) => (
        <div key={r.key} style={{ marginTop: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink-2)', padding: '4px 8px' }}>{r.label}{!r.editable && <span className="tag grey" style={{ marginLeft: 6 }}>只读</span>}</div>
          {r.children?.length ? renderNodes(r.children) : <div style={{ padding: '4px 16px', fontSize: 12, color: 'var(--ink-3)' }}>（空）</div>}
        </div>
      ))}
    </div>
  );
}

function StageTabView({ stage, run }: { stage: StageId; run: ReturnType<typeof useRunStore.getState>['runs'][0] | null }) {
  const ss: string = (run?.stage_status?.[stage] as string) || 'pending';
  return (
    <div>
      <h2>{STAGE_LABELS[stage]} <span className={`tag ${ss === 'completed' ? 'green' : ss === 'waiting_gate' ? 'amber' : ss === 'in_progress' ? 'blue' : 'grey'}`}>{ss}</span></h2>
      <p className="sub">阶段目标展示 · Mock 数据</p>
      <div className="banner info" style={{ marginBottom: 16 }}>本阶段内容为静态 Mock。完成条件：必需 Artifact · 可追溯 Evidence · Trace 记录（均 [mock]）</div>
      <h3>本阶段产物</h3>
      <div className="card" style={{ marginBottom: 12 }}>
        <div className="spread"><b>技术栈分析报告</b><span className="tag violet">Mock</span></div>
        <div className="hash">art-001 · 12,800B · artifacts/tech-stack-report.md</div>
      </div>
      <h3>Evidence 候选</h3>
      <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>暂无已关联证据（Mock）</div>
    </div>
  );
}

function FileViewTab({ data }: { data: { path: string; content: string; editable: boolean } }) {
  return (
    <div>
      <div className="spread" style={{ marginBottom: 8 }}>
        <span className="mono" style={{ fontSize: 12 }}>{data.path} {!data.editable && <span className="tag grey">只读</span>}</span>
        {data.editable && <button className="btn sm">保存</button>}
      </div>
      {data.editable
        ? <textarea style={{ width: '100%', height: 420, fontFamily: 'var(--mono)', fontSize: 13 }} defaultValue={data.content} />
        : <pre style={{ background: 'var(--surface-2)', padding: 16, borderRadius: 8, fontSize: 13, overflow: 'auto' }}>{data.content}</pre>}
      <span className="tag violet" style={{ marginTop: 8 }}>Mock 文件内容</span>
    </div>
  );
}

function ArtifactTabView({ data }: { data: any }) {
  return (
    <div>
      <h2>产物预览</h2>
      <span className="tag violet">Mock</span>
      <div className="card" style={{ marginTop: 12 }}>
        <pre style={{ fontSize: 13, color: 'var(--ink-2)' }}>{JSON.stringify(data, null, 2) || '无数据'}</pre>
      </div>
    </div>
  );
}

// ── Agent Chat — messages in tab, input floating at bottom ──

function AgentChatView({ projectId, run }: { projectId: string; run: ReturnType<typeof useRunStore.getState>['runs'][0] | null }) {
  const [input, setInput] = useState('');
  const msgs = [
    { role: 'assistant' as const, content: `当前项目：${projectId}\n状态：${run?.run_status || '无运行任务'}\n阶段：${run?.current_stage ? STAGE_LABELS[run.current_stage] : '未启动'}\n\n所有信息为 Mock 数据。真实 Agent 对话将在后续阶段接入 ModelGateway。经 ModelGateway 调用模型；高风险动作需人工 Gate。` },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', position: 'relative' }}>
      <div className="banner info" style={{ marginBottom: 12 }}>经 ModelGateway 调用模型；高风险动作需人工 Gate。当前为 Mock 演示。</div>
      {/* Messages area */}
      <div style={{ flex: 1, overflow: 'auto', paddingBottom: 80 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {msgs.map((m, i) => (
            <div key={i} style={{ background: 'var(--surface-2)', padding: '12px 16px', borderRadius: 8, fontSize: 13, whiteSpace: 'pre-wrap', maxWidth: '85%' }}>{m.content}</div>
          ))}
        </div>
      </div>
      {/* Floating input at bottom */}
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0,
        background: 'var(--surface)', borderTop: '1px solid var(--line)',
        padding: '12px 16px', display: 'flex', gap: 8,
        boxShadow: '0 -2px 12px rgba(0,0,0,.06)', zIndex: 20,
      }}>
        <input
          value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && input.trim()) { setInput(''); } }}
          placeholder="向 Agent 提问（Mock 演示，无真实响应）"
          style={{ flex: 1, fontSize: 13 }} disabled
        />
        <button className="btn sm" disabled>发送</button>
      </div>
    </div>
  );
}

// ── Bottom Dock — Terminal / Output / Problems / Progress (ref V26.0) ──

function BottomDockPanel({ run, dockTab, setDockTab, height }: { run: ReturnType<typeof useRunStore.getState>['runs'][0] | null; dockTab: DockTab; setDockTab: (t: DockTab) => void; height: number }) {
  const tabs: [DockTab, string][] = [['terminal', '终端'], ['output', '输出'], ['problems', '问题'], ['progress', '任务进度']];
  return (
    <div style={{ height, borderTop: '1px solid var(--line)', background: 'var(--surface)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', borderBottom: '1px solid var(--line)', background: 'var(--surface-2)', flexShrink: 0 }}>
        {tabs.map(([k, label]) => (
          <button key={k}
            style={{ padding: '6px 14px', border: 'none', background: dockTab === k ? 'var(--surface)' : 'transparent', borderBottom: dockTab === k ? '2px solid var(--accent-ink)' : '2px solid transparent', fontSize: 12, cursor: 'pointer' }}
            onClick={() => setDockTab(k)}>{label}</button>
        ))}
        <span className="tag violet" style={{ margin: '6px 10px', alignSelf: 'center' }}>Mock</span>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 12, fontSize: 12, fontFamily: dockTab === 'terminal' ? 'var(--mono)' : 'var(--sans)' }}>
        {dockTab === 'terminal' && <TerminalMock run={run} />}
        {dockTab === 'output' && <OutputMock run={run} />}
        {dockTab === 'problems' && <ProblemsMock />}
        {dockTab === 'progress' && <ProgressMock run={run} />}
      </div>
    </div>
  );
}

function TerminalMock({ run }: { run: ReturnType<typeof useRunStore.getState>['runs'][0] | null }) {
  const lines = [
    '[rebuild] workspace ready · project=' + (run?.project_id || '—'),
    '[rebuild] execution provider: built-in (mock)',
    '[rebuild] model gateway: not connected',
    '',
    '$ echo "Mock 终端 — 真实终端将在 R8 工作区真实化阶段接入"',
    'Mock 终端 — 真实终端将在 R8 工作区真实化阶段接入',
    '$ ',
  ];
  return (
    <div style={{ color: 'var(--ink-2)', lineHeight: 1.6 }}>
      {lines.map((l, i) => <div key={i} style={{ whiteSpace: 'pre' }}>{l || ' '}</div>)}
    </div>
  );
}

function OutputMock({ run }: { run: ReturnType<typeof useRunStore.getState>['runs'][0] | null }) {
  return (
    <div>
      <div className="hash" style={{ marginBottom: 6 }}>[output] 构建输出占位 — 真实输出将在 R4/R8 接入</div>
      <div className="hash" style={{ marginBottom: 6 }}>[output] 测试结果占位</div>
      <div className="hash">[output] Run: {run?.run_id || '—'} · 阶段: {run?.current_stage || '—'}</div>
      <span className="tag placeholder-tag" style={{ marginTop: 8 }}>占位</span>
    </div>
  );
}

function ProblemsMock() {
  return (
    <div>
      <div style={{ fontSize: 13, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="tag amber">⚠ 1 个警告</span>
        <span className="tag red">✕ 0 个错误</span>
      </div>
      <div className="hash" style={{ marginBottom: 4 }}>⚠ 依赖兼容性：3 个依赖项在信创环境中无对应版本（Mock）</div>
      <div className="hash">文件：artifacts/tech-stack-report.md</div>
      <span className="tag placeholder-tag" style={{ marginTop: 8 }}>占位</span>
    </div>
  );
}

function ProgressMock({ run }: { run: ReturnType<typeof useRunStore.getState>['runs'][0] | null }) {
  return (
    <div>
      {run ? (
        <div>
          <div className="hash" style={{ marginBottom: 4 }}>Run: {run.run_id}</div>
          <div className="hash" style={{ marginBottom: 4 }}>目标: {run.run_goal}</div>
          <div className="hash" style={{ marginBottom: 4 }}>当前阶段: {STAGE_LABELS[run.current_stage]} · 状态: {run.run_status}</div>
          <div className="hash" style={{ marginBottom: 4 }}>启动: {run.started_at} · 更新: {run.updated_at}</div>
          <div style={{ marginTop: 8, background: 'var(--surface-2)', borderRadius: 6, height: 8, overflow: 'hidden' }}>
            <div style={{ width: '60%', height: '100%', background: 'var(--blue)', borderRadius: 6 }} />
          </div>
          <div className="hash" style={{ marginTop: 4 }}>Mock 进度: 60%</div>
        </div>
      ) : (
        <div className="empty" style={{ fontSize: 12, padding: '16px 0' }}>无运行任务</div>
      )}
      <span className="tag placeholder-tag" style={{ marginTop: 8 }}>占位</span>
    </div>
  );
}

// ── Right Inspect Panel ──

function RightInspectView({ tab }: { tab: string }) {
  if (tab === 'gate') return (
    <div>
      <h3 style={{ fontSize: 14, marginBottom: 8 }}>Gate</h3>
      <span className="tag placeholder-tag" style={{ marginBottom: 8 }}>占位</span>
      <span className="tag not-connected-tag" style={{ marginBottom: 8, marginLeft: 8 }}>未接真实服务</span>
      <p className="sub" style={{ marginTop: 8 }}>Gate 决策详情与历史在主展位（中央横幅）展示；右侧检视为辅助详情。</p>
    </div>
  );
  if (tab === 'evidence') return (
    <div>
      <h3 style={{ fontSize: 14, marginBottom: 8 }}>证据链</h3>
      <span className="tag violet" style={{ marginBottom: 8 }}>Mock</span>
      <div style={{ fontSize: 12 }}>
        <div className="card" style={{ marginBottom: 8 }}>
          <div className="spread"><b>ev-001</b><span className="tag green">已验证</span></div>
          <div style={{ marginTop: 4 }}>源码可访问且完整（Mock）</div>
          <div className="hash">stage=p0 · validation_passed</div>
        </div>
        <div className="card" style={{ marginBottom: 8, border: '1px solid var(--red)' }}>
          <div className="spread"><b>ev-002</b><span className="tag red">证据缺口</span></div>
          <div style={{ marginTop: 4 }}>源码可构建：构建验证待执行</div>
          <div className="hash">stage=p0 · blocking=true</div>
        </div>
      </div>
    </div>
  );
  if (tab === 'trace') return (
    <div>
      <h3 style={{ fontSize: 14, marginBottom: 8 }}>Trace</h3>
      <span className="tag violet" style={{ marginBottom: 8 }}>Mock</span>
      <div style={{ fontSize: 12 }}>
        <div className="hash" style={{ marginBottom: 8 }}>tr-001: source_scan · p0 · 源码扫描完成</div>
        <div className="hash" style={{ marginBottom: 8 }}>tr-002: model_call · p2 · 技术栈评估</div>
        <div className="hash" style={{ marginBottom: 8 }}>tr-003: gate_decision · p2 · Gate 等待决策</div>
      </div>
    </div>
  );
  if (tab === 'audit') return (
    <div>
      <h3 style={{ fontSize: 14, marginBottom: 8 }}>Audit</h3>
      <span className="tag violet" style={{ marginBottom: 8 }}>Mock</span>
      <div style={{ fontSize: 12 }}>
        <div className="hash">au-001: gate_decision · L2 · pending · P2→P3 审批</div>
      </div>
    </div>
  );
  if (tab === 'context') return <PlaceholderInspect label="Context" desc="Agent 当前引用的上下文摘要（R6 施工）。" />;
  if (tab === 'resource') return <PlaceholderInspect label="Resource" desc="当前调用的资源状态（Tool/MCP/Expert Agent/Skill，R6 施工）。" />;
  return <PlaceholderInspect label="Model" desc="当前使用的模型 Profile 与调用状态（R5 施工）。" />;
}

function PlaceholderInspect({ label, desc }: { label: string; desc: string }) {
  return (
    <div>
      <h3 style={{ fontSize: 14, marginBottom: 8 }}>{label}</h3>
      <span className="tag placeholder-tag" style={{ marginBottom: 8 }}>占位</span>
      <span className="tag not-connected-tag" style={{ marginBottom: 8, marginLeft: 8 }}>未接真实服务</span>
      <p className="sub" style={{ marginTop: 8 }}>{desc}</p>
    </div>
  );
}
