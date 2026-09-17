/** R9-3B: Real 5-step onboarding wizard — environment, git, model, mode, confirm.
 *  Each step calls real backend APIs. Step 5 finalizes by writing onboarding_done
 *  and creating a Run (P0 entry point).
 */
import { useState, useEffect } from 'react';
import { Modal } from '../ui/Modal';
import { Icon } from '../ui/Icon';
import { fetchEnvironment, updateEnvironment, updateMode, type EnvironmentProfile } from '../../services/workspaceService';
import { fetchModelGatewayStatus, type ModelGatewayStatus, listProfiles, type ModelProfileInfo } from '../../services/modelService';
import { listGitAccounts, listAccountRepos, listCodingAgents,
  type GitAccountInfo, type GitRepoInfo, type CodingAgentInfo } from '../../services/integrationService';
import { listScenarios, type ScenarioOption } from '../../services/scenarioService';

interface Props {
  projectId: string;
  projectName: string;
  sourceType: string;
  onDone: () => void;
  /** Current mode from Workspace top bar, initializes wizard step 4 */
  initialMode?: string;
  /** Current coding agent ref, if any */
  codingAgentRef?: string | null;
  /** R20-2-01: 项目已有的场景值（若创建页已填），引导向导控件初值须回显它（"后填覆盖先填"）。 */
  initialScenario?: string | null;
}

const STEPS = [
  { id: 'env', label: '环境选择' },
  { id: 'git', label: '提交方式' },
  { id: 'model', label: '模型配置' },
  { id: 'mode', label: '执行模式' },
  { id: 'confirm', label: '摘要确认' },
];

export function OnboardingWizard({ projectId, projectName, sourceType, onDone, initialMode, codingAgentRef, initialScenario }: Props) {
  const [stepIdx, setStepIdx] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Step 1 state — Environment
  const [envProfile, setEnvProfile] = useState<EnvironmentProfile | null>(null);
  const [envKind, setEnvKind] = useState<'local' | 'remote'>('local');
  const [languageHint, setLanguageHint] = useState('');
  const [frameworkHint, setFrameworkHint] = useState('');
  // R17.5 WP-6 (Q-R17.4-3-2): 目标运行环境（迁移目标 CPU 架构 + 目标 OS）。
  // 开放可扩展 / 允许自由输入（datalist 仅为常见建议，非封闭枚举）；平台可基于源环境推荐，用户点选为准。
  const [targetCpuArch, setTargetCpuArch] = useState('');
  const [targetOs, setTargetOs] = useState('');
  // R20-2-01/03 (D-117③): 重构场景（可选）。回显已有 scenario（创建页可能已填），引导向导
  // 可改选/补选；"后填覆盖先填" —— 本处提交值会覆盖创建页已落库的值（onboarding/complete）。
  const [scenario, setScenario] = useState(initialScenario || '');
  const [scenarioOptions, setScenarioOptions] = useState<ScenarioOption[]>([]);
  const [scenarioLoadError, setScenarioLoadError] = useState(false);
  const [scenarioDiscoveryStatus, setScenarioDiscoveryStatus] = useState('');
  const [scenarioInvalidCount, setScenarioInvalidCount] = useState(0);


  // Step 2 state — Submission method (R9-5-7 T1/T2: 本轮只接远端 Git)
  const [submissionKind, setSubmissionKind] = useState<'remote_git' | 'local_git'>('remote_git');
  const [gitAccounts, setGitAccounts] = useState<GitAccountInfo[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState('');
  const [accountRepos, setAccountRepos] = useState<GitRepoInfo[]>([]);
  const [selectedRepoUrl, setSelectedRepoUrl] = useState('');
  const [gitBranch, setGitBranch] = useState('');
  // Step 2b state — External platform delegation (R9-5-7 T8 / D-088)
  const [delegateExternal, setDelegateExternal] = useState(false);
  const [externalScope, setExternalScope] = useState<'coding_only' | 'all_stages'>('coding_only');
  const [codingAgents, setCodingAgents] = useState<CodingAgentInfo[]>([]);
  const [selectedCodingAgent, setSelectedCodingAgent] = useState<string>(codingAgentRef || '');

  // Step 3 state — Model
  const [gwStatus, setGwStatus] = useState<ModelGatewayStatus | null>(null);
  const [modelLoading, setModelLoading] = useState(true);
  // D-098: model strategy — 全局统一 / 自定义 (自动 暂占位)
  const [modelStrategyMode, setModelStrategyMode] = useState<'global_unified' | 'custom'>('global_unified');
  const [profiles, setProfiles] = useState<ModelProfileInfo[]>([]);
  const [globalModelRef, setGlobalModelRef] = useState<string>('');

  // Step 4 state — Mode
  const [execMode, setExecMode] = useState<string>(initialMode || 'plan');

  // Load environment and model data on mount
  useEffect(() => {
    // Load environment profile
    fetchEnvironment(projectId).then(p => {
      setEnvProfile(p);
      if (p.env_kind) setEnvKind(p.env_kind);
      if (p.language_hint) setLanguageHint(p.language_hint);
      if (p.framework_hint) setFrameworkHint(p.framework_hint);
    }).catch(() => {});

    // Load model gateway status
    fetchModelGatewayStatus().then(s => {
      setGwStatus(s);
      setModelLoading(false);
    }).catch(() => setModelLoading(false));

    // D-098: load available model profiles for the global-mode selector
    listProfiles().then(resp => {
      const list = resp.data.profiles || [];
      setProfiles(list);
      const configured = list.find(p => p.status === 'configured');
      if (configured) setGlobalModelRef(configured.profile_id);
      else if (list.length > 0) setGlobalModelRef(list[0].profile_id);
    }).catch(() => {});

    // Check if git is configured (sourceType = git/github implies configured at project create)
    if (sourceType === 'git' || sourceType === 'github') {
      setSubmissionKind('remote_git');
    }

    // R9-5-7 T1/T2: load bound Git accounts for the remote-Git submission picker
    listGitAccounts().then(setGitAccounts).catch(() => {});
    // R9-5-7 T8: load coding agents for the external-platform delegation picker
    listCodingAgents().then(setCodingAgents).catch(() => {});

    // R20-2-03: 场景建议列表来自运行期 GET /api/scenarios（前端零场景常量）。
    // 失败须显式提示（不静默 catch(() => {})——新增代码不沿用本文件既有的静默反模式，
    // 仅有的既存 5 处 .catch(() => {}) 不动，§10-23 精准修改）。
    listScenarios()
      .then(r => {
        setScenarioOptions(r.scenarios);
        setScenarioDiscoveryStatus(r.discovery_status);
        setScenarioInvalidCount(r.invalid.length);
      })
      .catch(() => setScenarioLoadError(true));
  }, [projectId, sourceType]);

  // R9-5-7 T1/T2: when a Git account is chosen, load its repos
  useEffect(() => {
    if (!selectedAccountId) { setAccountRepos([]); return; }
    listAccountRepos(selectedAccountId).then(repos => {
      setAccountRepos(repos);
      if (repos.length > 0) {
        setSelectedRepoUrl(repos[0].clone_url);
        setGitBranch(repos[0].default_branch || '');
      }
    }).catch(() => setAccountRepos([]));
  }, [selectedAccountId]);

  // ── Step handlers ──────────────────────────────────────────────────

  const handleStep1Next = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await updateEnvironment(projectId, {
        env_kind: envKind,
        language_hint: languageHint || null,
        framework_hint: frameworkHint || null,
        status: 'declared',
      });
      setStepIdx(1);
    } catch (e: any) {
      setError(e.message || '保存环境配置失败');
    } finally {
      setSubmitting(false);
    }
  };

  // R9-5-7 T6: persist execution mode when leaving step 4 so "选了即生效"
  // (single source = workspace.json, same endpoint as the top-bar switch).
  const handleModeNext = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await updateMode(projectId, execMode);
      setStepIdx(i => i + 1);
    } catch (e: any) {
      setError(e.message || '保存执行模式失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleFinalize = async () => {
    setSubmitting(true);
    setError(null);
    try {
      // Step 1: Save onboarding data + materialize source (real work)
      const resp = await fetch(`/api/projects/${projectId}/onboarding/complete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          execution_mode: execMode,
          env_kind: envKind,
          language_hint: languageHint || null,
          framework_hint: frameworkHint || null,
          coding_agent_ref: delegateExternal ? (selectedCodingAgent || null) : (codingAgentRef || null),
          model_strategy_mode: modelStrategyMode,
          global_model_ref: modelStrategyMode === 'global_unified' ? (globalModelRef || null) : null,
          // R9-5-7 T8 (D-088): external platform delegation scope
          external_platform_scope: delegateExternal ? externalScope : 'none',
          // R9-5-7 T1/T2 (D-086②/D-094): remote-Git submission (本轮只接远端 Git)
          submission_kind: submissionKind,
          git_remote_url: submissionKind === 'remote_git' ? (selectedRepoUrl || null) : null,
          git_branch: submissionKind === 'remote_git' ? (gitBranch || null) : null,
          // R17.5 WP-6 (Q-R17.4-3-2): 目标运行环境（用户点选/自由输入，非封闭枚举）。
          target_cpu_arch: targetCpuArch.trim() || null,
          target_cpu_arch_label: targetCpuArch.trim() || null,
          target_os: targetOs.trim() || null,
          target_os_label: targetOs.trim() || null,
          // R20-2-01/03 (D-117③)：引导向导可补选/改选场景，"后填覆盖先填"——会覆盖创建页
          // 已落库的值。留空则不传具体新值（后端诚实保持原值不变，不猜场景 R20-2-06）。
          scenario: scenario.trim() || null,
        }),
      });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error((errData as any).detail || `完成引导失败 (${resp.status})`);
      }
      await resp.json();
      // WorkspacePage will trigger Agent-driven P0 execution via executeP0Agent()
      onDone();
    } catch (e: any) {
      setError(e.message || '完成引导失败');
    } finally {
      setSubmitting(false);
    }
  };

  // ── Render ──────────────────────────────────────────────────────────

  const step = STEPS[stepIdx];
  const isLast = stepIdx === STEPS.length - 1;

  return (
    <Modal open onClose={onDone} title={`项目引导 · ${projectName}`}>
      <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 16 }}>
        完成 5 步引导后进入 P0 接入流程。关闭浮窗可跳过，使用默认配置。
      </p>

      {/* Step indicators */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 20 }}>
        {STEPS.map((s, i) => (
          <div key={s.id} style={{
            flex: 1, textAlign: 'center', padding: '6px 4px', fontSize: 12, borderRadius: 4,
            background: i < stepIdx ? 'var(--green-soft, #e8f5e9)' : i === stepIdx ? 'var(--color-primary-soft)' : 'var(--color-surface-subtle)',
            color: i < stepIdx ? 'var(--green)' : i === stepIdx ? 'var(--color-primary)' : 'var(--color-text-muted)',
            fontWeight: i === stepIdx ? 600 : 400,
          }}>
            {i + 1}. {s.label}
          </div>
        ))}
      </div>

      {error && (
        <div style={{ padding: '8px 12px', background: 'var(--red-soft, #ffebee)', color: 'var(--red)', borderRadius: 4, marginBottom: 12, fontSize: 13 }}>
          {error}
          <button style={{ marginLeft: 8, fontSize: 12 }} onClick={() => setError(null)}>✕</button>
        </div>
      )}

      <div style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8, padding: 20 }}>
        {/* Step 1: Environment */}
        {step.id === 'env' && (
          <div>
            <h3 style={{ fontSize: 15, marginBottom: 12 }}>环境选择</h3>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 13, marginBottom: 6 }}>运行环境</label>
              <div style={{ display: 'flex', gap: 8 }}>
                {(['local', 'remote'] as const).map(k => (
                  <button key={k} onClick={() => setEnvKind(k)} style={{
                    flex: 1, padding: '10px', border: `2px solid ${envKind === k ? 'var(--color-primary)' : 'var(--color-border)'}`,
                    borderRadius: 6, background: envKind === k ? 'var(--color-primary-soft)' : 'transparent',
                    cursor: 'pointer', fontSize: 13, fontWeight: envKind === k ? 600 : 400,
                  }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      <Icon name={k === 'local' ? 'workspace' : 'remote'} size={14} />
                      {k === 'local' ? '本地环境' : '远程环境'}</span>
                  </button>
                ))}
              </div>
            </div>
            <div style={{ marginBottom: 12 }}>
              <label style={{ display: 'block', fontSize: 13, marginBottom: 4 }}>主要语言（可选）</label>
              <input value={languageHint} onChange={e => setLanguageHint(e.target.value)}
                placeholder="如 Java, Python, C#, Go..." style={{ width: '100%', padding: '8px', border: '1px solid var(--color-border)', borderRadius: 4, fontSize: 13 }} />
            </div>
            <div style={{ marginBottom: 12 }}>
              <label style={{ display: 'block', fontSize: 13, marginBottom: 4 }}>框架提示（可选）</label>
              <input value={frameworkHint} onChange={e => setFrameworkHint(e.target.value)}
                placeholder="如 Spring Boot, .NET, Django..." style={{ width: '100%', padding: '8px', border: '1px solid var(--color-border)', borderRadius: 4, fontSize: 13 }} />
            </div>
            {/* R17.5 WP-6: 目标运行环境（迁移目标）。开放输入，下拉仅为常见建议，可自由填写。 */}
            <div style={{ marginTop: 8, paddingTop: 12, borderTop: '1px dashed var(--color-border)' }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>目标运行环境（迁移目标）</div>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 10 }}>
                指定本次迁移/重构的目标 CPU 架构与操作系统。可从建议中选择，也可自由填写（不限于列出选项）。留空则后续阶段再确认。
              </div>
              <div style={{ display: 'flex', gap: 10 }}>
                <div style={{ flex: 1 }}>
                  <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>目标 CPU 架构</label>
                  <input list="target-cpu-arch-options" value={targetCpuArch}
                    onChange={e => setTargetCpuArch(e.target.value)}
                    placeholder="如 x86_64 / ARM64 / 龙芯 LoongArch..."
                    style={{ width: '100%', padding: '8px', border: '1px solid var(--color-border)', borderRadius: 4, fontSize: 13 }} />
                  <datalist id="target-cpu-arch-options">
                    <option value="x86_64" />
                    <option value="ARM64 (aarch64)" />
                    <option value="LoongArch (龙芯)" />
                    <option value="鲲鹏 (Kunpeng ARM64)" />
                    <option value="飞腾 (Phytium ARM64)" />
                    <option value="兆芯 (Zhaoxin x86)" />
                  </datalist>
                </div>
                <div style={{ flex: 1 }}>
                  <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>目标操作系统</label>
                  <input list="target-os-options" value={targetOs}
                    onChange={e => setTargetOs(e.target.value)}
                    placeholder="如 统信 UOS / 麒麟 / openEuler / Linux..."
                    style={{ width: '100%', padding: '8px', border: '1px solid var(--color-border)', borderRadius: 4, fontSize: 13 }} />
                  <datalist id="target-os-options">
                    <option value="统信 UOS" />
                    <option value="麒麟 Kylin" />
                    <option value="openEuler" />
                    <option value="Ubuntu" />
                    <option value="CentOS / RHEL" />
                    <option value="Windows Server" />
                  </datalist>
                </div>
              </div>
            </div>
            {/* R20-2-01/03 (D-117③)：重构场景（可选）。与目标运行环境同组、同一开放输入
                范式——共用 <input list>+<datalist>，允许自由填写不在建议列表里的场景 id。
                回显已有 scenario（若创建页已填）；此处提交会"后填覆盖先填"。 */}
            <div style={{ marginTop: 8, paddingTop: 12, borderTop: '1px dashed var(--color-border)' }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>重构场景（可选）</div>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 10 }}>
                可从建议中选择，也可自由填写（不限于列出选项）。留空则后续阶段再确认。
              </div>
              <input list="onboarding-scenario-options" value={scenario}
                onChange={e => setScenario(e.target.value)}
                placeholder="可选，如自定义场景标识"
                style={{ width: '100%', padding: '8px', border: '1px solid var(--color-border)', borderRadius: 4, fontSize: 13 }} />
              <datalist id="onboarding-scenario-options">
                {scenarioOptions.map(s => (
                  <option key={s.scenario_id} value={s.scenario_id} label={
                    `${s.display_name}${s.tier === 'typical' ? '（典型场景 · 配套资源更丰富）' : '（自定义场景）'}`
                  } />
                ))}
              </datalist>
              {scenarioLoadError && (
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 4 }}>
                  场景列表加载失败，可手动填写场景标识。
                </div>
              )}
              {!scenarioLoadError && scenarioDiscoveryStatus === 'root_missing' && (
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 4 }}>
                  未发现场景包目录，可留空或手动填写场景标识。
                </div>
              )}
              {!scenarioLoadError && scenarioInvalidCount > 0 && (
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 4 }}>
                  {scenarioInvalidCount} 个场景包目录不完整，已跳过。
                </div>
              )}
            </div>
            {envProfile && (
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                当前状态：{envProfile.status} · {envProfile.env_kind || '未设置'}
                {envProfile.language_hint && <> · {envProfile.language_hint}</>}
              </div>
            )}
          </div>
        )}

        {/* Step 2: Submission method (R9-5-7 T1/T2/T8) */}
        {step.id === 'git' && (
          <div>
            <h3 style={{ fontSize: 15, marginBottom: 12 }}>提交方式</h3>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 10 }}>
              当前项目来源：<b>{sourceType === 'local_dir' ? '本地目录' : sourceType === 'git' ? 'Git 仓库' : sourceType === 'github' ? 'GitHub' : sourceType === 'zip' ? 'ZIP 上传' : '手动创建'}</b>
            </div>

            {/* submission kind: remote / local (本轮只接远端 Git) */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              <button type="button" onClick={() => setSubmissionKind('remote_git')} style={{
                flex: 1, padding: '10px', borderRadius: 6, cursor: 'pointer', fontSize: 13,
                border: `2px solid ${submissionKind === 'remote_git' ? 'var(--color-primary)' : 'var(--color-border)'}`,
                background: submissionKind === 'remote_git' ? 'var(--color-primary-soft)' : 'transparent',
                fontWeight: submissionKind === 'remote_git' ? 600 : 400,
              }}><Icon name="remote" size={14} /> 远端 Git</button>
              <button type="button" onClick={() => setSubmissionKind('local_git')} style={{
                flex: 1, padding: '10px', borderRadius: 6, cursor: 'pointer', fontSize: 13,
                border: `2px solid ${submissionKind === 'local_git' ? 'var(--color-primary)' : 'var(--color-border)'}`,
                background: submissionKind === 'local_git' ? 'var(--color-primary-soft)' : 'transparent',
                fontWeight: submissionKind === 'local_git' ? 600 : 400,
              }}><Icon name="files" size={14} /> 本地 Git</button>
            </div>

            {submissionKind === 'remote_git' ? (
              <div>
                <label style={{ display: 'block', fontSize: 13, marginBottom: 4 }}>Git 账号</label>
                <select value={selectedAccountId} onChange={e => setSelectedAccountId(e.target.value)}
                  style={{ width: '100%', padding: '8px 10px', borderRadius: 6, fontSize: 13, marginBottom: 10, border: '1px solid var(--color-border)', background: 'var(--color-surface)' }}>
                  <option value="">{gitAccounts.length ? '选择已绑定的 Git 账号' : '（未绑定 Git 账号，请先在集成页绑定）'}</option>
                  {gitAccounts.map(a => (
                    <option key={a.account_id} value={a.account_id}>{a.platform} · {a.username}</option>
                  ))}
                </select>
                {selectedAccountId && (
                  <>
                    <label style={{ display: 'block', fontSize: 13, marginBottom: 4 }}>仓库</label>
                    <select value={selectedRepoUrl} onChange={e => {
                      setSelectedRepoUrl(e.target.value);
                      const r = accountRepos.find(x => x.clone_url === e.target.value);
                      if (r) setGitBranch(r.default_branch || '');
                    }} style={{ width: '100%', padding: '8px 10px', borderRadius: 6, fontSize: 13, marginBottom: 10, border: '1px solid var(--color-border)', background: 'var(--color-surface)' }}>
                      <option value="">{accountRepos.length ? '选择仓库' : '（该账号无可见仓库）'}</option>
                      {accountRepos.map(r => (
                        <option key={r.repo_id} value={r.clone_url}>{r.full_name}{r.private ? ' 🔒' : ''}</option>
                      ))}
                    </select>
                    <label style={{ display: 'block', fontSize: 13, marginBottom: 4 }}>分支</label>
                    <input value={gitBranch} onChange={e => setGitBranch(e.target.value)} placeholder="main"
                      style={{ width: '100%', padding: '8px', borderRadius: 4, fontSize: 13, border: '1px solid var(--color-border)' }} />
                  </>
                )}
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 6 }}>
                  确认后将按所选仓库/分支拉取源码；凭据无效时按诚实降级标记，不阻断引导。
                </div>
              </div>
            ) : (
              <div style={{ padding: '10px', background: 'var(--color-surface-subtle)', borderRadius: 4, fontSize: 12, color: 'var(--color-text-muted)' }}>
                本地 Git 接入待 D-094 交互形态澄清后开放（依赖标注）。本轮请使用「远端 Git」。
              </div>
            )}

            {/* T8: external platform delegation */}
            <div style={{ marginTop: 16, paddingTop: 12, borderTop: '1px dashed var(--color-border)' }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>执行方式</div>
              <div style={{ display: 'flex', gap: 8, marginBottom: delegateExternal ? 10 : 0 }}>
                <button type="button" onClick={() => setDelegateExternal(false)} style={{
                  flex: 1, padding: '8px', borderRadius: 6, cursor: 'pointer', fontSize: 12,
                  border: `2px solid ${!delegateExternal ? 'var(--color-primary)' : 'var(--color-border)'}`,
                  background: !delegateExternal ? 'var(--color-primary-soft)' : 'transparent',
                }}>自有 Agent 全包</button>
                <button type="button" onClick={() => setDelegateExternal(true)} style={{
                  flex: 1, padding: '8px', borderRadius: 6, cursor: 'pointer', fontSize: 12,
                  border: `2px solid ${delegateExternal ? 'var(--color-primary)' : 'var(--color-border)'}`,
                  background: delegateExternal ? 'var(--color-primary-soft)' : 'transparent',
                }}>委托外部平台</button>
              </div>
              {delegateExternal && (
                <div>
                  <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>委托范围</label>
                  <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
                    {([['coding_only', '仅编码 (P4)'], ['all_stages', '所有环节 (P0-P6)']] as const).map(([v, t]) => (
                      <button key={v} type="button" onClick={() => setExternalScope(v)} style={{
                        flex: 1, padding: '6px', borderRadius: 6, cursor: 'pointer', fontSize: 12,
                        border: `2px solid ${externalScope === v ? 'var(--color-primary)' : 'var(--color-border)'}`,
                        background: externalScope === v ? 'var(--color-primary-soft)' : 'transparent',
                      }}>{t}</button>
                    ))}
                  </div>
                  <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>外部编码平台</label>
                  <select value={selectedCodingAgent} onChange={e => setSelectedCodingAgent(e.target.value)}
                    style={{ width: '100%', padding: '8px 10px', borderRadius: 6, fontSize: 13, border: '1px solid var(--color-border)', background: 'var(--color-surface)' }}>
                    <option value="">{codingAgents.length ? '选择编码平台' : '（未配置编码平台，请先在集成页添加）'}</option>
                    {codingAgents.map(a => (
                      <option key={a.agent_id} value={a.agent_id}>{a.name}（{a.agent_type}）</option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Step 3: Model */}
        {step.id === 'model' && (
          <div>
            <h3 style={{ fontSize: 15, marginBottom: 12 }}>模型配置</h3>
            {/* 网关状态 */}
            <div style={{ fontSize: 13, marginBottom: 12 }}>
              网关状态：
              {modelLoading ? '加载中…' : gwStatus ? (
                <>
                  <span style={{
                    display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
                    background: gwStatus.global_status === 'healthy' ? 'var(--green)' : 'var(--amber)',
                    margin: '0 4px',
                  }} />
                  <b>{gwStatus.global_status === 'healthy' ? '可用' : gwStatus.global_status}</b>
                </>
              ) : <span style={{ color: 'var(--color-text-muted)' }}>不可达（可稍后配置）</span>}
            </div>

            {/* D-098: 模型策略选择 */}
            <div style={{ fontSize: 13, marginBottom: 8, fontWeight: 600 }}>默认模型策略</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {([
                { id: 'global_unified', title: '全局统一', desc: '指定一个模型，本项目所有环节都使用它' },
                { id: 'custom', title: '自定义', desc: '按各 Agent 配置（在资源页为每个 Agent 设置模型，未设则用系统默认）' },
              ] as const).map(opt => (
                <button key={opt.id} type="button"
                  onClick={() => setModelStrategyMode(opt.id)}
                  style={{
                    textAlign: 'left', padding: '10px 12px', borderRadius: 8, cursor: 'pointer',
                    border: `2px solid ${modelStrategyMode === opt.id ? 'var(--color-primary)' : 'var(--color-border)'}`,
                    background: modelStrategyMode === opt.id ? 'var(--color-primary-soft)' : 'var(--color-surface)',
                  }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{opt.title}</div>
                  <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>{opt.desc}</div>
                </button>
              ))}
              {/* 自动模式占位（暂未开放） */}
              <div style={{
                textAlign: 'left', padding: '10px 12px', borderRadius: 8,
                border: '2px dashed var(--color-border)', opacity: 0.6,
                background: 'var(--color-surface-subtle)',
              }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>自动 <span style={{ fontSize: 11, fontWeight: 400 }}>（占位 · 暂未开放）</span></div>
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>按任务自动选择最合适的模型，后续版本开放</div>
              </div>
            </div>

            {/* 全局模式：选择模型 */}
            {modelStrategyMode === 'global_unified' && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontSize: 13, marginBottom: 6 }}>全局模型</div>
                <select value={globalModelRef} onChange={e => setGlobalModelRef(e.target.value)}
                  style={{
                    width: '100%', padding: '8px 10px', borderRadius: 6, fontSize: 13,
                    border: '1px solid var(--color-border)', background: 'var(--color-surface)',
                  }}>
                  {profiles.length === 0 && <option value="">（暂无可选模型，请先在模型页配置）</option>}
                  {profiles.map(p => (
                    <option key={p.profile_id} value={p.profile_id}>
                      {p.display_name || p.model_name}（{p.provider_id}）{p.status === 'configured' ? '' : ' · 未配置'}
                    </option>
                  ))}
                </select>
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 6 }}>
                  留空则使用系统默认策略。模型不可用时按策略自动回退到可用模型。
                </div>
              </div>
            )}
          </div>
        )}

        {/* Step 4: Execution Mode */}
        {step.id === 'mode' && (
          <div>
            <h3 style={{ fontSize: 15, marginBottom: 12 }}>执行模式</h3>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
              选择自动化程度。阶段晋级 Gate 在所有模式下均需用户确认。
            </p>
            {([
              ['manual', '手动', '用户审核每步动作和授权，最高控制力'],
              ['plan', '计划确认', '用户审核阶段计划，计划内动作自动执行。推荐'],
              ['auto', '自动', '低风险动作自动，高风险回用户确认'],
            ] as const).map(([mode, label, desc]) => (
              <div key={mode} onClick={() => setExecMode(mode)} style={{
                padding: '12px', marginBottom: 8, cursor: 'pointer',
                border: `2px solid ${execMode === mode ? 'var(--color-primary)' : 'var(--color-border)'}`,
                borderRadius: 6, background: execMode === mode ? 'var(--color-primary-soft)' : 'transparent',
              }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{label}</div>
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>{desc}</div>
              </div>
            ))}
          </div>
        )}

        {/* Step 5: Confirm */}
        {step.id === 'confirm' && (
          <div>
            <h3 style={{ fontSize: 15, marginBottom: 12 }}>摘要确认</h3>
            <div style={{ fontSize: 13, lineHeight: 1.8 }}>
              <div>✓ 环境：<b>{envKind === 'local' ? '本地环境' : '远程环境'}</b>
                {languageHint && <> · {languageHint}</>}
                {frameworkHint && <> · {frameworkHint}</>}
              </div>
              <div>✓ 来源：<b>{sourceType === 'local_dir' ? '本地目录' : sourceType === 'git' ? 'Git' : sourceType === 'github' ? 'GitHub' : sourceType === 'zip' ? 'ZIP' : '手动'}</b></div>
              {(targetCpuArch.trim() || targetOs.trim()) && (
                <div>✓ 目标环境：<b>{[targetCpuArch.trim(), targetOs.trim()].filter(Boolean).join(' · ')}</b></div>
              )}
              <div>✓ 重构场景：<b>{
                scenario.trim()
                  ? (scenarioOptions.find(s => s.scenario_id === scenario.trim())?.display_name || scenario.trim())
                  : '未选择（后续阶段再确认）'
              }</b></div>
              <div>✓ 模型：<b>{gwStatus?.global_status === 'healthy' ? '全局默认' : '待配置'}</b></div>
              <div>✓ 模式：<b>{execMode === 'manual' ? '手动' : execMode === 'plan' ? '计划确认' : '自动'}</b></div>
            </div>
            <div style={{ marginTop: 16, padding: '10px', background: 'var(--color-surface-subtle)', borderRadius: 4, fontSize: 12 }}>
              确认后将写入项目配置、创建 P0 接入 Run，并进入工作区。
              你可以在工作区顶部随时切换执行模式。
            </div>
          </div>
        )}

        {/* Navigation buttons */}
        <div style={{ display: 'flex', gap: 8, marginTop: 20 }}>
          {stepIdx > 0 && (
            <button className="btn ghost" onClick={() => setStepIdx(i => i - 1)} disabled={submitting}>
              上一步
            </button>
          )}
          <div style={{ flex: 1 }} />
          <button className="btn ghost" onClick={() => { onDone(); }} disabled={submitting}>
            跳过引导
          </button>
          {!isLast ? (
            <button className="btn" onClick={
              step.id === 'env' ? handleStep1Next
              : step.id === 'mode' ? handleModeNext
              : () => setStepIdx(i => i + 1)
            } disabled={submitting}>
              {submitting ? '保存中…' : '下一步'}
            </button>
          ) : (
            <button className="btn" onClick={handleFinalize} disabled={submitting}>
              {submitting ? '完成中…' : '确认并进入工作区'}
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}
