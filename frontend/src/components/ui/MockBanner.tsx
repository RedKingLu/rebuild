import { useLocation } from 'react-router-dom';
import { useWorkspaceStore } from '../../stores';

// 已真实接入后端的路由前缀——这些页面不显示「体验壳/mock」横幅（C-5：诚实边界 D-049）。
const REAL_ROUTES = ['/models', '/resources', '/integrations', '/projects', '/'];

export function GlobalMockBanner() {
  const dismissed = useWorkspaceStore(s => s.mockBannerDismissed);
  const dismiss = useWorkspaceStore(s => s.dismissMockBanner);
  const { pathname } = useLocation();
  // 真实页面不挂 mock 横幅
  if (REAL_ROUTES.some(r => pathname === r || (r !== '/' && pathname.startsWith(r)))) return null;
  if (dismissed) return null;
  return (
    <div className="banner mock-banner" style={{ borderRadius: 0, borderLeft: 0, borderRight: 0, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
      <span>⚠ 当前为前端体验壳（R6），部分页面为静态演示 [mock]；模型/供应商/资源/集成/项目/概览页已真实接入后端。</span>
      <button className="btn sm ghost" onClick={dismiss} style={{ flexShrink: 0 }}>✕</button>
    </div>
  );
}
