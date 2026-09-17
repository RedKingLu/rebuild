/** Scenario package API service (R20-2-03).
 *
 * Sole frontend consumer of `GET /api/scenarios`. The backend endpoint returns a raw
 * (non-enveloped) dict (see routes_scenarios.py) with 19 keys per list item — including
 * `skill_body` / `anchors_text` / `risks_text` (≤~4000 chars each, R20-3 injection payload)
 * and `directory` (a server-side absolute filesystem path). The frontend only needs 4 keys
 * for the suggestion list — this service deliberately narrows the shape here rather than
 * asking the backend to change its already-accepted API contract (Q-R20-2-5, 方案 A):
 * we neither render nor store `directory` or the full skill text in the browser.
 *
 * Scenario ids are NEVER hardcoded here — the suggestion list is always the live result
 * of this call (AGENTS §10-27: no closed enum / fixed dropdown list for scenarios).
 */
import { get, unwrap } from './client';

export interface ScenarioOption {
  scenario_id: string;
  display_name: string;
  tier: string;
  summary: string;
}

export interface ListScenariosResult {
  scenarios: ScenarioOption[];
  discovery_status: string;
  /** 目录不完整而被跳过的场景包（scenario_loader.py: {dir, code, message}）。`dir` 只是
   *  场景包目录名本身（不含路径），不是绝对路径 —— 与场景项的 `directory` 键（服务器绝对
   *  路径，本 service 不取）是两件不同的事。 */
  invalid: Array<{ dir?: string; code?: string; message?: string }>;
}

interface RawScenarioListItem {
  scenario_id: string;
  display_name?: string;
  tier?: string;
  summary?: string;
  [key: string]: unknown; // skill_body / anchors_text / risks_text / directory / … — 均不取用
}

interface RawScenarioListResponse {
  scenarios?: RawScenarioListItem[];
  discovery_status?: string;
  invalid?: Array<{ dir?: string; code?: string; message?: string }>;
  root?: string; // 服务器绝对路径 —— 本 service 不透传给调用方
  notices?: unknown[];
}

/** GET /api/scenarios — 每次真扫目录，无缓存；用户新建/改写场景包后立即可见（R20-3-02）。 */
export async function listScenarios(): Promise<ListScenariosResult> {
  const resp = await get<RawScenarioListResponse>('/scenarios');
  const raw = unwrap(resp) as RawScenarioListResponse;
  const scenarios: ScenarioOption[] = (raw.scenarios || []).map((s) => ({
    scenario_id: s.scenario_id,
    display_name: s.display_name || s.scenario_id,
    tier: s.tier || 'open',
    summary: s.summary || '',
  }));
  return {
    scenarios,
    discovery_status: raw.discovery_status || 'unknown',
    invalid: raw.invalid || [],
  };
}
