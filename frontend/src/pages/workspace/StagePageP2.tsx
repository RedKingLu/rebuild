/** StagePageP2 — R10 T18: P2 评估阶段页，展示契约 §4.5 的 6 类评估产出。
 *  数据来自 GET /api/projects/{id}/assessment-summary（RealP2Handler 落盘的 p2_*.json）。
 *  6 类产出：评估报告 / 风险清单（按 L0-L5 分组）/ 阻塞项 / 不确定项 / 验证缺口 / 资源需求。
 *  硬要求：analysis_only 醒目标记（§4.7-5/STOP-4 模型输出为辅助分析非事实）；blocked/rework
 *  状态横幅；空态诚实；线性图标（Icon.tsx）；中文优先；真实数据不挂 mock 横幅（D-049）。
 */
import { useState, useEffect } from 'react';
import { Icon } from '../../components/ui/Icon';
import { ModelUnavailableBanner } from '../../components/ui/ModelUnavailableBanner';
import { RISK_LABELS } from '../../services/resourceService';

interface Props {
  projectId: string;
  stageStatus?: string;
  onReExecute?: () => void;
}

// 风险等级 → 语义色（配文字，不依赖颜色单独表达，视觉规范 §2-4）
const RISK_COLOR: Record<string, string> = {
  L0: 'var(--color-text-muted)', L1: 'var(--color-text-muted)',
  L2: 'var(--color-primary)', L3: 'var(--color-warning)',
  L4: 'var(--color-warning-strong)', L5: 'var(--color-danger)',
};
const RISK_ORDER = ['L5', 'L4', 'L3', 'L2', 'L1', 'L0'];

const card: React.CSSProperties = {
  padding: 14, background: 'var(--color-surface)',
  border: '1px solid var(--color-border)', borderRadius: 8,
};
const cardTitle: React.CSSProperties = { fontWeight: 600, fontSize: 14, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 };
const muted: React.CSSProperties = { fontSize: 12, color: 'var(--color-text-muted)' };

function itemText(it: any): string {
  if (typeof it === 'string') return it;
  return it?.title || it?.detail || it?.description || it?.name || JSON.stringify(it);
}

export function StagePageP2({ projectId, stageStatus, onReExecute }: Props) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isChangesRequested = stageStatus === 'changes_requested';
  const isBlocked = stageStatus === 'blocked';

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(`/api/projects/${projectId}/assessment-summary`)
      .then(r => r.json())
      .then(d => { setData(d?.data || d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [projectId]);

  if (loading) return <div style={{ padding: 12, ...muted }}>加载 P2 评估结果…</div>;
  if (error) return <div style={{ padding: 12, fontSize: 13, color: 'var(--red)' }}>加载失败：{error}</div>;

  const available = data?.available !== false;
  const analysisOnly = data?.analysis_only !== false;
  const modelUsed: string | null = data?.model_used || null;
  const report = data?.assessment_report || {};
  const riskList: any[] = data?.risk_list || [];
  const blockerList: any[] = data?.blocker_list || [];
  const uncertaintyList: any[] = data?.uncertainty_list || [];
  const gapList: any[] = data?.validation_gap_list || [];
  const resourceNeeds: any[] = data?.resource_needs || [];

  // 风险按等级分组（L5→L0）
  const risksByLevel: Record<string, any[]> = {};
  riskList.forEach(r => {
    const lvl = (typeof r === 'object' && r?.risk_level) || 'L0';
    (risksByLevel[lvl] ||= []).push(r);
  });
  const parseError = report?.parse_error === true;

  return (
    <div style={{ fontSize: 13 }}>
      <h3 style={{ marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Icon name="stage" size={18} /> P2 评估 — 风险 · 可行性 · 阻塞项
      </h3>

      {/* 返工 / 阻塞 状态横幅 */}
      {isChangesRequested && (
        <div style={{ padding: '10px 14px', marginBottom: 12, background: 'var(--orange-bg)', border: '1px solid var(--orange)', borderRadius: 6, fontSize: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
          <Icon name="warning" size={16} style={{ color: 'var(--orange)' }} />
          <span style={{ flex: 1 }}>阶段需要返工。请依据 Gate 决策原因重新执行 P2 评估。</span>
          {onReExecute && <button className="btn sm" style={{ background: 'var(--orange)', color: '#fff' }} onClick={onReExecute}>重新执行</button>}
        </div>
      )}
      {/* WP-6：模型全失败强制中断 → 显式报错（模型不可用 / 中断阶段 / 原因 / 已尝试链路 / 操作） */}
      {data?.model_unavailable && (
        <ModelUnavailableBanner info={data.model_unavailable} onReExecute={onReExecute} />
      )}
      {isBlocked && !data?.model_unavailable && (
        <div style={{ padding: '10px 14px', marginBottom: 12, background: 'var(--red-bg)', border: '1px solid var(--red)', borderRadius: 6, fontSize: 12, color: 'var(--red)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <Icon name="blocked" size={16} />
          <span>阶段已被阻塞。P2 评估依赖有效模型（无 Key 不降级为规则评估），请确认模型配置或查看 Gate 决策原因。</span>
        </div>
      )}

      {/* 尚未评估：诚实空态 */}
      {!available && (
        <div style={{ ...card, display: 'flex', alignItems: 'center', gap: 10 }}>
          <Icon name="future" size={18} style={{ color: 'var(--color-text-muted)' }} />
          <span style={muted}>{data?.reason || 'P2 评估尚未执行。请先完成 P1 并批准 Gate 以触发 P2 评估。'}</span>
        </div>
      )}

      {available && (
        <>
          {/* analysis_only 醒目标记（§4.7-5 / STOP-4）：模型输出为辅助分析，非事实结论 */}
          {analysisOnly && (
            <div style={{ padding: '9px 13px', marginBottom: 12, background: 'var(--color-primary-soft)', border: '1px solid var(--color-primary-border)', borderRadius: 6, fontSize: 12, color: 'var(--color-primary)', display: 'flex', alignItems: 'center', gap: 8 }}>
              <Icon name="model" size={15} />
              <span style={{ flex: 1 }}>
                以下为模型<strong>辅助分析</strong>，非事实结论；不替代 Evidence、P3 规划或 P5 验证。
                {modelUsed ? <>（分析模型：<code style={{ fontFamily: 'var(--font-mono)' }}>{modelUsed}</code>）</> : <>（分析模型标识未记录）</>}
              </span>
            </div>
          )}
          {parseError && (
            <div style={{ padding: '9px 13px', marginBottom: 12, background: 'var(--color-warning-soft)', border: '1px solid var(--color-warning)', borderRadius: 6, fontSize: 12, color: 'var(--color-warning)', display: 'flex', alignItems: 'center', gap: 8 }}>
              <Icon name="warning" size={15} /> 模型输出未能结构化解析，以下 6 类产出可能不完整（评估节点会重试结构化输出）。
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 12 }}>

            {/* 1. 评估报告 */}
            <div style={{ ...card, gridColumn: '1 / -1' }}>
              <div style={cardTitle}><Icon name="docs" size={16} /> 评估报告</div>
              {(() => {
                const keys = Object.keys(report).filter(k => k !== 'parse_error' && k !== 'raw');
                if (report?.raw) {
                  return <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', maxHeight: 260, overflow: 'auto', padding: 8, background: 'var(--color-surface-subtle)', borderRadius: 4, margin: 0 }}>{String(report.raw).slice(0, 2000)}</pre>;
                }
                if (keys.length === 0) return <div style={muted}>无评估报告内容。</div>;
                return (
                  <div style={{ fontSize: 12, lineHeight: 1.7 }}>
                    {keys.map(k => (
                      <div key={k} style={{ marginBottom: 6 }}>
                        <span style={{ fontWeight: 600, color: 'var(--color-text-muted)' }}>{k}：</span>
                        <span>{typeof report[k] === 'object' ? JSON.stringify(report[k]) : String(report[k])}</span>
                      </div>
                    ))}
                  </div>
                );
              })()}
            </div>

            {/* 2. 风险清单（按 L0-L5 分组） */}
            <div style={card}>
              <div style={cardTitle}><Icon name="warning" size={16} /> 风险清单（{riskList.length}）</div>
              {riskList.length === 0 ? <div style={muted}>未识别风险项。</div> : (
                <div style={{ fontSize: 12 }}>
                  {RISK_ORDER.filter(lvl => risksByLevel[lvl]?.length).map(lvl => (
                    <div key={lvl} style={{ marginBottom: 10 }}>
                      <div style={{ fontSize: 11, fontWeight: 600, color: RISK_COLOR[lvl], marginBottom: 4 }}>
                        {RISK_LABELS[lvl] || lvl}（{risksByLevel[lvl].length}）
                      </div>
                      {risksByLevel[lvl].map((r, i) => (
                        <div key={i} style={{ padding: '4px 8px', marginBottom: 3, borderLeft: `3px solid ${RISK_COLOR[lvl]}`, background: 'var(--color-surface-subtle)', borderRadius: '0 4px 4px 0' }}>
                          <div>{itemText(r)}</div>
                          {typeof r === 'object' && (r.source || r.basis) && (
                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 2 }}>
                              {r.source && <>来源：{r.source} </>}{r.basis && <>· 依据：{r.basis}</>}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 3. 阻塞项清单 */}
            <div style={card}>
              <div style={cardTitle}><Icon name="blocked" size={16} /> 阻塞项清单（{blockerList.length}）</div>
              {blockerList.length === 0 ? <div style={{ ...muted, color: 'var(--green)' }}>无登记的阻塞项。</div> : (
                <div style={{ fontSize: 12 }}>
                  {blockerList.map((b, i) => (
                    <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid var(--color-border)' }}>
                      <div>{itemText(b)}</div>
                      {typeof b === 'object' && b.source && <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>来源：{b.source}</div>}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 4. 不确定项清单 */}
            <div style={card}>
              <div style={cardTitle}><Icon name="search" size={16} /> 不确定项清单（{uncertaintyList.length}）</div>
              {uncertaintyList.length === 0 ? <div style={muted}>无登记的不确定项。</div> : (
                <div style={{ fontSize: 12 }}>
                  {uncertaintyList.map((u, i) => (
                    <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid var(--color-border)' }}>{itemText(u)}</div>
                  ))}
                </div>
              )}
            </div>

            {/* 5. 验证缺口清单 */}
            <div style={card}>
              <div style={cardTitle}><Icon name="evidence" size={16} /> 验证缺口清单（{gapList.length}）</div>
              {gapList.length === 0 ? <div style={muted}>无登记的验证缺口。</div> : (
                <div style={{ fontSize: 12 }}>
                  {gapList.map((g, i) => (
                    <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid var(--color-border)' }}>
                      <div>{itemText(g)}</div>
                      {typeof g === 'object' && g.source && <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>来源：{g.source}</div>}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 6. 资源需求建议 */}
            <div style={card}>
              <div style={cardTitle}><Icon name="resource" size={16} /> 资源需求建议（{resourceNeeds.length}）</div>
              {resourceNeeds.length === 0 ? <div style={muted}>明确无额外资源需求。</div> : (
                <div style={{ fontSize: 12 }}>
                  {resourceNeeds.map((r, i) => (
                    <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid var(--color-border)' }}>{itemText(r)}</div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* 产物列表脚注 */}
          {Array.isArray(data?.artifacts) && data.artifacts.length > 0 && (
            <div style={{ marginTop: 12, ...muted, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Icon name="artifact" size={14} /> P2 产物：{data.artifacts.length} 个（{data.artifacts.map((a: string) => a.replace('artifacts/', '')).join('、')}）
            </div>
          )}
        </>
      )}
    </div>
  );
}
