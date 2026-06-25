import { create } from 'zustand';
import { mockProjects, mockRuns, mockGates, mockArtifacts, mockEvidences, mockTraces, mockAudits, mockFileTree } from '../mock/data';
import type { Project, Run, Gate, Artifact, Evidence, Trace, Audit, FileRoot } from '../types';

// === Pre-computed stable lookups (created once, never change) ===
const EMPTY_RUNS: Run[] = [];
const EMPTY_GATES: Gate[] = [];
const runsByProject = new Map<string, Run[]>();
const gatesByProject = new Map<string, Gate[]>();
const gapsList: Evidence[] = [];
for (const r of mockRuns) {
  const arr = runsByProject.get(r.project_id) || [];
  arr.push(r);
  runsByProject.set(r.project_id, arr);
}
for (const g of mockGates) {
  const arr = gatesByProject.get(g.project_id) || [];
  arr.push(g);
  gatesByProject.set(g.project_id, arr);
}
for (const e of mockEvidences) {
  if (e.blocking || e.evidence_status === 'insufficient') gapsList.push(e);
}

// === Project Store ===
interface ProjectState {
  projects: Project[];
  getProject: (id: string) => Project | undefined;
  createProject: (name: string) => Project;
}
export const useProjectStore = create<ProjectState>((set, get) => ({
  projects: mockProjects,
  getProject: (id) => get().projects.find(p => p.project_id === id),
  createProject: (name) => {
    const p: Project = {
      project_id: 'proj-' + Date.now(),
      name,
      description: '',
      project_status: 'created',
      current_stage: null,
      current_run_id: null,
      active_gate: null,
      evidence_gap_count: 0,
      source_type: 'local_dir',
      workspace_status: 'ready',
      updated_at: new Date().toISOString(),
      onboarding_done: false,
      mock_level: 'mock',
    };
    set(s => ({ projects: [...s.projects, p] }));
    return p;
  },
}));

// === Run Store ===
interface RunState {
  runs: Run[];
  /** Stable — same array reference for same projectId */
  getRunsByProject: (projectId: string) => Run[];
  getRun: (runId: string) => Run | undefined;
}
export const useRunStore = create<RunState>(() => ({
  runs: mockRuns,
  getRunsByProject: (pid) => runsByProject.get(pid) || EMPTY_RUNS,
  getRun: (rid) => mockRuns.find(r => r.run_id === rid),
}));

// === Gate Store ===
interface GateState {
  gates: Gate[];
  /** Stable — same array reference for same projectId */
  getGatesByProject: (projectId: string) => Gate[];
  /** Stable — returns same object ref for same projectId */
  getActiveGate: (projectId: string) => Gate | undefined;
}
export const useGateStore = create<GateState>(() => ({
  gates: mockGates,
  getGatesByProject: (pid) => gatesByProject.get(pid) || EMPTY_GATES,
  getActiveGate: (pid) => (gatesByProject.get(pid) || []).find(g => g.gate_status === 'waiting_decision'),
}));

// === Artifact Store ===
interface ArtifactState { artifacts: Artifact[]; }
export const useArtifactStore = create<ArtifactState>(() => ({ artifacts: mockArtifacts }));

// === Evidence Store ===
interface EvidenceState {
  evidences: Evidence[];
  /** Stable — pre-computed once */
  gaps: Evidence[];
}
export const useEvidenceStore = create<EvidenceState>(() => ({
  evidences: mockEvidences,
  gaps: gapsList,
}));

// === Trace Store ===
interface TraceState { traces: Trace[]; }
export const useTraceStore = create<TraceState>(() => ({ traces: mockTraces }));

// === Audit Store ===
interface AuditState { audits: Audit[]; }
export const useAuditStore = create<AuditState>(() => ({ audits: mockAudits }));

// === Workspace UI Store ===
export type ActivityType = 'stage' | 'files' | 'git' | 'remote' | 'search' | 'agent';
export type RightTab = 'gate' | 'evidence' | 'trace' | 'audit' | 'context' | 'resource' | 'model';
export type CenterTabKind = 'agent' | 'stage' | 'file' | 'artifact';
/** D-025 阶段内部执行模式（不改变 P 阶段晋级 Gate 必须人工授权） */
export type ExecMode = 'manual' | 'plan' | 'auto';

export interface CenterTab { id: string; title: string; kind: CenterTabKind; data?: unknown; closable: boolean; }

interface WorkspaceState {
  projectId: string | null;
  activity: ActivityType;
  leftCollapsed: boolean;
  rightOpen: boolean;
  rightTab: RightTab;
  bottomOpen: boolean;
  execMode: ExecMode;
  tabs: CenterTab[];
  activeTab: string;
  mockBannerDismissed: boolean;
  setProjectId: (id: string) => void;
  setActivity: (a: ActivityType) => void;
  toggleLeft: () => void;
  setRightOpen: (o: boolean) => void;
  setRightTab: (t: RightTab) => void;
  setBottomOpen: (o: boolean) => void;
  setExecMode: (m: ExecMode) => void;
  openTab: (t: CenterTab) => void;
  closeTab: (id: string) => void;
  setActiveTab: (id: string) => void;
  dismissMockBanner: () => void;
}
export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  projectId: null,
  activity: 'stage',
  leftCollapsed: false,
  rightOpen: true,
  rightTab: 'evidence',
  bottomOpen: false,
  execMode: 'plan',
  tabs: [{ id: 'agent', title: 'Agent 对话', kind: 'agent', closable: false }],
  activeTab: 'agent',
  mockBannerDismissed: false,
  setProjectId: (id) => set({ projectId: id }),
  setActivity: (a) => set(s => ({ activity: a, leftCollapsed: s.activity === a ? !s.leftCollapsed : false })),
  toggleLeft: () => set(s => ({ leftCollapsed: !s.leftCollapsed })),
  setRightOpen: (o) => set({ rightOpen: o }),
  setRightTab: (t) => set({ rightTab: t, rightOpen: true }),
  setBottomOpen: (o) => set({ bottomOpen: o }),
  setExecMode: (m) => set({ execMode: m }),
  openTab: (t) => set(s => {
    const exists = s.tabs.find(x => x.id === t.id);
    return { tabs: exists ? s.tabs.map(x => x.id === t.id ? t : x) : [...s.tabs, t], activeTab: t.id };
  }),
  closeTab: (id) => set(s => ({ tabs: s.tabs.filter(x => x.id !== id), activeTab: s.activeTab === id ? 'agent' : s.activeTab })),
  setActiveTab: (id) => set({ activeTab: id }),
  dismissMockBanner: () => set({ mockBannerDismissed: true }),
}));

// === Settings Store ===
// 字号通过整体界面缩放实现（现有样式多为 px 内联，故用根 zoom 全局缩放，
// 既改变字号也按比例缩放间距/图标，是最稳健的全局"字号"控制）。持久化到 localStorage。
const FONT_SCALE_KEY = 'rb_font_scale';
function readFontScale(): number {
  if (typeof localStorage === 'undefined') return 1;
  const v = parseFloat(localStorage.getItem(FONT_SCALE_KEY) || '1');
  return Number.isFinite(v) && v >= 0.8 && v <= 1.4 ? v : 1;
}
function applyFontScale(n: number) {
  if (typeof document !== 'undefined') {
    // zoom 在 Chromium/WebKit/新版 Firefox 均支持；缩放整个界面字号
    (document.documentElement.style as CSSStyleDeclaration & { zoom?: string }).zoom = String(n);
  }
}

interface SettingsState {
  theme: 'light' | 'dark';
  collapsed: boolean;
  fontScale: number; // 0.9 小 / 1.0 标准 / 1.1 大 / 1.25 特大
  setTheme: (t: 'light' | 'dark') => void;
  toggleCollapsed: () => void;
  setFontScale: (n: number) => void;
}
export const useSettingsStore = create<SettingsState>((set) => ({
  theme: 'light',
  collapsed: false,
  fontScale: readFontScale(),
  setTheme: (t) => { document.documentElement.setAttribute('data-theme', t); set({ theme: t }); },
  toggleCollapsed: () => set(s => ({ collapsed: !s.collapsed })),
  setFontScale: (n) => {
    applyFontScale(n);
    if (typeof localStorage !== 'undefined') localStorage.setItem(FONT_SCALE_KEY, String(n));
    set({ fontScale: n });
  },
}));

// 启动即应用已保存字号
applyFontScale(readFontScale());

// === File Store ===
interface FileState { tree: FileRoot[]; }
export const useFileStore = create<FileState>(() => ({ tree: mockFileTree }));
