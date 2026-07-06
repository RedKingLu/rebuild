/** Judge JSON 五字段可视化（WP-7.3）。
 * 吸收 OpenRouter A 级证据：consensus / contradictions / partial_coverage / unique_insights / blind_spots。
 * 展示 scores / winner / confidence + 五个维度列表。 */
import type { CSSProperties } from 'react';

const DIMENSION_META: { key: string; label: string; icon: string; tone: 'good' | 'warn' | 'info' | 'bad' }[] = [
  { key: 'consensus', label: '共识', icon: '✓', tone: 'good' },
  { key: 'unique_insights', label: '独有洞察', icon: '✦', tone: 'info' },
  { key: 'partial_coverage', label: '部分覆盖', icon: '◐', tone: 'warn' },
  { key: 'contradictions', label: '矛盾', icon: '≠', tone: 'bad' },
  { key: 'blind_spots', label: '盲区', icon: '○', tone: 'bad' },
];

const TONE_STYLE: Record<string, CSSProperties> = {
  good: { background: 'rgba(34,197,94,.12)', borderColor: 'rgba(34,197,94,.3)' },
  warn: { background: 'rgba(245,158,11,.12)', borderColor: 'rgba(245,158,11,.3)' },
  info: { background: 'rgba(59,130,246,.12)', borderColor: 'rgba(59,130,246,.3)' },
  bad: { background: 'rgba(239,68,68,.10)', borderColor: 'rgba(239,68,68,.25)' },
};

function asStr(v: unknown): string { return typeof v === 'string' ? v : ''; }
function asStrArr(v: unknown): string[] { return Array.isArray(v) ? v.map(String) : []; }
function asScores(v: unknown): Record<string, number> {
  if (v && typeof v === 'object') return v as Record<string, number>;
  return {};
}

export function FusionJudgeResult({ result }: { result: Record<string, unknown> | undefined }) {
  if (!result) {
    return <div className="empty"><p className="sub">无 Judge 结果（Judge 未参与或失败）。</p></div>;
  }
  const scores = asScores(result.scores);
  const winner = asStr(result.winner);
  const confidence = asStr(result.confidence) || 'unknown';
  const confColor = confidence === 'high' ? 'var(--green,#22c55e)'
    : confidence === 'medium' ? 'var(--amber,#f59e0b)'
      : 'var(--orange,#f97316)';
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div className="row" style={{ gap: 10, flexWrap: 'wrap' }}>
        <span className="tag" style={{ fontSize: 12 }}>
          🏆 胜出：<b>{winner || '—'}</b>
        </span>
        <span className="tag" style={{ fontSize: 12, background: confColor, color: '#fff' }}>
          置信度：<b>{confidence}</b>
        </span>
      </div>

      {Object.keys(scores).length > 0 && (
        <div>
          <div className="sub" style={{ fontSize: 12, marginBottom: 4 }}>评分</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {Object.entries(scores).map(([k, v]) => (
              <span key={k} className="tag" style={{ fontSize: 11 }}>
                {k}：<b>{typeof v === 'number' ? v.toFixed(1) : String(v)}</b>
              </span>
            ))}
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gap: 8 }}>
        {DIMENSION_META.map(d => {
          const items = asStrArr(result[d.key]);
          return (
            <div key={d.key} style={{
              border: `1px solid ${TONE_STYLE[d.tone].borderColor}`,
              background: TONE_STYLE[d.tone].background, borderRadius: 8, padding: '8px 10px',
            }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: items.length ? 4 : 0 }}>
                {d.icon} {d.label} <span className="sub" style={{ fontWeight: 400 }}>({items.length})</span>
              </div>
              {items.length > 0 ? (
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, lineHeight: 1.6 }}>
                  {items.map((it, i) => <li key={i}>{it}</li>)}
                </ul>
              ) : <div className="sub" style={{ fontSize: 11 }}>—</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
