import { useLocation, useNavigate } from 'react-router-dom';
import { useSettingsStore } from '../../stores';
import { Icon, type IconKey } from '../ui/Icon';

const NAV: { group: string; items: [string, string, IconKey][] }[] = [
  { group: '工作台', items: [['overview', '概览', 'overview'], ['projects', '项目', 'project']] },
  { group: '能力', items: [['resources', '资源', 'resource'], ['integrations', '集成', 'integration'], ['models', '模型', 'model'], ['fusion', '聚合', 'fusion']] },
  { group: '知识与社区', items: [['cases', '案例', 'case'], ['knowledge', '知识', 'knowledge'], ['community', '社区', 'community'], ['docs', '文档', 'docs']] },
  { group: '系统', items: [['settings', '设置', 'settings']] },
];

export function MainNav() {
  const { pathname } = useLocation();
  const nav = useNavigate();
  const theme = useSettingsStore(s => s.theme);
  const setTheme = useSettingsStore(s => s.setTheme);
  const collapsed = useSettingsStore(s => s.collapsed);
  const toggleCollapsed = useSettingsStore(s => s.toggleCollapsed);
  const active = pathname === '/' ? 'overview' : pathname.slice(1).split('/')[0];

  return (
    <aside className="sidebar" style={collapsed ? { width: 'var(--sidebar-collapsed-width)' as any, minWidth: 'var(--sidebar-collapsed-width)' as any } : undefined}>
      {/* Brand */}
      <div className="brand" style={{ padding: collapsed ? '16px 0' : '16px', justifyContent: collapsed ? 'center' : undefined }}>
        {collapsed ? (
          <img src="/logo-icon.svg" alt="rebuild" style={{ width: 32, height: 32, cursor: 'pointer' }} onClick={toggleCollapsed} />
        ) : (
          <>
            <img src="/logo-icon.svg" alt="" style={{ width: 32, height: 32, cursor: 'pointer', flexShrink: 0 }} onClick={toggleCollapsed} />
            <div className="who">rebuild<small>软件重构与迁移平台</small></div>
            <button className="btn sm ghost" style={{ marginLeft: 'auto', fontSize: 16, padding: '0 6px', height: 28 }} onClick={toggleCollapsed}>≡</button>
          </>
        )}
      </div>

      {/* Nav items */}
      <nav className="nav" style={{ padding: collapsed ? '4px' : '8px' }}>
        {NAV.map(g => (
          <div key={g.group}>
            {!collapsed && <div className="group">{g.group}</div>}
            {g.items.map(([k, label, icon]) => {
              const isActive = active === k;
              return (
                <button key={k} className={`item${isActive ? ' active' : ''}`}
                  title={collapsed ? label : undefined}
                  style={collapsed ? { justifyContent: 'center', padding: '8px 0' } : undefined}
                  onClick={() => {
                    if (k === 'community' || k === 'docs') { window.open('/' + k, '_blank'); return; }
                    nav(k === 'overview' ? '/' : '/' + k);
                  }}>
                  <span className="ic" style={isActive ? { color: 'var(--color-primary)' } : {}}><Icon name={icon} size={20} /></span>
                  {!collapsed && <span className="lbl">{label}</span>}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="brand" style={{ borderTop: '1px solid var(--line)', borderBottom: 'none', marginTop: 'auto', padding: collapsed ? '12px 0' : '12px 16px', justifyContent: collapsed ? 'center' : undefined }}>
        {!collapsed && <span style={{ fontSize: 11, color: 'var(--ink-3)' }}>凭证据·可审计</span>}
        <button className="btn sm ghost" style={{ marginLeft: collapsed ? 0 : 'auto', padding: '0 6px', fontSize: 16, height: 28 }} onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>
          {theme === 'light' ? '☾' : '☀'}
        </button>
      </div>
    </aside>
  );
}
