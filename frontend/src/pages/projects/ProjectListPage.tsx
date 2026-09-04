import { useEffect, useState } from 'react';
import { Icon } from '../../components/ui/Icon';
import { useNavigate } from 'react-router-dom';
import { STAGE_LABELS } from '../../types';
import type { Project } from '../../types';
import { fetchProjectsWithSource, updateProject, deleteProject } from '../../services/projectService';

// R4 最小前后端联调（C-B）：本页挂载时真实调用后端 GET /api/projects。
// R9-5-8 T13/公理1：后端不可用时显示「后端未连接」错误态 + 重试，
// 绝不回退到本地 mock 冒充真实列表（消除“看起来有数据”的假象）。
type ConnState = 'loading' | 'connected' | 'error';

export function ProjectListPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [conn, setConn] = useState<ConnState>('loading');
  const [sourceStatus, setSourceStatus] = useState<string>('');
  const nav = useNavigate();
  const openWs = (pid: string) => window.open(`/projects/${pid}/workspace`, '_blank');

  // ── Edit modal state ────────────────────────────────────────────────
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState('');
  const [editDesc, setEditDesc] = useState('');

  // ── Delete confirm state ────────────────────────────────────────────
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deletingName, setDeletingName] = useState('');

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
        setProjects([]);
        setConn('error');
      });
    return () => { active = false; };
    // 仅挂载时拉取一次。后端不可用→错误态，不回退 mock（公理1）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshProjects = () => {
    fetchProjectsWithSource()
      .then(({ projects: list, sourceStatus: src }) => {
        setProjects(list);
        setSourceStatus(src);
        setConn('connected');
      })
      .catch(() => {
        setProjects([]);
        setConn('error');
      });
  };

  const openEdit = (p: Project) => {
    setEditingId(p.project_id);
    setEditName(p.name);
    setEditDesc(p.description || '');
  };

  const closeEdit = () => {
    setEditingId(null);
  };

  const handleSave = async () => {
    if (!editingId) return;
    try {
      await updateProject(editingId, { name: editName, description: editDesc });
      closeEdit();
      refreshProjects();
    } catch (e) {
      alert('保存失败：' + (e as Error).message);
    }
  };

  const openDelete = (p: Project) => {
    setDeletingId(p.project_id);
    setDeletingName(p.name);
  };

  const closeDelete = () => {
    setDeletingId(null);
  };

  const handleDelete = async () => {
    if (!deletingId) return;
    try {
      await deleteProject(deletingId);
      closeDelete();
      refreshProjects();
    } catch (e) {
      alert('删除失败：' + (e as Error).message);
    }
  };

  const sourceTag =
    conn === 'loading' ? <span className="tag">加载中…</span>
    : conn === 'connected'
      ? <span className="tag violet">后端接口数据 · /api/projects（source_status: {sourceStatus}）</span>
      : <>
          <span className="tag red">后端未连接 · 无法加载项目</span>
          <button className="btn sm ghost" style={{ marginLeft: 8 }} onClick={refreshProjects}>重试</button>
        </>;

  // ── Inline modal overlay styles (CSS variables, no separate component) ──
  const overlay: React.CSSProperties = {
    position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
    background: 'rgba(0,0,0,0.5)', display: 'flex',
    alignItems: 'center', justifyContent: 'center', zIndex: 1000,
  };

  const modalCard: React.CSSProperties = {
    background: 'var(--surface, #fff)', borderRadius: 12,
    padding: 24, minWidth: 360, maxWidth: 480, boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
  };

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '8px 12px', borderRadius: 6,
    border: '1px solid var(--border, #d1d5db)', fontSize: 14,
    outline: 'none', marginBottom: 12, boxSizing: 'border-box',
    background: 'var(--input-bg, #fff)', color: 'var(--ink, #111)',
  };

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
                {/* R19-3-04：workspace 状态标识改 Icon.tsx */}
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>状态:
                  {p.workspace_status === 'importing'
                    ? <><Icon name="refresh" size={12} style={{ color: 'var(--amber)' }} />导入中…</>
                    : p.workspace_status === 'ready'
                      ? <><Icon name="success" size={12} style={{ color: 'var(--green)' }} />已就绪</>
                      : (p.workspace_status || '—')}
                </span>
                <span>缺口: {p.evidence_gap_count}</span>
                <span>更新: {p.updated_at?.slice(0, 10)}</span>
                {p.active_gate && <span className="tag amber">⚠ Gate: {p.active_gate}</span>}
              </div>
            </div>
            {/* Right: actions */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
              {p.workspace_status === 'importing' && (
                <span className="tag amber" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                  <Icon name="refresh" size={12} />导入中</span>
              )}
              {!p.onboarding_done && p.workspace_status === 'ready' && <span className="tag amber">待引导</span>}
              <button className="btn sm ghost" onClick={() => nav(`/projects/${p.project_id}`)}>详情</button>
              <button className="btn sm ghost" onClick={() => openEdit(p)}>编辑</button>
              <button className="btn sm ghost" onClick={() => openDelete(p)} style={{ color: 'var(--red, #dc2626)' }}>删除</button>
              <button className="btn sm" onClick={() => openWs(p.project_id)}
                disabled={p.workspace_status === 'importing'}
                title={p.workspace_status === 'importing' ? '源码导入中，请稍候…' : '进入工作区'}>
                {p.workspace_status === 'importing' ? '导入中…' : '进入工作区'}
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* ── Edit Modal ────────────────────────────────────────────────── */}
      {editingId && (
        <div style={overlay} onClick={closeEdit}>
          <div style={modalCard} onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 16px 0', fontSize: 16 }}>编辑项目</h3>
            <label style={{ fontSize: 13, color: 'var(--ink-2)', display: 'block', marginBottom: 4 }}>名称</label>
            <input style={inputStyle} value={editName} onChange={e => setEditName(e.target.value)} placeholder="项目名称" />
            <label style={{ fontSize: 13, color: 'var(--ink-2)', display: 'block', marginBottom: 4 }}>描述</label>
            <input style={inputStyle} value={editDesc} onChange={e => setEditDesc(e.target.value)} placeholder="项目描述" />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 8 }}>
              <button className="btn sm ghost" onClick={closeEdit}>取消</button>
              <button className="btn sm" onClick={handleSave}>保存</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Delete Confirm Modal ──────────────────────────────────────── */}
      {deletingId && (
        <div style={overlay} onClick={closeDelete}>
          <div style={modalCard} onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 12px 0', fontSize: 16 }}>确认删除</h3>
            <p style={{ margin: '0 0 20px 0', color: 'var(--ink-2)', lineHeight: 1.5 }}>
              确定删除项目 {deletingName}？此操作不可撤销。
            </p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn sm ghost" onClick={closeDelete}>取消</button>
              <button className="btn sm" onClick={handleDelete} style={{ background: 'var(--red, #dc2626)', color: '#fff', borderColor: 'var(--red, #dc2626)' }}>确认删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
