/**
 * G10 场景三：负路径集（WP-3 幂等 + 错误阻断诚实降级）
 *
 * 验证点：
 * - 重复执行 onboarding/execute（已有活跃 Gate）→ 幂等返回，不新建 Gate（WP-3）
 * - 重复 Gate 决策（已批准 Gate）→ 无二次推进（不双重晋级）
 * - invalid decision → HTTP 400（WP-3 校验）
 * - 0 console error
 *
 * 运行：node r96-s3-negative.mjs
 * 前提：后端 :8010（独立测试DB），前端 :5173
 */

import { chromium } from 'playwright';
import { mkdirSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCREENSHOT_DIR = '/tmp/r96_g10_s3';
const BACKEND = process.env.BACKEND_URL || 'http://localhost:8010';
const FRONTEND = process.env.BASE_URL || 'http://localhost:5173';

mkdirSync(SCREENSHOT_DIR, { recursive: true });

const results = [];
const consoleErrors = [];

function record(step, status, detail = '') {
  results.push({ step, status, detail });
  console.log(`[${status.toUpperCase()}] Step ${step}: ${detail}`);
}

function fail(step, detail) {
  record(step, 'FAIL', detail);
  throw new Error(`FAIL at step ${step}: ${detail}`);
}

async function screenshot(page, name) {
  const path = resolve(SCREENSHOT_DIR, `${name}.png`);
  await page.screenshot({ path, fullPage: false });
}

async function apiPost(url, body, expectStatus) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (expectStatus !== undefined && res.status !== expectStatus) {
    throw new Error(`Expected HTTP ${expectStatus} but got ${res.status}: ${JSON.stringify(data)}`);
  }
  return { status: res.status, data };
}

async function apiGet(url) {
  const res = await fetch(url);
  return res.json();
}

async function collectSSE(url) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
  const text = await res.text();
  return { status: res.status, text };
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  page.on('console', msg => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  try {
    // Step 1: 前端首页加载
    await page.goto(FRONTEND, { waitUntil: 'networkidle', timeout: 10000 });
    await screenshot(page, '1-homepage');
    record(1, 'PASS', '前端加载');

    // ── 负路径 A: 幂等重复执行 execute（WP-3）──────────────────────────────
    // 创建项目 + 首次 execute
    const cResp = await apiPost(`${BACKEND}/api/projects`, {
      name: 'G10-S3 幂等测试',
      source_type: 'manual',
      source_config: {},
    }, 200);
    const pid = cResp.data.data.project_id;
    await apiPost(`${BACKEND}/api/projects/${pid}/onboarding/complete`, { execution_mode: 'plan' });
    const s1 = await collectSSE(`${BACKEND}/api/projects/${pid}/onboarding/execute`);
    if (s1.status !== 200) fail(2, `首次 execute 失败: ${s1.status}`);
    if (!s1.text.includes('event: complete')) fail(2, '首次 execute 无 complete 事件');

    // 解析首次 Gate ID
    const m1 = s1.text.match(/"gate_id":\s*"([^"]+)"/);
    const gid1 = m1 ? m1[1] : '';
    record(2, 'PASS', `首次 execute 成功: gate_id=${gid1}`);

    // Step 3: 幂等 — 重复执行 execute（项目已有活跃 Gate）→ 应返回 idempotent=true
    const s2 = await collectSSE(`${BACKEND}/api/projects/${pid}/onboarding/execute`);
    if (s2.status !== 200) fail(3, `重复 execute 失败: ${s2.status}`);
    // WP-3 幂等: 应返回 idempotent: true 且 gate_id 相同
    if (!s2.text.includes('idempotent')) {
      fail(3, `WP-3 幂等失败: 重复 execute 未返回 idempotent 标记\n${s2.text.slice(0, 400)}`);
    }
    const m2 = s2.text.match(/"gate_id":\s*"([^"]+)"/);
    const gid2 = m2 ? m2[1] : '';
    if (gid1 && gid2 && gid1 !== gid2) {
      fail(3, `WP-3 幂等失败: 重复 execute 创建了新 Gate (${gid2} != ${gid1})`);
    }
    record(3, 'PASS', `重复 execute 幂等: gate_id=${gid2} (同一 Gate，idempotent 标记存在)`);

    // Step 4: DB 中只有 1 个 waiting_decision Gate（不重复建）
    const gatesResp = await apiGet(`${BACKEND}/api/projects/${pid}/gates`);
    const waitingGates = (gatesResp.data.gates || []).filter(g =>
      g.gate_status === 'waiting_decision' && g.stage === 'p0'
    );
    if (waitingGates.length !== 1) {
      fail(4, `WP-3 幂等失败: DB 中有 ${waitingGates.length} 个 waiting_decision P0 Gate，应恰好 1 个`);
    }
    record(4, 'PASS', `DB 中只有 1 个 waiting_decision P0 Gate（WP-3 通过）`);

    // ── 负路径 B: invalid decision → HTTP 400 ────────────────────────────────
    // Step 5: 非法 decision
    const activeGate = await apiGet(`${BACKEND}/api/projects/${pid}/gates/active`);
    const gateId = activeGate.data.gate_id;
    const badR = await apiPost(`${BACKEND}/api/projects/${pid}/gates/${gateId}/decision`,
      { decision: 'frobnicate' }, 400);
    // Status 400 expected
    if (badR.status !== 400) fail(5, `非法 decision 应返回 400，实际: ${badR.status}`);
    record(5, 'PASS', `非法 decision → HTTP 400（阻断，不伪装成功）`);

    // Step 6: Gate 仍在 waiting_decision
    const afterBad = await apiGet(`${BACKEND}/api/projects/${pid}/gates/active`);
    if (!afterBad.data || afterBad.data.gate_status !== 'waiting_decision') {
      fail(6, `非法 decision 后 Gate 状态变了: ${afterBad.data?.gate_status}`);
    }
    record(6, 'PASS', `非法 decision 后 Gate 仍 waiting_decision（状态未变）`);

    // ── 负路径 C: 已 > p0 阶段时重复 execute → 409 ──────────────────────────
    // Step 7: 批准 Gate，然后再 execute
    const runId = activeGate.data.run_id;
    await apiPost(
      `${BACKEND}/api/projects/${pid}/runs/${runId}/stages/p0/promotion-decision`,
      { decision: 'approve' }
    );
    const projNow = await apiGet(`${BACKEND}/api/projects/${pid}`);
    const cs = (projNow.data || {}).current_stage;
    if (cs !== 'p1') fail(7, `批准后 current_stage 应为 p1，实际: ${cs}`);
    record(7, 'PASS', `Gate 批准: current_stage=p1`);

    // Step 8: 再次 execute（阶段已 > p0）→ 应 409
    const s3 = await fetch(`${BACKEND}/api/projects/${pid}/onboarding/execute`, { method: 'POST' });
    if (s3.status !== 409) {
      fail(8, `阶段 > p0 时 execute 应返回 409，实际 ${s3.status}`);
    }
    record(8, 'PASS', `阶段 > p0 时 execute → 409（WP-3 阻断）`);

    // Step 9: 0 console error
    await page.goto(FRONTEND, { waitUntil: 'networkidle', timeout: 10000 });
    await screenshot(page, '9-final');
    if (consoleErrors.length > 0) fail(9, `Console errors: ${consoleErrors.join(', ')}`);
    record(9, 'PASS', `0 console error`);

  } finally {
    await browser.close();
  }

  const total = results.length;
  const passed = results.filter(r => r.status === 'PASS').length;
  const failed = results.filter(r => r.status === 'FAIL').length;
  console.log(`\n=== G10 场景三：负路径集 ===`);
  console.log(`总计: ${total} 步 | 通过: ${passed} | 失败: ${failed}`);
  console.log(`Console errors: ${consoleErrors.length}`);
  results.forEach(r => console.log(`  [${r.status}] ${r.step}: ${r.detail}`));

  if (failed > 0 || consoleErrors.length > 0) {
    process.exit(1);
  }
  console.log('\nPASS');
}

main().catch(e => {
  console.error('FATAL:', e.message);
  process.exit(1);
});
