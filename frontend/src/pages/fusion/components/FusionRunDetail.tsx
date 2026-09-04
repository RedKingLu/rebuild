/** 单次 Fusion 运行详情（WP-7.2 子 tab 3 详情 / WP-7.3 Judge 可视化 / WP-7.4 degraded）。
 * 展示 Panel 对比、Judge JSON 五字段、Synthesizer 输出、参与者可用性、degraded/Key 无效状态。 */
import type { FusionRun } from '../../../services/fusionService';
import { FusionJudgeResult } from './FusionJudgeResult';
import { Icon } from '../../../components/ui/Icon';

const STATUS_STYLE: Record<string, { label: string; color: string; bg: string }> = {
  completed: { label: '完成', color: '#fff', bg: 'var(--green,#22c55e)' },
  degraded: { label: '已降级', color: '#1a1207', bg: 'var(--amber,#f59e0b)' },
  failed: { label: '失败', color: '#fff', bg: 'var(--red,#ef4444)' },
  running: { label: '运行中', color: '#fff', bg: 'var(--blue,#3b82f6)' },
  pending: { label: '排队', color: 'var(--fg)', bg: 'var(--surface-2)' },
};

export function FusionRunDetail({ run }: { run: FusionRun | undefined }) {
  if (!run) return <div className="empty"><p className="sub">选择一条触发记录查看详情。</p></div>;
  const st = STATUS_STYLE[run.status] || STATUS_STYLE.pending;
  const md = (run.summary as Record<string, unknown> | undefined) || {};
  const fusionMeta = (md.fusion_metadata || md) as Record<string, unknown> | undefined;
  const judgeResult = fusionMeta?.judge_result as Record<string, unknown> | undefined;

  // 参与者可用性：收集每 profile_ref 的 provider 能力标记（后端已脱敏，无 Key）。
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      {/* 状态条 WP-7.4 */}
      <div className="row" style={{ gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <span className="tag" style={{ fontSize: 12, background: st.bg, color: st.color }}>{st.label}</span>
        <span className="tag" style={{ fontSize: 12 }}>策略：<b>{run.strategy}</b></span>
        <span className="tag" style={{ fontSize: 12 }}>用时：<b>{Math.round(run.latency_sum_ms)}ms</b></span>
        <span className="tag" style={{ fontSize: 12 }}>参与模型：<b>{run.participants.length}</b></span>
      </div>

      {run.degraded && (
        <div className="banner warn">
          <b>⚠ 已降级运行</b>
          <div className="sub" style={{ fontSize: 12, marginTop: 4 }}>
            原因：{run.degrade_reason || '—'}。结果基于部分可用模型或降级策略（Self-MoA / 单模型回退）。
          </div>
        </div>
      )}

      {/* WP-7.3 Judge 可视化 */}
      {judgeResult != null && Object.keys(judgeResult).length > 0 ? (
        <div className="card" style={{ padding: 14 }}>
          <b style={{ fontSize: 13 }}>Judge 评审结果</b>
          <div style={{ marginTop: 8 }}>
            <FusionJudgeResult result={judgeResult} />
          </div>
        </div>
      ) : null}

      {/* Panel 对比 */}
      {((md.panel_outputs as Record<string, unknown>[]) || []).length > 0 && (
        <div>
          <b style={{ fontSize: 13 }}>Panel 意见对比</b>
          <div style={{ display: 'grid', gap: 8, marginTop: 6 }}>
            {(md.panel_outputs as Record<string, unknown>[]).map((p, i) => (
              <div key={i} className="card" style={{ padding: 10 }}>
                <div className="sub" style={{ fontSize: 12, fontWeight: 600 }}>
                  <Icon name="docs" size={12} /> {(p.perspective as string) || `模型 ${i + 1}`}
                  <span className="tag" style={{ fontSize: 10, marginLeft: 6 }}>
                    {(p.provider_id as string) || ''}/{(p.model as string) || ''}
                  </span>
                  <span className="tag" style={{ fontSize: 10, marginLeft: 4, textTransform: 'uppercase' }}>
                    {p.status as string}
                  </span>
                </div>
                <div style={{ fontSize: 12, marginTop: 6, whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
                  {(p.content as string) || '（无内容）'}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Synthesizer 输出 */}
      {!!(md.synthesizer_result as Record<string, unknown> | undefined)?.content && (
        <div className="card" style={{ padding: 14, borderColor: 'var(--violet,#8b5cf6)' }}>
          <b style={{ fontSize: 13 }}>Synthesizer 综合输出</b>
          <div style={{ fontSize: 13, marginTop: 6, whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>
            {(md.synthesizer_result as Record<string, unknown>).content as string}
          </div>
        </div>
      )}

      {/* Trace 链接 */}
      {run.trace_refs?.length > 0 && (
        <div className="sub" style={{ fontSize: 11 }}>
          <Icon name="trace" size={12} /> Trace 引用：{run.trace_refs.length} 条
        </div>
      )}
    </div>
  );
}
