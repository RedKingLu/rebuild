import { useEffect, useState } from 'react';
import { useProjectStore } from '../../stores';
import { useNavigate } from 'react-router-dom';
import { STAGE_LABELS } from '../../types';
import type { Project } from '../../types';
import { fetchProjectsWithSource } from '../../services/projectService';

// R4 最小前后端联调（C-B）：本页在挂载时真实调用后端 GET /api/projects，
// 成功则展示后端数据并标注 source_status；后端不可用时优雅回退到本地 mock，
// 页面不崩溃。刻意使用 page-local state（非 store selector），规避 R3-4 的
// React19 选择器新引用无限渲染问题。
type ConnState = 'loading' | 'connected' | 'fallback';

export function ProjectListPage() {
  const mockProjects = useProjectStore(s => s.projects);
  const [projects, setProjects] = useState<Project[]>(mockProjects);
  const [conn, setConn] = useState<ConnState>('loading');
  const [sourceStatus, setSourceStatus] = useState<string>('');
  const nav = useNavigate();
  const openWs = (pid: string) => window.open(`/projects/${pid}/workspace`, '_blank');

  useEffect(() => {
    let active = true;
    fetchProjectsWithSource()
      .then(({ projects: list, sourceStatus: src }) => {
        if (!active) return;
        setProjects(list);
        setSourceStatus(src);
        setConn('connected');
      })
      .catch(() => {
        if (!active) return;
        setProjects(mockProjects);
        setConn('fallback');
      });
    return () => { active = false; };
    // 仅挂载时拉取一次；mockProjects 为 store 稳定引用，作为回退基线。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sourceTag =
    conn === 'loading' ? <span className="tag">加载中…</span>
    : conn === 'connected'
      ? <span className="tag violet">后端接口数据 · /api/projects（source_status: {sourceStatus}）</span>
      : <span className="tag amber">后端未连接 · 显示本地 mock 回退</span>;

  return (
    <div>
      <div className="spread"><h1>项目</h1><button className="btn" onClick={() => nav('/projects/new')}>＋ 新建项目</button></div>
      <p className="sub">项目对象绑定独立 Project Workspace；创建后经首次引导进入工作区。点击"进入工作区"在新标签页打开。</p>
      <div style={{ marginBottom: 12 }}>{sourceTag}</div>

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
