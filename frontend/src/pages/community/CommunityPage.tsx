import { useState } from 'react';

const TABS = ['全部', '案例', '工具', '模板', '知识'];

const CARDS = [
  { title: '.NET → 信创迁移案例', tab: '案例', desc: '某政务系统从 .NET Framework 迁移至信创环境的完整记录（规划中）', stars: 0 },
  { title: 'Oracle → 达梦迁移模板', tab: '模板', desc: '数据库迁移标准作业模板，含 DDL 转换与数据校验（规划中）', stars: 0 },
  { title: 'WebLogic → TongWeb 工具', tab: '工具', desc: 'Java EE 应用服务器迁移辅助脚本集（规划中）', stars: 0 },
  { title: '信创中间件兼容性知识', tab: '知识', desc: '国产中间件与常见框架的兼容性矩阵参考（规划中）', stars: 0 },
  { title: 'Vue2 → Vue3 迁移指南', tab: '案例', desc: '前端框架升级迁移路径与常见坑点（规划中）', stars: 0 },
  { title: '信创安全合规清单', tab: '知识', desc: '等保/密评/信创目录对照参考（规划中）', stars: 0 },
];

export function CommunityPage() {
  const [tab, setTab] = useState('全部');
  const [search, setSearch] = useState('');

  const filtered = CARDS.filter(c => {
    if (tab !== '全部' && c.tab !== tab) return false;
    if (search && !c.title.includes(search) && !c.desc.includes(search)) return false;
    return true;
  });

  return (
    <div style={{ minHeight: '100vh', background: '#0d1117', color: '#e6edf3', fontFamily: 'var(--sans)' }}>
      {/* Header */}
      <header style={{ background: '#161b22', borderBottom: '1px solid #30363d', padding: '14px 24px', display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap', position: 'sticky', top: 0, zIndex: 100 }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: '#58a6ff', whiteSpace: 'nowrap' }}>rebuild <span style={{ color: '#e6edf3', fontWeight: 400 }}>社区</span></div>
        <div style={{ flex: 1, minWidth: 200, maxWidth: 480, position: 'relative' }}>
          <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#6e7681', fontSize: 14 }}>⌕</span>
          <input
            value={search} onChange={e => setSearch(e.target.value)}
            placeholder="搜索案例、工具、模板、知识…"
            style={{ width: '100%', padding: '8px 12px 8px 36px', borderRadius: 8, border: '1px solid #30363d', background: '#0d1117', color: '#e6edf3', fontSize: 14, outline: 'none', fontFamily: 'var(--sans)' }}
          />
        </div>
        <span style={{ fontSize: 12, color: '#6e7681' }}>信创迁移资源中心 · R3 Mock</span>
      </header>

      {/* Tabs */}
      <nav style={{ background: '#161b22', borderBottom: '1px solid #30363d', padding: '0 24px', display: 'flex', gap: 0, overflowX: 'auto' }}>
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)}
            style={{ padding: '12px 20px', fontSize: 14, border: 'none', background: 'transparent', color: tab === t ? '#e6edf3' : '#8b949e', cursor: 'pointer', borderBottom: tab === t ? '2px solid #f0883e' : '2px solid transparent', whiteSpace: 'nowrap', fontFamily: 'var(--sans)' }}>
            {t}
          </button>
        ))}
      </nav>

      {/* Content */}
      <main style={{ maxWidth: 960, margin: '0 auto', padding: '24px' }}>
        <div style={{ marginBottom: 20 }}>
          <h2 style={{ fontSize: 20, fontWeight: 600 }}>探索社区资源</h2>
          <p style={{ fontSize: 13, color: '#8b949e', marginTop: 4 }}>以下为静态演示内容，社区功能将在 R15 阶段正式建设。社区资源默认只读参考（D-061）。</p>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {filtered.map(c => (
            <div key={c.title} style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8, padding: '14px 18px', display: 'flex', alignItems: 'flex-start', gap: 14 }}>
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                  <b style={{ fontSize: 14, color: '#58a6ff' }}>{c.title}</b>
                  <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4, background: '#21262d', color: '#8b949e' }}>{c.tab}</span>
                </div>
                <div style={{ fontSize: 13, color: '#8b949e', marginBottom: 6 }}>{c.desc}</div>
                <div style={{ display: 'flex', gap: 12, fontSize: 12, color: '#6e7681' }}>
                  <span>★ {c.stars}</span>
                  <span style={{ color: '#d2991d' }}>规划中</span>
                </div>
              </div>
            </div>
          ))}
          {!filtered.length && <div style={{ textAlign: 'center', padding: 32, color: '#6e7681', fontSize: 13 }}>无匹配结果</div>}
        </div>

        <footer style={{ marginTop: 32, padding: '20px 0', borderTop: '1px solid #30363d', fontSize: 12, color: '#6e7681', textAlign: 'center' }}>
          rebuild 社区 · 当前版本 V26.1.1（R17 前端体验壳）· 正式社区功能 R15 建设
        </footer>
      </main>
    </div>
  );
}
