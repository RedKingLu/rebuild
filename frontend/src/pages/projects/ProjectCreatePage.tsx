import { useState, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { createProject } from '../../services/projectService';
import {
  listGitAccounts, listAccountRepos,
  type GitAccountInfo, type GitRepoInfo,
} from '../../services/integrationService';

type SourceMode = 'zip' | 'git';

export function ProjectCreatePage() {
  const nav = useNavigate();
  const [searchParams] = useSearchParams();

  // Pre-fill from URL params (when coming from Integrations page)
  const prefillName = searchParams.get('name') || '';
  const prefillRepo = searchParams.get('repo') || '';
  const prefillBranch = searchParams.get('branch') || 'main';

  const [name, setName] = useState(prefillName);
  const [desc, setDesc] = useState('');
  const [mode, setMode] = useState<SourceMode>('zip');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [createdId, setCreatedId] = useState<string | null>(null);

  // ZIP state
  const [zipFile, setZipFile] = useState<File | null>(null);

  // Git OAuth state
  const [accounts, setAccounts] = useState<GitAccountInfo[]>([]);
  const [acctsLoading, setAcctsLoading] = useState(false);
  const [selectedAccount, setSelectedAccount] = useState('');
  const [repos, setRepos] = useState<GitRepoInfo[]>([]);
  const [reposLoading, setReposLoading] = useState(false);
  const [selectedRepo, setSelectedRepo] = useState(prefillRepo);
  const [gitBranch, setGitBranch] = useState(prefillBranch);

  // Load accounts when entering git mode
  useEffect(() => {
    if (mode === 'git') {
      setAcctsLoading(true);
      listGitAccounts()
        .then(a => { setAccounts(a); if (a.length === 1) setSelectedAccount(a[0].account_id); })
        .catch(() => {})
        .finally(() => setAcctsLoading(false));
    }
  }, [mode]);

  // Load repos when account is selected
  useEffect(() => {
    if (!selectedAccount) { setRepos([]); return; }
    setReposLoading(true);
    setRepos([]);
    listAccountRepos(selectedAccount)
      .then(r => setRepos(r))
      .catch(() => {})
      .finally(() => setReposLoading(false));
  }, [selectedAccount]);

  // Auto-select prefill repo
  useEffect(() => {
    if (prefillRepo && repos.length > 0) {
      const match = repos.find(r => r.clone_url === prefillRepo || r.ssh_url === prefillRepo);
      if (match && !selectedRepo) setSelectedRepo(match.clone_url);
    }
  }, [repos, prefillRepo]);

  const handleCreate = async () => {
    if (!name.trim()) { setErr('请填写项目名称'); return; }
    if (mode === 'zip' && !zipFile) { setErr('请选择 ZIP 文件'); return; }
    if (mode === 'git' && (!selectedAccount || !selectedRepo)) {
      setErr('请选择 Git 账号和仓库'); return;
    }
    setErr(''); setLoading(true);

    try {
      let project: { project_id: string };

      if (mode === 'zip') {
        const form = new FormData();
        form.append('name', name.trim());
        form.append('description', desc.trim());
        form.append('file', zipFile!);
        const resp = await fetch('/api/projects/upload', { method: 'POST', body: form });
        if (!resp.ok) {
          const data = await resp.json().catch(() => ({}));
          throw new Error((data as any)?.detail || `上传失败 (${resp.status})`);
        }
        project = (await resp.json()).data;
      } else {
        const repo = repos.find(r => r.clone_url === selectedRepo || r.ssh_url === selectedRepo);
        project = await createProject({
          name: name.trim(),
          description: desc.trim(),
          source_type: 'git',
          source_config: {
            clone_url: selectedRepo,
            ssh_url: repo?.ssh_url || '',
            branch: gitBranch,
            repo_name: repo?.full_name || repo?.name || '',
            repo_id: repo?.repo_id,
            account_id: selectedAccount,
          },
        });
      }
      setCreatedId(project.project_id);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '创建失败');
    } finally { setLoading(false); }
  };

  if (createdId) {
    return (
      <div>
        <h1>新建项目</h1>
        <div className="card" style={{ textAlign: 'center', padding: 32 }}>
          <h2>创建完成</h2>
          <div className="banner info" style={{ margin: '12px 0' }}>
            <b>Project ID: {createdId}</b>
          </div>
          <div className="row" style={{ gap: 10, justifyContent: 'center' }}>
            <button className="btn" onClick={() => nav(`/projects/${createdId}`)}>打开项目详情</button>
            <button className="btn" onClick={() => window.open(`/projects/${createdId}/workspace`, '_blank')}>进入工作区</button>
            <button className="btn ghost" onClick={() => nav('/projects')}>返回列表</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h1>新建项目</h1>
      <p className="sub">上传 ZIP 源码包，或从已绑定的 Git 平台账号选择仓库。</p>

      {err && <div className="redline" style={{ marginBottom: 14 }}>{err}</div>}

      {/* Basic info */}
      <div className="card" style={{ marginBottom: 14 }}>
        <label>项目名称 *</label>
        <input value={name} onChange={e => { setName(e.target.value); setErr(''); }}
          placeholder="例如：MicroOA 信创迁移" style={{ marginBottom: 10 }} />
        <label>项目描述（可选）</label>
        <input value={desc} onChange={e => setDesc(e.target.value)} placeholder="简要描述" />
      </div>

      {/* Source mode */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="cardgrid two" style={{ marginBottom: 12 }}>
          <div className={`card statcard`} style={{
            cursor: 'pointer', borderColor: mode === 'zip' ? 'var(--accent-ink)' : 'var(--line)',
          }} onClick={() => setMode('zip')}>
            <b>📦 ZIP 上传</b>
            <div className="hash" style={{ marginTop: 4 }}>上传本地项目源码包</div>
          </div>
          <div className={`card statcard`} style={{
            cursor: 'pointer', borderColor: mode === 'git' ? 'var(--accent-ink)' : 'var(--line)',
          }} onClick={() => setMode('git')}>
            <b>🔀 Git 仓库</b>
            <div className="hash" style={{ marginTop: 4 }}>从已绑定的 Git 账号选择仓库</div>
          </div>
        </div>

        {mode === 'zip' && (
          <div style={{ padding: 14, background: 'var(--surface-2)', borderRadius: 8 }}>
            <label>选择 ZIP 文件 *（最大 100MB 解压后）</label>
            <input type="file" accept=".zip" onChange={e => { setZipFile(e.target.files?.[0] || null); setErr(''); }} style={{ marginTop: 4 }} />
            {zipFile && <div className="hash" style={{ marginTop: 4 }}>已选择: {zipFile.name} ({(zipFile.size / 1024 / 1024).toFixed(1)} MB)</div>}
          </div>
        )}

        {mode === 'git' && (
          <div style={{ padding: 14, background: 'var(--surface-2)', borderRadius: 8 }}>
            <label>绑定账号 *</label>
            {acctsLoading ? <div className="meta">加载中…</div> : accounts.length === 0 ? (
              <div style={{ padding: '12px 0' }}>
                <div className="meta" style={{ marginBottom: 8 }}>暂无已绑定的 Git 账号。</div>
                <button className="btn sm ghost" onClick={() => nav('/integrations?tab=git')}>
                  前往集成页 → 连接 GitHub
                </button>
              </div>
            ) : (
              <select className="inp" value={selectedAccount}
                onChange={e => { setSelectedAccount(e.target.value); setSelectedRepo(''); }}
                style={{ marginTop: 4 }}>
                <option value="">-- 选择账号 --</option>
                {accounts.map(a => (
                  <option key={a.account_id} value={a.account_id}>{a.username} ({a.platform})</option>
                ))}
              </select>
            )}

            {selectedAccount && (
              <>
                <label style={{ marginTop: 12 }}>选择仓库 *</label>
                {reposLoading ? <div className="meta">加载仓库列表…</div> : repos.length === 0 ? (
                  <div className="meta" style={{ marginTop: 4 }}>该账号暂无仓库</div>
                ) : (
                  <select className="inp" value={selectedRepo}
                    onChange={e => setSelectedRepo(e.target.value)}
                    style={{ marginTop: 4 }}>
                    <option value="">-- 选择仓库 --</option>
                    {repos.map(r => (
                      <option key={r.repo_id} value={r.clone_url}>
                        {r.full_name} {r.private ? '(私有)' : ''} — {r.default_branch} ⭐{r.stargazers_count}
                      </option>
                    ))}
                  </select>
                )}
                <label style={{ marginTop: 10 }}>分支</label>
                <input value={gitBranch} onChange={e => setGitBranch(e.target.value)} placeholder="main" style={{ marginTop: 4 }} />
              </>
            )}
          </div>
        )}
      </div>

      <button className="btn" onClick={handleCreate} disabled={loading}
        style={{ width: '100%', justifyContent: 'center', padding: '12px 0', fontSize: 15 }}>
        {loading ? '创建中…' : '创建项目'}
      </button>
      <button className="btn ghost" onClick={() => nav('/projects')} style={{ width: '100%', marginTop: 8 }}>取消</button>
    </div>
  );
}
