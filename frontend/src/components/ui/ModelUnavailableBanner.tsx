/** ModelUnavailableBanner — R17.3-6 WP-6：模型全失败强制中断的前端显式报错。
 *  据用户裁决 Q-R17.3-6-2：显示 模型不可用错误 + 当前中断阶段 + 失败原因 +
 *  已尝试模型链路（各 profile 及失败类别）+ 用户可采取操作（配置 / 切换 / 重试）。
 *  数据来自后端（summary 端点的 model_unavailable / Gate metadata），非 mock、非假数据（D-097）。
 *  中文优先；技术标识（profile_id/model）保留原文；无 Key/Token 展示（后端已脱敏）。
 */
import { Link } from 'react-router-dom';
import { Icon } from './Icon';

export interface AttemptedModel {
  profile_id?: string;
  provider_id?: string;
  model?: string;
  is_fallback?: boolean;
  outcome?: string;
  error_category?: string;
  error_message?: string;
}

export interface ModelUnavailableInfo {
  interrupted_stage?: string;
  failure_reason?: string;
  error_category?: string;
  attempted_chain?: AttemptedModel[];
  user_actions?: { action: string; label: string; target?: string }[];
}

const OUTCOME_LABEL: Record<string, string> = {
  completed: '成功',
  failed: '调用失败',
  credential_missing: '缺少凭据',
  not_configured: '未配置',
  capability_unmet: '能力不满足',
  skipped: '跳过',
};

const box: React.CSSProperties = {
  padding: '12px 14px', marginBottom: 12, background: 'var(--red-bg)',
  border: '1px solid var(--red)', borderRadius: 6, fontSize: 12, color: 'var(--red)',
};
const mono: React.CSSProperties = { fontFamily: 'var(--font-mono)', fontSize: 11 };

export function ModelUnavailableBanner({ info, onReExecute }: {
  info: ModelUnavailableInfo;
  onReExecute?: () => void;
}) {
  const stage = (info.interrupted_stage || '').toUpperCase();
  const chain = info.attempted_chain || [];
  const actions = info.user_actions || [];
  return (
    <div style={box} role="alert">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600, marginBottom: 6 }}>
        <Icon name="blocked" size={16} />
        <span>模型不可用：{stage ? `${stage} 阶段` : '当前阶段'}已强制中断</span>
      </div>
      <div style={{ marginBottom: 8, color: 'var(--color-text)' }}>
        失败原因：{info.failure_reason || '所有可用模型均不可用（未配置 / 无凭据 / 调用失败）'}
        {info.error_category && <span style={{ ...mono, marginLeft: 6, color: 'var(--red)' }}>[{info.error_category}]</span>}
      </div>
      <div style={{ color: 'var(--color-text)', marginBottom: 6 }}>
        平台未静默降级、未以规则兜底冒充模型输出；请处理后重试。
      </div>

      {chain.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontWeight: 600, color: 'var(--color-text)', marginBottom: 4 }}>已尝试的模型链路：</div>
          <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--color-text)' }}>
            {chain.map((c, i) => (
              <li key={i} style={{ marginBottom: 2 }}>
                <code style={mono}>{c.profile_id || c.model || '(未知模型)'}</code>
                {c.is_fallback ? <span style={{ ...mono, color: 'var(--color-text-muted)' }}> · 回退</span> : null}
                <span style={{ marginLeft: 6, color: 'var(--red)' }}>
                  {OUTCOME_LABEL[c.outcome || ''] || c.outcome || '未知'}
                </span>
                {c.error_message && (
                  <span style={{ ...muted }}> — {c.error_message}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
        {actions.some(a => a.action === 'configure' || a.action === 'switch') && (
          <Link to="/models" className="btn sm" style={{ textDecoration: 'none' }}>
            <Icon name="key" size={13} style={{ marginRight: 4 }} />配置 / 切换模型
          </Link>
        )}
        {onReExecute && (
          <button className="btn sm" style={{ background: 'var(--red)', color: '#fff' }} onClick={onReExecute}>
            <Icon name="refresh" size={13} style={{ marginRight: 4 }} />重新执行本阶段
          </button>
        )}
      </div>
    </div>
  );
}

const muted: React.CSSProperties = { fontSize: 11, color: 'var(--color-text-muted)' };
