import { useState, useEffect, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Icon } from '../../components/ui/Icon';
import {
  getGithubAuthorizeUrl, listGitAccounts, listAccountRepos, disconnectGitAccount,
  listGitHosts, createGitHost, deleteGitHost,
  listRemoteHosts, createRemoteHost, deleteRemoteHost, testRemoteHost,
  executeOpenCode, getOpenCodeStatus,
  getFeishuConfig, updateFeishuConfig, testFeishu,
  getIntegrationSummary,
  INTEGRATION_STATUS_LABELS,
  type GitAccountInfo, type GitRepoInfo, type GitHostInfo,
  type RemoteHostInfo, type IntegrationSummary,
} from '../../services/integrationService';

type Tab = 'git' | 'remote' | 'opencode' | 'feishu';

export function IntegrationsPage() {
  const nav = useNavigate();
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>(() => {
    const t = searchParams.get('tab');
    return (t === 'git' || t === 'remote' || t === 'opencode' || t === 'feishu') ? t : 'git';
  });
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState<IntegrationSummary | null>(null);

  // Git state
  const [accounts, setAccounts] = useState<GitAccountInfo[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<string>('');
  const [repos, setRepos] = useState<GitRepoInfo[]>([]);
  const [reposLoading, setReposLoading] = useState(false);

  // Remote state
  const [remoteHosts, setRemoteHosts] = useState<RemoteHostInfo[]>([]);
  const [testResults, setTestResults] = useState<Record<string, any>>({});

  // OpenCode state
  const [ocAvailable, setOcAvailable] = useState(false);
  const [ocCode, setOcCode] = useState('print("hello")');
  const [ocLang, setOcLang] = useState('python');
  const [ocTimeout, setOcTimeout] = useState(30);
  const [ocModel, setOcModel] = useState('');
  const [ocModels, setOcModels] = useState<{id: string; name: string}[]>([]);
  const [ocResult, setOcResult] = useState<any>(null);
  const [ocExecuting, setOcExecuting] = useState(false);

  // Load models for OpenCode
  useEffect(() => {
    import('../../services/modelService').then(({ listProviders }) => {
      listProviders().then(resp => {
        const providers: any[] = resp.data?.providers || [];
        const models: {id: string; name: string}[] = [];
        for (const p of providers) {
          const profiles = p.profiles || [];
          for (const m of profiles) {
            const mName = m.profile_id || m.model_name || '';
            if (mName) models.push({ id: mName, name: `${p.provider_id}/${mName}` });
          }
        }
        if (models.length > 0) { setOcModels(models); setOcModel(models[0].id); }
      }).catch(() => {});
    }).catch(() => {});
  }, []);

  // Feishu state
  const [fsConfig, setFsConfig] = useState<any>(null);
  const [fsUrl, setFsUrl] = useState('');
  const [fsSaving, setFsSaving] = useState(false);
  const [fsTestResult, setFsTestResult] = useState<any>(null);

  // OAuth callback detection
  useEffect(() => {
    const oauth = searchParams.get('oauth');
    const status = searchParams.get('status');
    if (oauth === 'github' && status === 'connected') {
      // Clear URL params and refresh accounts
      window.history.replaceState({}, '', '/integrations');
      fetchAccounts();
    }
  }, [searchParams]);

  const fetchAccounts = useCallback(async () => {
    try { setAccounts(await listGitAccounts()); } catch {}
  }, []);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [s, accts, _hosts, remotes] = await Promise.all([
        getIntegrationSummary().catch(() => null),
        listGitAccounts().catch(() => []),
        listGitHosts().catch(() => []),
        listRemoteHosts().catch(() => []),
      ]);
      setSummary(s);
      setAccounts(accts);
      setRemoteHosts(remotes);
      // OpenCode status
      try { const st = await getOpenCodeStatus(); setOcAvailable(st.opencode_available); } catch {}
      // Feishu config
      try { const fc = await getFeishuConfig(); setFsConfig(fc); } catch {}
    } catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // ── Git: Connect GitHub ──
  const handleConnectGithub = async () => {
    try {
      const url = await getGithubAuthorizeUrl();
      if (url) window.open(url, '_blank', 'width=800,height=700');
      else alert('GitHub OAuth 未配置。请在 backend .env 中设置 REBUILD_GITHUB_CLIENT_ID 和 REBUILD_GITHUB_CLIENT_SECRET。');
    } catch (e) { alert('获取授权链接失败'); }
  };

  // ── Git: Load repos for account ──
  const handleSelectAccount = async (accountId: string) => {
    setSelectedAccount(accountId);
    setRepos([]);
    if (!accountId) return;
    setReposLoading(true);
    try { setRepos(await listAccountRepos(accountId)); } catch {}
    finally { setReposLoading(false); }
  };

  // ── Git: Disconnect ──
  const handleDisconnect = async (accountId: string) => {
    try { await disconnectGitAccount(accountId); fetchAccounts(); setRepos([]); setSelectedAccount(''); } catch {}
  };

  // ── Remote ──
  const handleTestRemote = async (id: string) => {
    try { const r = await testRemoteHost(id); setTestResults(p => ({ ...p, [id]: r })); } catch {}
  };

  // ── OpenCode ──
  const handleExecute = async () => {
    setOcExecuting(true); setOcResult(null);
    try { setOcResult(await executeOpenCode(ocCode, ocLang, ocTimeout, ocModel || undefined)); } catch (e: any) {
      setOcResult({ error: e.message, provider: 'error', exit_code: -1 });
    } finally { setOcExecuting(false); }
  };

  // ── Feishu ──
  const handleSaveFeishu = async () => {
    setFsSaving(true);
    try { await updateFeishuConfig({ webhook_url: fsUrl }); setFsConfig({ configured: true }); } catch (e: any) { alert('保存失败: ' + e.message); }
    finally { setFsSaving(false); }
  };

  const handleTestFeishu = async () => { try { setFsTestResult(await testFeishu()); } catch (e: any) { setFsTestResult({ error: e.message }); } };

  const statusBadge = (s: string) => {
    const info = INTEGRATION_STATUS_LABELS[s] || { label: s, color: 'var(--ink-2)' };
    return <span className="tag" style={{ fontSize: 11, background: info.color, color: '#fff' }}>{info.label}</span>;
  };

  if (loading) return <div className="card"><p>加载中…</p></div>;

  const TABS: [Tab, string][] = [
    ['git', '代码托管'],
    ['remote', '远程资源'],
    ['opencode', '执行集成'],
    ['feishu', '其他集成'],
  ];

  return (
    <div>
      <h1>集成</h1>
      <p className="sub">管理外部平台和服务的连接：Git 仓库、远程主机、代码执行、通知推送。</p>

      {/* Tab nav + summary */}
      <div className="statgrid" style={{ marginBottom: 16 }}>
        {TABS.map(([k, label]) => (
          <button key={k} className="card statcard" onClick={() => setTab(k)} style={{
            textAlign: 'left', cursor: 'pointer',
            border: tab === k ? '2px solid var(--accent-ink)' : '1px solid var(--line)',
            background: tab === k ? 'var(--blue-bg, var(--surface-2))' : 'var(--surface)',
            width: '100%',
          }}>
            <b>{label}</b>
            <div className="snum">{
              k === 'git' ? `${summary?.git?.connected ?? 0} / ${(summary?.git?.connected ?? 0) + (summary?.git?.total ?? 0) || 0}` :
              k === 'remote' ? `${summary?.remote?.connected ?? 0} / ${summary?.remote?.total ?? 0}` :
              k === 'opencode' ? (ocAvailable ? 'OpenCode' : 'Shell') :
              fsConfig?.configured ? '已配置' : '0'
            }</div>
            <div className="slabel">{
              k === 'git' ? '账号（已绑定 / 总数）' :
              k === 'remote' ? '主机（在线 / 总数）' :
              k === 'opencode' ? '执行集成状态' :
              '飞书 Webhook'
            }</div>
          </button>
        ))}
      </div>

      {/* ═══ Tab: Git ═══ */}
      {tab === 'git' && (
        <div>
          {/* Connect GitHub */}
          <div className="card" style={{ marginBottom: 12 }}>
            <div className="spread">
              <div>
                <b>绑定 Git 平台账号</b>
                <div className="hash" style={{ marginTop: 4 }}>通过 OAuth 授权，绑定后可查看该账号所有仓库。</div>
              </div>
              <button className="btn" onClick={handleConnectGithub} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <Icon name="git" size={16} />连接 GitHub
              </button>
            </div>

            {/* Connected accounts */}
            {accounts.length > 0 && (
              <div style={{ marginTop: 12, borderTop: '1px solid var(--line)', paddingTop: 12 }}>
                <b style={{ fontSize: 13 }}>已绑定账号</b>
                {accounts.map(a => (
                  <div key={a.account_id} className={`listrow ${selectedAccount === a.account_id ? '' : ''}`}
                    style={{
                      cursor: 'pointer', padding: '10px 6px', marginTop: 6,
                      borderRadius: 8, background: selectedAccount === a.account_id ? 'var(--surface-2)' : 'transparent',
                    }}
                    onClick={() => handleSelectAccount(a.account_id === selectedAccount ? '' : a.account_id)}>
                    <div className="row" style={{ alignItems: 'center', gap: 10 }}>
                      {a.avatar_url && <img src={a.avatar_url} alt="" style={{ width: 32, height: 32, borderRadius: 16 }} />}
                      <div style={{ flex: 1 }}>
                        <div className="ttl">{a.username}</div>
                        <div className="meta">{a.platform} · 已连接</div>
                      </div>
                      <span className="tag green">已连接</span>
                      <button className="btn sm ghost" style={{ color: 'var(--red)' }}
                        onClick={e => { e.stopPropagation(); handleDisconnect(a.account_id); }}>断开</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Repo list for selected account */}
          {selectedAccount && (
            <div className="card">
              <b style={{ fontSize: 13 }}>仓库列表</b>
              {reposLoading ? <p className="meta">加载中…</p> : repos.length === 0 ? (
                <p className="meta" style={{ marginTop: 8 }}>该账号暂无仓库。</p>
              ) : (
                <div className="grid" style={{ gap: 8, marginTop: 8 }}>
                  {repos.map(r => (
                    <div key={r.repo_id} className="listrow" style={{ padding: '10px 0' }}>
                      <div style={{ flex: 1 }}>
                        <div className="ttl">
                          {r.name}
                          {r.private && <span className="tag" style={{ marginLeft: 6, fontSize: 10 }}>私有</span>}
                          {r.language && <span className="tag grey" style={{ marginLeft: 6, fontSize: 10 }}>{r.language}</span>}
                        </div>
                        <div className="meta">{r.description || r.full_name}</div>
                        <div className="meta" style={{ fontSize: 10 }}>{r.default_branch} · ⭐ {r.stargazers_count} · 更新于 {r.updated_at?.slice(0, 10)}</div>
                      </div>
                      <div className="row" style={{ gap: 6 }}>
                        <button className="btn sm ghost" onClick={() => nav(`/projects/new?repo=${encodeURIComponent(r.clone_url)}&name=${encodeURIComponent(r.name)}&branch=${r.default_branch}`)}>
                          创建项目
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Manual Git host management (legacy PAT) */}
          <details style={{ marginTop: 16 }}>
            <summary className="hash" style={{ cursor: 'pointer', padding: '8px 0' }}>手动添加仓库（PAT/SSH 方式，适用于未支持 OAuth 的平台）</summary>
            <ManualGitHosts />
          </details>
        </div>
      )}

      {/* ═══ Tab: Remote ═══ */}
      {tab === 'remote' && (
        <div>
          <div className="card" style={{ marginBottom: 12 }}>
            <div className="spread">
              <b>远程主机</b>
              <button className="btn sm" onClick={async () => {
                const name = prompt('主机名称：'); if (!name) return;
                const addr = prompt('地址（IP 或主机名）：'); if (!addr) return;
                const type = prompt('类型（virtual_machine/physical_machine/container）：') || 'virtual_machine';
                const port = parseInt(prompt('SSH 端口：') || '22');
                try { await createRemoteHost({ name, address: addr, host_type: type, port }); fetchAll(); }
                catch (e: any) { alert('添加失败: ' + e.message); }
              }}>＋ 添加主机</button>
            </div>
          </div>
          {remoteHosts.length === 0 ? (
            <div className="empty"><p className="sub">暂无远程主机。</p></div>
          ) : (
            remoteHosts.map(h => {
              const tr = testResults[h.remote_host_id];
              return (
                <div key={h.remote_host_id} className="card" style={{ padding: 14, marginBottom: 8 }}>
                  <div className="spread">
                    <div><b>{h.name}</b> <span className="tag">{h.host_type}</span></div>
                    <div className="row" style={{ gap: 6 }}>
                      {statusBadge(h.status)}
                      <button className="btn sm ghost" onClick={() => handleTestRemote(h.remote_host_id)}>测试连接</button>
                      <button className="btn sm ghost" style={{ color: 'var(--red)' }} onClick={async () => { await deleteRemoteHost(h.remote_host_id); fetchAll(); }}>删除</button>
                    </div>
                  </div>
                  <div className="meta" style={{ marginTop: 4 }}>{h.address}:{h.port} {h.os_name ? `· ${h.os_name}` : ''}</div>
                  {tr && <div className="hash" style={{ marginTop: 6, fontSize: 12, color: tr.status === 'connected' ? 'var(--green)' : 'var(--red)' }}>
                    {tr.status}: {tr.stdout?.slice(0, 200) || tr.error || ''}
                  </div>}
                </div>
              );
            })
          )}
        </div>
      )}

      {/* ═══ Tab: OpenCode ═══ */}
      {tab === 'opencode' && (
        <div>
          <div className="card" style={{ marginBottom: 14 }}>
            <div className="spread">
              <b>执行集成 · OpenCode</b>
              <span className="tag" style={{ background: ocAvailable ? 'var(--green)' : 'var(--amber)', color: '#fff' }}>
                {ocAvailable ? 'OpenCode CLI' : 'Fallback Shell'}
              </span>
            </div>
            <div className="hash" style={{ marginTop: 4, fontSize: 12 }}>
              {ocAvailable
                ? 'OpenCode CLI 已安装，Agent 可通过 ExecutionProvider 直接调用。'
                : 'OpenCode CLI 未安装，使用 Python3/Bash 安全沙箱作为降级执行器。'}
            </div>
          </div>

          {/* Quick test examples */}
          <div className="card" style={{ marginBottom: 14 }}>
            <b style={{ fontSize: 13 }}>快速测试</b>
            <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
              {[
                ['print("hello rebuild")', 'python', 'Hello'],
                ['echo "test ok"', 'bash', 'Echo'],
              ].map(([code, lang, label]) => (
                <button key={label} className="btn sm ghost" onClick={() => { setOcCode(code); setOcLang(lang); }}>
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* Execute */}
          <div className="card">
            <div className="grid" style={{ gap: 10 }}>
              <label className="sub" style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
                代码
                <textarea className="inp" rows={6} value={ocCode} onChange={e => setOcCode(e.target.value)}
                  style={{ fontFamily: 'monospace', fontSize: 13 }} />
              </label>
              <div className="row" style={{ gap: 10, flexWrap: 'wrap' }}>
                <label className="sub" style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, minWidth: 80 }}>
                  语言
                  <select className="inp" value={ocLang} onChange={e => setOcLang(e.target.value)}>
                    <option value="python">Python</option><option value="bash">Bash</option>
                  </select>
                </label>
                <label className="sub" style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, minWidth: 180 }}>
                  模型
                  <select className="inp" value={ocModel} onChange={e => setOcModel(e.target.value)}>
                    <option value="">默认（OpenCode 内置）</option>
                    {ocModels.map(m => (
                      <option key={m.id} value={m.id}>{m.name}</option>
                    ))}
                  </select>
                </label>
                <label className="sub" style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, minWidth: 70 }}>
                  超时(秒)
                  <input className="inp" type="number" value={ocTimeout} onChange={e => setOcTimeout(+e.target.value)} style={{ width: 70 }} />
                </label>
              </div>
              <button className="btn" onClick={handleExecute} disabled={ocExecuting} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <Icon name="send" size={14} />{ocExecuting ? '执行中…' : '执行代码'}
              </button>
            </div>
            {ocResult && (
              <div style={{ marginTop: 12, padding: 12, background: 'var(--surface-2)', borderRadius: 8, fontFamily: 'monospace', fontSize: 12 }}>
                <div className="row" style={{ gap: 12, marginBottom: 6, flexWrap: 'wrap' }}>
                  <span>provider: <b>{ocResult.provider}</b></span>
                  <span>exit: <b style={{ color: ocResult.exit_code === 0 ? 'var(--green)' : 'var(--red)' }}>{ocResult.exit_code}</b></span>
                  <span>{ocResult.elapsed_ms}ms</span>
                  {ocResult.blocked && <span className="tag red">已被安全策略阻止</span>}
                </div>
                {ocResult.error && <div className="err" style={{ marginBottom: 6 }}>{ocResult.error}</div>}
                {ocResult.stdout && <pre style={{ margin: '4px 0', whiteSpace: 'pre-wrap', maxHeight: 300, overflow: 'auto' }}>{ocResult.stdout}</pre>}
                {ocResult.stderr && <pre style={{ margin: '4px 0', color: 'var(--red)', whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto' }}>{ocResult.stderr}</pre>}
              </div>
            )}
          </div>

          {/* Security boundary info */}
          <div className="banner info" style={{ marginTop: 12, fontSize: 12 }}>
            <b>安全边界</b>：命令 allowlist（python3/echo/cat/ls/pwd）· 禁止 rm -rf / sudo / curl|sh · cwd 沙箱隔离 · 超时 30-120s · 环境变量脱敏 · stdout/stderr 截断 64KB
          </div>
        </div>
      )}

      {/* ═══ Tab: Feishu ═══ */}
      {tab === 'feishu' && (
        <div>
          <div className="card" style={{ marginBottom: 14 }}>
            <div className="spread">
              <b>飞书群机器人通知</b>
              {fsConfig?.configured ? <span className="tag green">已配置</span> : <span className="tag">未配置</span>}
            </div>
            <div className="hash" style={{ marginTop: 4, fontSize: 12 }}>
              配置飞书群自定义机器人 Webhook，rebuild 可通过此通道推送 Gate 审批提醒、运行完成通知、系统告警等消息。
            </div>
          </div>

          {/* How-to */}
          <div className="banner info" style={{ marginBottom: 14, fontSize: 12 }}>
            <b>如何获取 Webhook URL？</b><br/>
            1. 打开飞书桌面端或网页端 → 进入目标群聊<br/>
            2. 群设置 → 群机器人 → 添加机器人 → 自定义机器人<br/>
            3. 填写机器人名称（如"rebuild 通知"）→ 复制 Webhook URL<br/>
            4. 回到本页面粘贴 URL → 保存 → 测试发送<br/>
            <span style={{ color: 'var(--ink-3)' }}>注：也可设置签名校验（可选），secret 会经 AES-256-GCM 加密存储。</span>
          </div>

          {/* Config */}
          <div className="card">
            <div className="grid" style={{ gap: 10 }}>
              <label className="sub" style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12 }}>
                Webhook URL
                <input className="inp" value={fsUrl} onChange={e => setFsUrl(e.target.value)}
                  placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxxx" />
              </label>
              <div className="row" style={{ gap: 8 }}>
                <button className="btn sm" onClick={handleSaveFeishu} disabled={fsSaving}>
                  {fsSaving ? '保存中…' : '保存配置'}
                </button>
                <button className="btn sm ghost" onClick={handleTestFeishu} disabled={!fsConfig?.configured}>
                  发送测试消息
                </button>
              </div>
            </div>
            {fsTestResult && (
              <div style={{ marginTop: 10, padding: '8px 12px', borderRadius: 6, fontSize: 13,
                background: fsTestResult.sent ? 'var(--green-bg, #e6f7e6)' : 'var(--red-bg, #fde8e8)',
                color: fsTestResult.sent ? 'var(--green)' : 'var(--red)' }}>
                {fsTestResult.sent ? '✅ 测试消息已发送，请检查飞书群。' : '❌ ' + (fsTestResult.error || fsTestResult.reason || '发送失败')}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Legacy manual Git host management (PAT/SSH) */
function ManualGitHosts() {
  const [hosts, setHosts] = useState<GitHostInfo[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: '', repo_url: '', platform: 'github', auth_type: 'https_token', default_branch: 'main' });

  const fetchHosts = async () => {
    try { setHosts(await listGitHosts()); } catch {}
  };
  useEffect(() => { fetchHosts(); }, []);

  const handleSubmit = async () => {
    if (!form.name || !form.repo_url) return;
    try { await createGitHost(form); setShowForm(false); setForm({ name: '', repo_url: '', platform: 'github', auth_type: 'https_token', default_branch: 'main' }); fetchHosts(); }
    catch (e: any) { alert('添加失败: ' + e.message); }
  };

  return (
    <div>
      <button className="btn sm" onClick={() => setShowForm(!showForm)}>＋ 手动添加仓库</button>
      {showForm && (
        <div className="card" style={{ padding: 14, marginTop: 8 }}>
          <div className="grid" style={{ gridTemplateColumns: '1fr 2fr', gap: 8 }}>
            <input className="inp" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="名称" />
            <input className="inp" value={form.repo_url} onChange={e => setForm(f => ({ ...f, repo_url: e.target.value }))} placeholder="https://github.com/user/repo.git" />
            <select className="inp" value={form.platform} onChange={e => setForm(f => ({ ...f, platform: e.target.value }))}>
              <option value="github">GitHub</option><option value="gitlab">GitLab</option><option value="gitee">Gitee</option><option value="other">其他</option>
            </select>
            <div className="row" style={{ gap: 6 }}>
              <button className="btn sm" onClick={handleSubmit}>添加</button>
              <button className="btn sm ghost" onClick={() => setShowForm(false)}>取消</button>
            </div>
          </div>
        </div>
      )}
      {hosts.map(h => (
        <div key={h.git_host_id} className="listrow" style={{ padding: '8px 0' }}>
          <div style={{ flex: 1 }}><b style={{ fontSize: 13 }}>{h.name}</b> <span className="meta">{h.repo_url}</span></div>
          <div className="row" style={{ gap: 4 }}>
            <span className="tag" style={{ fontSize: 10 }}>{h.platform}</span>
            <button className="btn sm ghost" style={{ color: 'var(--red)' }} onClick={async () => { await deleteGitHost(h.git_host_id); fetchHosts(); }}>删除</button>
          </div>
        </div>
      ))}
    </div>
  );
}
