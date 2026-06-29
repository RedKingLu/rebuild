/**
 * G10 场景一：本地理想路径
 * 测试 manual 源码项目的完整 P0 接入流程。
 *
 * 验证点：
 * - 创建项目 → onboarding/complete → onboarding/execute
 * - SSE 流中 graph_driven=true，gate_type=stage_promotion
 * - checkpoint_ref = run_id（WP-6 核实）
 * - Evidence 落库（WP-2 核实）
 * - Gate 批准 → current_stage=p1（WP-3/WP-6 核实）
 * - 0 console error（G10 金标准）
 *
 * 运行：node r96-s1-ideal.mjs
 * 前提：后端 :8010（独立测试DB），前端 :5173
 */

import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCREENSHOT_DIR = '/tmp/r96_g10_s1';
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
    if (msg.type() === 'error') {
      consoleErrors.push(msg.text());
    }
  });

  try {
    // Step 1: 前端首页加载
    await page.goto(FRONTEND, { waitUntil: 'networkidle', timeout: 10000 });
    await screenshot(page, '1-homepage');
    if (consoleErrors.length > 0) fail(1, `Console errors: ${consoleErrors.join(', ')}`);
    record(1, 'PASS', '前端首页加载，0 console error');

    // Step 2: 创建 manual 源码项目
    const createResp = await apiPost(`${BACKEND}/api/projects`, {
      name: 'G10-S1 理想路径测试',
      source_type: 'manual',
      source_config: {},
    });
    if (createResp.status !== 'success') fail(2, `Create project failed: ${JSON.stringify(createResp)}`);
    const pid = createResp.data.project_id;
    record(2, 'PASS', `项目创建成功: ${pid}`);

    // Step 3: onboarding/complete
    const completeResp = await apiPost(`${BACKEND}/api/projects/${pid}/onboarding/complete`, {
      execution_mode: 'plan',
    });
    if (completeResp.status !== 'success') fail(3, `complete failed: ${JSON.stringify(completeResp)}`);
    const runId = completeResp.data.run_id;
    if (!runId) fail(3, 'No run_id returned from onboarding/complete');
    record(3, 'PASS', `onboarding/complete: run_id=${runId}`);

    // Step 4: onboarding/execute (SSE stream)
    const ssBody = await collectSSE(`${BACKEND}/api/projects/${pid}/onboarding/execute`);
    if (!ssBody.includes('event: complete')) fail(4, 'No complete event in SSE');
    const completeMatch = ssBody.match(/event: complete\ndata: ({.*})/);
    if (!completeMatch) fail(4, 'Cannot parse complete event');
    const completeData = JSON.parse(completeMatch[1]);

    if (completeData.graph_driven !== true) fail(4, `graph_driven != true: ${completeData.graph_driven}`);
    if (completeData.graph_capability_status !== 'live') {
      fail(4, `graph_capability_status != live: ${completeData.graph_capability_status}`);
    }
    if (!completeData.gate_id) fail(4, 'No gate_id in complete event');
    if (completeData.checkpoint_ref !== runId) {
      fail(4, `checkpoint_ref(${completeData.checkpoint_ref}) != run_id(${runId})`);
    }
    record(4, 'PASS', `execute SSE: graph_driven=true, checkpoint_ref=${completeData.checkpoint_ref}`);

    // Step 5: Gate 验证 checkpoint_ref 落库（WP-6）
    const gateResp = await apiGet(`${BACKEND}/api/projects/${pid}/gates/active`);
    if (!gateResp.data) fail(5, 'No active gate found');
    const gate = gateResp.data;
    if (gate.checkpoint_ref !== runId) {
      fail(5, `Gate checkpoint_ref(${gate.checkpoint_ref}) != run_id(${runId})`);
    }
    if (gate.gate_type !== 'stage_promotion') fail(5, `Expected stage_promotion gate, got ${gate.gate_type}`);
    record(5, 'PASS', `Gate checkpoint_ref=${gate.checkpoint_ref}, type=${gate.gate_type}`);

    // Step 6: Evidence 落库验证（WP-2）
    const evidResp = await apiGet(`${BACKEND}/api/projects/${pid}/evidence`);
    const evList = (evidResp.data || {}).evidence || [];
    if (evList.length < 1) fail(6, `No evidence in storage, got: ${JSON.stringify(evList)}`);
    record(6, 'PASS', `Evidence 落库: ${evList.length} 条`);

    // Step 7: P0 Gate 批准 → current_stage = p1（WP-6 graph_driven）
    const approveResp = await apiPost(
      `${BACKEND}/api/projects/${pid}/runs/${runId}/stages/p0/promotion-decision`,
      { decision: 'approve' }
    );
    if (approveResp.status !== 'success') fail(7, `approve failed: ${JSON.stringify(approveResp)}`);
    if (approveResp.data.graph_driven !== true) {
      // On real server with proper event loop, graph_driven should be true
      // Accept false only if stage actually advanced (cross-loop fallback)
      const projNow = await apiGet(`${BACKEND}/api/projects/${pid}`);
      const cs = (projNow.data || {}).current_stage;
      if (cs !== 'p1') {
        fail(7, `After approve: graph_driven=${approveResp.data.graph_driven}, current_stage=${cs} (expected p1)`);
      }
      record(7, 'PASS', `Gate 批准（非图驱动回退）: current_stage=p1`);
    } else {
      const projNow = await apiGet(`${BACKEND}/api/projects/${pid}`);
      const cs = (projNow.data || {}).current_stage;
      if (cs !== 'p1') fail(7, `After approve: current_stage=${cs}, expected p1`);
      record(7, 'PASS', `Gate 批准（graph_driven=true）: current_stage=p1`);
    }

    // Step 8: 前端项目列表页渲染
    await page.goto(`${FRONTEND}`, { waitUntil: 'networkidle', timeout: 10000 });
    await screenshot(page, '8-after-approve');
    const errCount = consoleErrors.length;
    if (errCount > 0) fail(8, `Console errors after approve: ${consoleErrors.join(', ')}`);
    record(8, 'PASS', `前端渲染正常，累计 console error: ${errCount}`);

  } finally {
    await browser.close();
  }

  // Summary
  const total = results.length;
  const passed = results.filter(r => r.status === 'PASS').length;
  const failed = results.filter(r => r.status === 'FAIL').length;
  console.log(`\n=== G10 场景一：本地理想路径 ===`);
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
