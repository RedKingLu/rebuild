import { useWorkspaceStore } from '../../stores';

export function GlobalMockBanner() {
  const dismissed = useWorkspaceStore(s => s.mockBannerDismissed);
  const dismiss = useWorkspaceStore(s => s.dismissMockBanner);
  if (dismissed) return null;
  return (
    <div className="banner mock-banner" style={{ borderRadius: 0, borderLeft: 0, borderRight: 0, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
      <span>⚠ 当前为前端体验壳（R3），所有数据均为静态演示 [mock]，未接入真实后端服务。</span>
      <button className="btn sm ghost" onClick={dismiss} style={{ flexShrink: 0 }}>✕</button>
    </div>
  );
}
