import { create } from 'zustand';

// D-097：本文件不再引入任何 mock 数据。业务数据（Project / Run / Gate / Artifact /
// Evidence / Trace / Audit / File）一律由各页面 / 组件直接调用真实 service（API）获取，
// 不经此处的全局 mock store。以下仅保留纯前端 UI 状态 store（Workspace 布局、设置），
// 它们不承载任何业务数据初值。

// === Workspace UI Store ===
export type ActivityType = 'stage' | 'files' | 'materials' | 'git' | 'remote' | 'search' | 'agent';
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
  // UX-3: active agent conversation id (persisted session).
  activeConversationId: string | null;
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
  setActiveConversation: (id: string | null) => void;
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
  activeConversationId: null,
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
  setActiveConversation: (id) => set({ activeConversationId: id }),
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
