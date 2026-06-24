import { useSettingsStore } from '../../stores';

export function SettingsPage() {
  const theme = useSettingsStore(s => s.theme);
  const setTheme = useSettingsStore(s => s.setTheme);

  return (
    <div>
      <h1>设置</h1>
      <p className="sub">平台配置、安全、偏好。</p>
      <span className="tag violet" style={{ marginBottom: 12 }}>Mock（部分可用）</span>
      <div className="card" style={{ marginBottom: 14 }}>
        <b>主题</b>
        <div className="row" style={{ marginTop: 8 }}>
          <button className={`btn sm ${theme === 'light' ? '' : 'ghost'}`} onClick={() => setTheme('light')}>浅色</button>
          <button className={`btn sm ${theme === 'dark' ? '' : 'ghost'}`} onClick={() => setTheme('dark')}>深色</button>
        </div>
      </div>
      <div className="card" style={{ marginBottom: 14 }}>
        <b>执行安全 · 自动模式分级（AUTO-L0~L5）</b>
        <div style={{ marginTop: 8, fontSize: 13 }}>
          {['L0 只读自动', 'L1 生成产物不写工作区', 'L2 写临时目录', 'L3 写工作区需确认/预授权', 'L4 shell·测试需白名单+审计', 'L5 网络·删除·大改默认禁止'].map(l => (
            <div key={l} className="hash" style={{ padding: '3px 0' }}>{l}</div>
          ))}
        </div>
        <span className="tag violet" style={{ marginTop: 8 }}>Mock</span>
      </div>
      <div className="cardgrid">
        <div className="card"><div className="spread"><b>Hook：写盘前 Policy 检查</b><span className="tag green">Mock 启用</span></div></div>
        <div className="card"><div className="spread"><b>记忆策略</b><span className="tag grey">默认关</span></div></div>
        <div className="card"><div className="spread"><b>安全扫描策略</b><span className="tag amber">占位</span></div></div>
        <div className="card"><div className="spread"><b>外部 Agent 自我批准</b><span className="tag red">禁止</span></div></div>
      </div>
      <div className="card" style={{ marginTop: 14 }}>
        <b>关于</b>
        <div className="hash" style={{ marginTop: 6 }}>平台：rebuild · 版本：V26.1.1 · 当前阶段：R3 前端体验壳</div>
        <span className="tag violet" style={{ marginTop: 4 }}>凭证据·可审计</span>
      </div>
    </div>
  );
}
