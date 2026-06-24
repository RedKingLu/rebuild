import { useNavigate } from 'react-router-dom';

const CATEGORIES = [
  { key: 'agents', label: 'Agents', count: 0 },
  { key: 'skills', label: 'Skills', count: 0 },
  { key: 'rules', label: 'Rules', count: 0 },
  { key: 'hooks', label: 'Hooks', count: 0 },
  { key: 'commands', label: 'Commands', count: 0 },
  { key: 'contexts', label: 'Contexts', count: 0 },
  { key: 'mcp', label: 'MCP', count: 0 },
  { key: 'tools', label: 'Tools', count: 0 },
  { key: 'knowledge', label: 'Knowledge', count: 0 },
  { key: 'eps', label: 'ExecutionProviders', count: 0 },
];

export function ResourcesPage() {
  const nav = useNavigate();
  // Derive active tab from URL path: /resources/:type
  const pathSeg = typeof window !== 'undefined' ? window.location.pathname.split('/').pop() : '';
  const activeTab = pathSeg && CATEGORIES.some(c => c.key === pathSeg) ? pathSeg : 'agents';

  return (
    <div>
      <div className="spread">
        <h1>资源中心</h1>
        <button className="btn sm">＋ 新建</button>
      </div>
      <p className="sub">Agent Harness 信息架构（R6 施工）。Skill 三分；Case 永远无执行权。</p>
      <span className="tag placeholder-tag" style={{ marginBottom: 12 }}>占位</span>
      <div className="row" style={{ marginBottom: 16, flexWrap: 'wrap' }}>
        {CATEGORIES.map(c => (
          <button key={c.key} className={`btn sm ${activeTab === c.key ? '' : 'ghost'}`}
            onClick={() => nav(`/resources/${c.key}`)}>{c.label}（{c.count}）</button>
        ))}
      </div>
      <div className="card">
        <div className="empty">
          <b>{CATEGORIES.find(c => c.key === activeTab)?.label || activeTab}</b>
          <p className="sub" style={{ marginTop: 8 }}>该分类暂无条目。资源管理将在 R6（Agent / Skill / 资源基础）阶段施工。</p>
          <span className="tag placeholder-tag">占位</span>
        </div>
      </div>
    </div>
  );
}
