/** R8 Workspace — real API-driven workspace with file tree, editing, terminal, AET panels. */
import { useEffect, useState, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useWorkspaceStore, useSettingsStore, type ActivityType, type ExecMode } from '../../stores';
import { STAGE_LABELS, type StageId } from '../../types';
import { listCodingAgents, type CodingAgentInfo } from '../../services/integrationService';
import { GlobalMockBanner } from '../../components/ui/MockBanner';
import { Icon, type IconKey } from '../../components/ui/Icon';
import { OnboardingWizard } from '../../components/onboarding/OnboardingWizard';
import { FileTree } from './FileTree';
import { FileView } from './FileView';
import { MaterialTree } from './MaterialTree';
import { BottomDock } from './BottomDock';
import { InspectPanel, type InspectTab } from './InspectPanel';
import { GatePanel } from '../../components/gate/GatePanel';
import { StagePageP0 } from './StagePageP0';
import { StagePageP1 } from './StagePageP1';
import { StagePageP2 } from './StagePageP2';
import { StagePageP3 } from './StagePageP3';
import { StagePageP4 } from './StagePageP4';
import { StagePageP5 } from './StagePageP5';
import { StagePageP6 } from './StagePageP6';
import { AgentChat, type SystemMessage } from '../../components/agent/AgentChat';
import { ConversationList } from './ConversationList';
import { RemoteEnvironmentPanel } from './RemoteEnvironmentPanel';
import {
  fetchWorkspace, fetchFileTree, fetchMaterialTree, fetchSessions, fetchMode,
  type WorkspaceAggregate, type FileTreeResponse,
} from '../../services/workspaceService';
import { updateProject } from '../../services/projectService';
import { connectEventStream } from '../../services/eventService';

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
  const [p0SystemMessages, setP0SystemMessages] = useState<SystemMessage[]>([]);
  const [p0Executing, setP0Executing] = useState(false);
  // UX-3: refresh token for the conversation sidebar after messages are persisted.
  const [convoVersion, setConvoVersion] = useState(0);

  // ── UI state ──
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [leftW, setLeftW] = useState(264);
  const [rightW, setRightW] = useState(320);
  const [bottomH, setBottomH] = useState(220);
  const [rightTab, setRightTab] = useState<InspectTab>('trace');
  const dragging = useRef<'left' | 'right' | 'bottom' | null>(null);

  // Output history (for terminal → output panel). R9-3A: now writable.
  const [outputHistory, setOutputHistory] = useState<Array<{
    cmd: string; exit_code: number; provider: string; elapsed_ms: number; stdout: string; stderr: string;
  }>>([]);

  const project = data?.project;
  const run = data?.active_run;

  // UX-3: when the run's stage changes, resolve the active conversation for that stage
  // (server creates it lazily on first chat; here we just surface the latest active one).
  useEffect(() => {
    if (!id || !run?.current_stage) return;
    let cancelled = false;
    (async () => {
      try {
        const { listConversations } = await import('../../services/conversationService');
        const convos = await listConversations(id);
        if (cancelled) return;
        const stageConvos = convos.filter(c => c.stage === run.current_stage && c.status === 'active');
        const latest = stageConvos.sort((a, b) =>
          (b.last_message_at || b.created_at).localeCompare(a.last_message_at || a.created_at))[0];
        if (latest) ws.setActiveConversation(latest.conversation_id);
      } catch { /* best-effort */ }
    })();
    return () => { cancelled = true; };
  }, [id, run?.current_stage]);

  // R17-5: record "last visited project" on entry, powers OverviewPage "上次退出项目"
  useEffect(() => {
    if (id) localStorage.setItem('lastProjectId', id);
  }, [id]);

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
      // R9-3F: hydrate execution mode from backend so it survives refresh.
      fetchMode(id).then(m => { if (m) ws.setExecMode(m as ExecMode); }).catch(() => {});
    } catch (e: any) {
      setError(e.message || '加载失败');
      setUsingMock(true);
    } finally {
      setLoading(false);
    }
  }, [id]);

  // ── Agent-driven P0 execution (R9-3G P0-2: consume SSE, show in AgentChat) ──
  const executeP0Agent = useCallback(async () => {
    if (!id || p0Executing) return;
    setP0Executing(true);
    setP0SystemMessages([]);
    try {
      const resp = await fetch(`/api/projects/${id}/onboarding/execute`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const reader = resp.body?.getReader();
      if (!reader) throw new Error('No response body');
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';
      const msgs: SystemMessage[] = [];
      const addSysMsg = (data: any, phase: string) => {
        const sm: SystemMessage = {
          id: `${phase}-${Date.now()}-${msgs.length}`,
          phase, message: data.message || '',
          ok: data.ok, file_count: data.file_count, gate_id: data.gate_id,
          timestamp: new Date().toISOString(),
        };
        msgs.push(sm);
        setP0SystemMessages([...msgs]);
      };
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('event: ')) { currentEvent = line.slice(7).trim(); continue; }
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (currentEvent === 'status') addSysMsg(data, data.phase || 'executing');
              else if (currentEvent === 'complete') addSysMsg(data, 'complete');
              else if (currentEvent === 'error') addSysMsg({ ...data, ok: false }, 'error');
            } catch { /* skip malformed */ }
          }
        }
      }
      await loadData();
    } catch (e: any) {
      setP0SystemMessages(prev => [...prev, {
        id: `err-${Date.now()}`, phase: 'error', ok: false,
        message: `Agent 执行失败: ${e.message}`,
        timestamp: new Date().toISOString(),
      }]);
    } finally {
      setP0Executing(false);
    }
  }, [id, p0Executing, loadData]);

  useEffect(() => {
    if (id) ws.setProjectId(id);
    if (!ws.tabs.find(t => t.id === 'agent')) {
      ws.openTab({ id: 'agent', title: 'Agent 对话', kind: 'agent', closable: false });
    }
    loadData();
  }, [id]);

  // R9-5-8 T6: real SSE subscription — refetch the workspace aggregate when the
  // backend emits a domain event (run/gate/stage/trace/audit). Replaces the
  // previously-dead connectEventStream; heartbeats are ignored. Closes on unmount.
  useEffect(() => {
    if (!id) return;
    const DOMAIN_PREFIXES = ['run.', 'stage.', 'gate.', 'trace.', 'audit.', 'checkpoint.', 'interrupt.', 'resume.'];
    const es = connectEventStream(id, (evt: any) => {
      const t = evt?.event_type || evt?.type || '';
      if (DOMAIN_PREFIXES.some(p => t.startsWith(p))) {
        loadData();
      }
    });
    return () => { try { es?.close(); } catch { /* noop */ } };
  }, [id, loadData]);

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
    // UX-2: file tab is a singleton by kind (原设计 "Tab 按类型单例"). Opening any file
    // reuses the single 'file' tab and swaps its content — not one tab per path.
    ws.openTab({ id: 'file', title: path.split('/').pop() || path, kind: 'file', data: { path }, closable: true });
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
          <StatusChip dot={
            data?.active_gate?.gate_status === 'rejected' ? 'var(--red)' :
            data?.active_gate ? 'var(--amber)' : 'var(--green)'
          } label={
            data?.active_gate?.gate_status === 'rejected' ? 'Gate 已拒绝' :
            data?.active_gate?.gate_status === 'changes_requested' ? '需返工' :
            data?.active_gate?.gate_type === 'plan_review' ? '等待计划审核' :
            data?.active_gate ? '等待 Gate' : '无待决 Gate'
          } tone={data?.active_gate?.gate_status === 'rejected' || data?.active_gate?.gate_status === 'changes_requested' ? 'red' : data?.active_gate ? 'amber' : 'grey'} />
          <ModelGwChip />
          <ExecModeSwitch projectId={id!} />
          <CodingAgentSelector projectId={id!} currentRef={project?.coding_agent_ref} />
          <DelegationScopeSwitch projectId={id!} scope={project?.external_platform_scope || 'none'}
            hasAgent={!!project?.coding_agent_ref} />
          {project?.onboarding_done && onbDismissed && (
            <button className="btn sm ghost" title="重新打开引导向导" style={{ flexShrink: 0, fontSize: 11 }}
              onClick={() => setOnbDismissed(false)}>
              重新引导
            </button>
          )}
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
            {ws.activity === 'agent' && (
              <ConversationList
                projectId={id!}
                stage={run?.current_stage || 'p0'}
                refreshKey={convoVersion}
                onSelect={(conv) => {
                  ws.setActiveConversation(conv.conversation_id);
                  ws.setActiveTab('agent');
                }}
              />
            )}
            {ws.activity === 'git' && <div className="empty" style={{ fontSize: 12 }}>Git：基础占位（范围内入口）</div>}
            {ws.activity === 'remote' && <RemoteEnvironmentPanel projectId={id!} />}
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

          {/* R9-3G-B: GatePanel with material review */}
          {data?.active_gate && (
            <GatePanel gate={data.active_gate} projectId={id!}
              onDecided={() => loadData()}
              onProfilingStart={() => { ws.setActiveTab('agent'); }} />
          )}

          {/* R9-3B: Real onboarding wizard replaces the R8 honest placeholder. */}
          {project && !project.onboarding_done && !onbDismissed && (
            <OnboardingWizard
              projectId={id!}
              projectName={project.name}
              sourceType={project.source_type}
              initialMode={ws.execMode}
              codingAgentRef={project.coding_agent_ref}
              onDone={() => { setOnbDismissed(true); executeP0Agent(); }}
            />
          )}

          {/* R9-3B: Hide placeholder banner when wizard is shown */}
          {project && !project.onboarding_done && onbDismissed && (
            <div style={{ padding: '6px 12px', background: 'var(--color-surface-subtle)', borderBottom: '1px solid var(--color-border)', flexShrink: 0, display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
              <Icon name="run" size={14} />
              <span style={{ color: 'var(--color-text-muted)' }}>
                项目已接入，工作区可直接使用；<b>引导向导</b>已跳过。可在设置中重新打开。
              </span>
            </div>
          )}

          {/* Center content */}
          <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
            {loading && <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>加载 Workspace…</div>}
            {!loading && tab?.kind === 'agent' && (
              <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                {/* Compact project summary bar */}
                <div style={{ padding: '6px 12px', fontSize: 11, color: 'var(--color-text-muted)',
                  background: 'var(--color-surface-subtle)', borderBottom: '1px solid var(--color-border)',
                  display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                  <span>项目: <b style={{ color: 'var(--color-text)' }}>{project?.name || id}</b></span>
                  <span>Run: {run?.run_status || '—'}</span>
                  <span>文件数: {data?.file_index?.reduce?.((acc: number, r: any) => acc + (r.children?.length || 0), 0) || '—'}</span>
                </div>
                <div style={{ flex: 1, overflow: 'hidden' }}>
                  <AgentChat
                    projectId={id!}
                    stage={run?.current_stage || 'p0'}
                    systemMessages={p0SystemMessages}
                    conversationId={ws.activeConversationId}
                    onConversationChange={() => setConvoVersion(v => v + 1)}
                    // UX-4 (corrected): real run state for the task-overview bar.
                    // runId fetches the active TaskGraph node checklist; stage backs the
                    // task-context + real-time status when no graph is active.
                    runId={data?.active_run?.run_id || run?.run_id || null}
                    runStatus={run?.run_status || undefined}
                    // R17-3: plan_review gate 等待态 → TaskOverview 显示"计划审核中"
                    activeGate={data?.active_gate || null}
                  />
                </div>
              </div>
            )}
            {!loading && tab?.kind === 'stage' && (
              (() => {
                const sid = tab.data as StageId;
                if (sid === 'p0') {
                  return <StagePageP0
                    projectId={id!}
                    project={project}
                    run={run}
                    traces={data?.recent_traces || []}
                    audits={data?.recent_audits || []}
                    fileIndex={data?.file_index || []}
                    onReExecute={() => setOnbDismissed(false)}
                  />;
                }
                if (sid === 'p1') {
                  return <StagePageP1 projectId={id!} stageStatus={run?.stage_status?.p1} onReExecute={loadData} />;
                }
                if (sid === 'p2') {
                  return <StagePageP2 projectId={id!} stageStatus={run?.stage_status?.p2} onReExecute={loadData} />;
                }
                if (sid === 'p3') {
                  return <StagePageP3 projectId={id!} stageStatus={run?.stage_status?.p3} onReExecute={loadData} />;
                }
                // C8: P4 执行阶段页(真实后端 P4 状态，无 mock)
                if (sid === 'p4') {
                  return <StagePageP4 projectId={id!} runId={data?.active_run?.run_id || run?.run_id || undefined}
                    stageStatus={run?.stage_status?.p4} onReExecute={loadData} />;
                }
                // C7: P5 验证阶段页(真实后端 P5 全量验证结果 + P5→P6 Gate)
                if (sid === 'p5') {
                  return <StagePageP5 projectId={id!} runId={data?.active_run?.run_id || run?.run_id || undefined}
                    stageStatus={run?.stage_status?.p5} onReExecute={loadData} />;
                }
                // C10: P6 交付阶段页(交付包 + 下载 + 最终 Gate)
                if (sid === 'p6') {
                  return <StagePageP6 projectId={id!} runId={data?.active_run?.run_id || run?.run_id || undefined}
                    stageStatus={run?.stage_status?.p6} onReExecute={loadData} />;
                }
                return (
                  <div style={{ fontSize: 13 }}>
                    <h3>{STAGE_LABELS[sid]}</h3>
                    <p style={{ color: 'var(--color-text-muted)' }}>将在后续阶段接入</p>
                  </div>
                );
              })()
            )}
            {!loading && tab?.kind === 'file' && (tab.data as any) && (
              <FileView
                key={(tab.data as { path: string }).path}
                projectId={id!}
                filePath={(tab.data as { path: string }).path}
                onClose={() => closeTab(tab.id)}
              />
            )}
          </div>

          {/* Bottom Dock */}
          {ws.bottomOpen && (
            <div style={{ height: bottomH, minHeight: 100, maxHeight: window.innerHeight * 0.55, borderTop: '1px solid var(--color-border)', background: 'var(--color-surface)', flexShrink: 0 }}>
              <BottomDock projectId={id!} outputHistory={outputHistory} setOutputHistory={setOutputHistory} sessions={sessions} />
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
                  projectId={id}
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
        const color = status === 'completed' ? 'var(--green)' : status === 'in_progress' ? 'var(--blue)' : status === 'waiting_gate' ? 'var(--amber)' : status === 'blocked' ? 'var(--red)' : status === 'changes_requested' ? 'var(--orange, #e67e22)' : 'var(--color-text-muted)';
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

function ExecModeSwitch({ projectId }: { projectId: string }) {
  const execMode = useWorkspaceStore(s => s.execMode);
  const setExecMode = useWorkspaceStore(s => s.setExecMode);
  const handleSwitch = async (mode: ExecMode) => {
    setExecMode(mode);
    // R9-3A: Persist to backend
    try {
      await fetch(`/api/projects/${projectId}/mode`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
    } catch { /* non-fatal */ }
  };
  return (
    <span className="row" style={{ gap: 1, flexShrink: 0 }}>
      {EXEC_MODES.map(([mode, label, icon, title]) => (
        <button key={mode} title={title}
          className="btn sm ghost"
          style={{ fontSize: 11, padding: '2px 6px', background: execMode === mode ? 'var(--color-primary-soft)' : 'transparent', color: execMode === mode ? 'var(--color-primary)' : 'var(--color-text-muted)' }}
          onClick={() => handleSwitch(mode)}>
          <Icon name={icon} size={12} /> {label}
        </button>
      ))}
    </span>
  );
}

/** Coding agent selector (D-078 / R9-3A persistence).
 * Selection persists to Project.coding_agent_ref.
 * Real AI invocation still deferred to R11.
 */
function CodingAgentSelector({ projectId, currentRef }: { projectId: string; currentRef?: string | null }) {
  const [agents, setAgents] = useState<CodingAgentInfo[]>([]);
  const [selected, setSelected] = useState<string>(currentRef || 'platform');

  useEffect(() => {
    listCodingAgents().then(setAgents).catch(() => {});
  }, []);

  // Sync from parent when project data loads
  useEffect(() => {
    if (currentRef) setSelected(currentRef);
  }, [currentRef]);

  const typeLabel: Record<string, string> = {
    opencode_cli: 'OpenCode', qcode_cli: 'qcode', openai_compat: '自定义', platform_agent: '平台',
  };

  const handleChange = async (agentId: string) => {
    setSelected(agentId);
    // R9-3A: Persist to backend
    try {
      const { updateProject } = await import('../../services/projectService');
      await updateProject(projectId, { coding_agent_ref: agentId === 'platform' ? null : agentId });
    } catch { /* non-fatal */ }
  };

  return (
    <span title="编程 Agent 选择（D-078）：R11 实现真实调用，R9 已持久化" style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 4 }}>
      <Icon name="agent" size={13} style={{ color: 'var(--color-text-muted)' }} />
      <select
        value={selected}
        onChange={e => handleChange(e.target.value)}
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

/** B-6: external platform delegation scope switch (D-088). Per-project; the OnboardingWizard
 *  only sets this at creation. This control lets the user switch it later (none / coding_only /
 *  all_states), persisting via PATCH /projects/{id}. Should_delegate() re-reads it live so the
 *  next P4 execution honors the new choice — we never fake "already applied". */
const SCOPE_OPTS: { value: 'none' | 'coding_only' | 'all_stages'; label: string }[] = [
  { value: 'none', label: '自有' },
  { value: 'coding_only', label: '委托 P4' },
  { value: 'all_stages', label: '委托全部' },
];

function DelegationScopeSwitch({ projectId, scope, hasAgent }: {
  projectId: string; scope: 'none' | 'coding_only' | 'all_stages'; hasAgent: boolean;
}) {
  const [val, setVal] = useState(scope);
  useEffect(() => { setVal(scope); }, [scope]);

  const onChange = async (next: 'none' | 'coding_only' | 'all_stages') => {
    setVal(next);
    try { await updateProject(projectId, { external_platform_scope: next }); }
    catch { setVal(scope); /* revert on failure */ }
  };

  // "委托全部/委托 P4" without a coding agent is inert (should_delegate still False); hint that.
  const needsAgent = val !== 'none';

  return (
    <span title="执行方式：自有=rebuild 内部全做；委托 P4=仅 P4 外部平台；委托全部=P0-P6 外部平台(高风险)"
      style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 4 }}>
      <Icon name="remote" size={13} style={{ color: val === 'none' ? 'var(--color-text-muted)' : 'var(--color-primary)' }} />
      <span style={{ display: 'flex', border: '1px solid var(--color-border)', borderRadius: 6, overflow: 'hidden' }}>
        {SCOPE_OPTS.map(o => (
          <button key={o.value} title={o.value === 'none' ? '平台自有 Agent 全包' : o.value === 'coding_only' ? '仅 P4 委托外部平台' : '全部环节委托外部平台(高风险)'}
            onClick={() => onChange(o.value)}
            style={{
              fontSize: 11, padding: '2px 7px', border: 'none', cursor: 'pointer',
              background: val === o.value ? 'var(--color-primary-soft)' : 'transparent',
              color: val === o.value ? 'var(--color-primary)' : 'var(--color-text-muted)',
              borderRight: o.value !== 'all_stages' ? '1px solid var(--color-border)' : 'none',
            }}>{o.label}</button>
        ))}
      </span>
      {needsAgent && !hasAgent && (
        <span style={{ fontSize: 10, color: 'var(--amber)' }} title="请先在上方选择一个外部编程平台,否则委托不生效">需选平台</span>
      )}
    </span>
  );
}

