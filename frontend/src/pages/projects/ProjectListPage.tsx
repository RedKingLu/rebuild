import { useProjectStore } from '../../stores';
import { useNavigate } from 'react-router-dom';
import { STAGE_LABELS } from '../../types';

export function ProjectListPage() {
  const projects = useProjectStore(s => s.projects);
  const nav = useNavigate();
  const openWs = (pid: string) => window.open(`/projects/${pid}/workspace`, '_blank');

  return (
    <div>
      <div className="spread"><h1>项目</h1><button className="btn" onClick={() => nav('/projects/new')}>＋ 新建项目</button></div>
      <p className="sub">项目对象绑定独立 Project Workspace；创建后经首次引导进入工作区。点击"进入工作区"在新标签页打开。</p>
      <span className="tag violet" style={{ marginBottom: 12 }}>Mock 数据</span>

      {!projects.length && <div className="empty">暂无项目</div>}

      {/* Wide bar design */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {projects.map(p => (
          <div key={p.project_id} className="card" style={{ display: 'flex', alignItems: 'center', gap: 20, padding: '16px 20px' }}>
            {/* Left: project info */}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="spread" style={{ marginBottom: 4 }}>
                <b style={{ fontSize: 15 }}>{p.name}</b>
                <span className="tag">{p.project_status}</span>
              </div>
              <div className="hash" style={{ marginBottom: 4 }}>{p.description || '（无描述）'}</div>
              <div className="row" style={{ fontSize: 12, color: 'var(--ink-2)', gap: 12 }}>
                <span>来源: {p.source_type}</span>
                <span>阶段: {p.current_stage ? STAGE_LABELS[p.current_stage as keyof typeof STAGE_LABELS] || p.current_stage : '未开始'}</span>
                <span>缺口: {p.evidence_gap_count}</span>
                <span>更新: {p.updated_at?.slice(0, 10)}</span>
                {p.active_gate && <span className="tag amber">⚠ Gate: {p.active_gate}</span>}
              </div>
            </div>
            {/* Right: actions — Runs/设置 等深度交互在工作区内（D-046），列表只保留展示与入口 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
              {!p.onboarding_done && <span className="tag amber">待引导</span>}
              <button className="btn sm ghost" onClick={() => nav(`/projects/${p.project_id}`)}>详情</button>
              <button className="btn sm" onClick={() => openWs(p.project_id)}>进入工作区</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
