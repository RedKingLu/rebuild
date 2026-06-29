/**
 * G10 场景二：Git 失败路径（WP-1 修复结果验证）
 *
 * 验证点：
 * - 创建 git 项目（无凭据/不可达 URL）
 * - onboarding/execute → source_pending Gate（非 event:error 卡死）
 * - SSE 包含 source_pending + retry_action（WP-1 DoD）
 * - Gate reason 非空（用户有具体 reason 提示）
 * - manual 源码路径不受影响（对照组）
 * - 0 console error
 *
 * 运行：node r96-s2-git-fail.mjs
 * 前提：后端 :8010（独立测试DB），前端 :5173
 */

import { chromium } from 'playwright';
import { mkdirSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCREENSHOT_DIR = '/tmp/r96_g10_s2';
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

async function apiPost(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function apiGet(url) {
  const res = await fetch(url);
  return res.json();
}

async function collectSSE(url) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
  const text = await res.text();
  return text;
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
    record(1, 'PASS', '前端加载，0 console error');

    // Step 2: 创建 git 项目（不可达 URL，无凭据）
    const createResp = await apiPost(`${BACKEND}/api/projects`, {
      name: 'G10-S2 Git 失败路径测试',
      source_type: 'git',
      source_config: {
        remote_url: 'https://this-host-does-not-exist-g10.example/repo.git',
        branch: 'main',
      },
    });
    if (createResp.status !== 'success') fail(2, `Create project failed: ${JSON.stringify(createResp)}`);
    const pid = createResp.data.project_id;
    record(2, 'PASS', `Git 项目创建: ${pid}`);

    // Step 3: onboarding/complete（git 失败，但不应卡死）
    const completeResp = await apiPost(`${BACKEND}/api/projects/${pid}/onboarding/complete`, {
      execution_mode: 'plan',
    });
    if (completeResp.status !== 'success') fail(3, `complete failed: ${JSON.stringify(completeResp)}`);
    const runId = completeResp.data.run_id;
    record(3, 'PASS', `onboarding/complete: run_id=${runId}（git 失败后继续）`);

    // Step 4: onboarding/execute → 必须产出 source_pending Gate，不是 event:error 卡死
    const ssBody = await collectSSE(`${BACKEND}/api/projects/${pid}/onboarding/execute`);

    // V6 强规则：event:error 且无 event:complete 是卡死，不允许
    if (ssBody.includes('event: error') && !ssBody.includes('event: complete')) {
      fail(4, `WP-1 回归：SSE 包含 event:error 且无 event:complete（用户卡死）\n${ssBody.slice(0, 600)}`);
    }
    if (!ssBody.includes('event: complete')) {
      fail(4, `SSE 无 event:complete\n${ssBody.slice(0, 600)}`);
    }

    // WP-1 DoD: source_pending 出现在 SSE
    if (!ssBody.includes('source_pending')) {
      fail(4, `WP-1 失败：SSE 未包含 source_pending\n${ssBody.slice(0, 600)}`);
    }
    if (!ssBody.includes('retry_action')) {
      fail(4, `WP-1 失败：SSE 未包含 retry_action\n${ssBody.slice(0, 600)}`);
    }
    record(4, 'PASS', `execute SSE 包含 source_pending + retry_action（WP-1 通过）`);

    // Step 5: Gate 落库验证（gate_type = source_pending）
    const gatesResp = await apiGet(`${BACKEND}/api/projects/${pid}/gates`);
    const gates = (gatesResp.data || {}).gates || [];
    const pendingGates = gates.filter(g => g.gate_type === 'source_pending');
    if (pendingGates.length < 1) {
      fail(5, `DB 中无 source_pending Gate；实际: ${gates.map(g => g.gate_type).join(',')}`);
    }
    const pg = pendingGates[0];
    if (!pg.reason || pg.reason.trim().length === 0) fail(5, 'Gate reason 为空');
    record(5, 'PASS', `Gate DB: gate_type=source_pending, reason="${pg.reason.slice(0, 80)}"`);

    // Step 6: 对照组 — manual 路径不受影响
    const manualResp = await apiPost(`${BACKEND}/api/projects`, {
      name: 'G10-S2 Manual 对照',
      source_type: 'manual',
      source_config: {},
    });
    const mpid = manualResp.data.project_id;
    await apiPost(`${BACKEND}/api/projects/${mpid}/onboarding/complete`, { execution_mode: 'plan' });
    const mSSE = await collectSSE(`${BACKEND}/api/projects/${mpid}/onboarding/execute`);
    if (!mSSE.includes('stage_promotion')) {
      fail(6, `Manual 路径应产出 stage_promotion Gate，但未找到\n${mSSE.slice(0, 400)}`);
    }
    if (mSSE.includes('source_pending')) {
      fail(6, 'Manual 路径不应产出 source_pending Gate');
    }
    record(6, 'PASS', 'Manual 路径正常产出 stage_promotion Gate（不受 WP-1 影响）');

    // Step 7: 0 console error 最终检查
    await page.goto(FRONTEND, { waitUntil: 'networkidle', timeout: 10000 });
    await screenshot(page, '7-final');
    if (consoleErrors.length > 0) fail(7, `Console errors: ${consoleErrors.join(', ')}`);
    record(7, 'PASS', `0 console error`);

  } finally {
    await browser.close();
  }

  const total = results.length;
  const passed = results.filter(r => r.status === 'PASS').length;
  const failed = results.filter(r => r.status === 'FAIL').length;
  console.log(`\n=== G10 场景二：Git 失败路径 ===`);
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
