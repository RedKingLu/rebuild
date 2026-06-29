/**
 * G10 场景四：持久性验证（WP-2 落库 + WP-6 checkpoint 跨重启）
 *
 * 验证点：
 * - 完成 P0 Gate 批准（current_stage=p1）
 * - 记录 run_id、gate_id、evidence 条数、checkpoint_ref
 * - 重启后端（通过信号停止 + 重新启动）
 * - 刷新前端
 * - 所有状态/阶段/Gate/Evidence/checkpoint 完整恢复（WP-2 落库 + WP-6 checkpoint）
 *
 * !! 注意 !! 本脚本通过环境变量接收 BACKEND_PID 来重启后端进程。
 *           若未设置 BACKEND_PID，跳过重启测试步骤（标记为 SKIPPED）。
 *
 * 运行：
 *   BACKEND_PID=<pid> node r96-s4-persist.mjs
 *   或无重启测试：node r96-s4-persist.mjs
 *
 * 前提：后端 :8010（独立测试DB），前端 :5173
 */

import { chromium } from 'playwright';
import { mkdirSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { exec } from 'child_process';
import { promisify } from 'util';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCREENSHOT_DIR = '/tmp/r96_g10_s4';
const BACKEND = process.env.BACKEND_URL || 'http://localhost:8010';
const FRONTEND = process.env.BASE_URL || 'http://localhost:5173';
const BACKEND_PID = process.env.BACKEND_PID ? parseInt(process.env.BACKEND_PID, 10) : null;
const BACKEND_START_CMD = process.env.BACKEND_START_CMD || null;

const execAsync = promisify(exec);

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

function skip(step, detail) {
  results.push({ step, status: 'SKIP', detail });
  console.log(`[SKIP] Step ${step}: ${detail}`);
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
  return { status: res.status, text };
}

async function waitForBackend(timeoutMs = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(`${BACKEND}/api/health`);
      if (res.ok) return true;
    } catch (_) {
      // not ready yet
    }
    await new Promise(r => setTimeout(r, 500));
  }
  return false;
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  page.on('console', msg => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  let pid = null;
  let runId = null;
  let gateId = null;
  let checkpointRef = null;
  let evidenceCount = 0;
  let projectId = null;

  try {
    // Step 1: 前端首页加载
    await page.goto(FRONTEND, { waitUntil: 'networkidle', timeout: 10000 });
    await screenshot(page, '1-homepage');
    record(1, 'PASS', '前端加载');

    // Step 2: 创建 manual 项目 + complete
    const cResp = await apiPost(`${BACKEND}/api/projects`, {
      name: 'G10-S4 持久性测试',
      source_type: 'manual',
      source_config: {},
    });
    if (cResp.status !== 'success') fail(2, `Create project failed: ${JSON.stringify(cResp)}`);
    projectId = cResp.data.project_id;
    const completeResp = await apiPost(`${BACKEND}/api/projects/${projectId}/onboarding/complete`, {
      execution_mode: 'plan',
    });
    if (completeResp.status !== 'success') fail(2, `complete failed: ${JSON.stringify(completeResp)}`);
    runId = completeResp.data.run_id;
    record(2, 'PASS', `项目 ${projectId} run_id=${runId}`);

    // Step 3: onboarding/execute → 获取 gate_id + checkpoint_ref
    const ssR = await collectSSE(`${BACKEND}/api/projects/${projectId}/onboarding/execute`);
    if (ssR.status !== 200) fail(3, `execute status=${ssR.status}`);
    if (!ssR.text.includes('event: complete')) fail(3, '无 event:complete');
    const m = ssR.text.match(/event: complete\ndata: ({.*})/);
    if (!m) fail(3, '无法解析 complete event');
    const cData = JSON.parse(m[1]);
    gateId = cData.gate_id;
    checkpointRef = cData.checkpoint_ref;
    if (!checkpointRef) fail(3, 'checkpoint_ref 为空（WP-6 失败）');
    if (checkpointRef !== runId) fail(3, `checkpoint_ref(${checkpointRef}) != run_id(${runId})`);
    record(3, 'PASS', `execute: gate_id=${gateId}, checkpoint_ref=${checkpointRef}`);

    // Step 4: Evidence 落库（WP-2 基准）
    const evResp = await apiGet(`${BACKEND}/api/projects/${projectId}/evidence`);
    const evList = (evResp.data || {}).evidence || [];
    evidenceCount = evList.length;
    if (evidenceCount < 1) fail(4, `Evidence 为空（WP-2 失败）`);
    record(4, 'PASS', `Evidence 落库基准: ${evidenceCount} 条`);

    // Step 5: 批准 Gate → current_stage=p1
    await apiPost(
      `${BACKEND}/api/projects/${projectId}/runs/${runId}/stages/p0/promotion-decision`,
      { decision: 'approve' }
    );
    const projCheck = await apiGet(`${BACKEND}/api/projects/${projectId}`);
    const csBefore = (projCheck.data || {}).current_stage;
    if (csBefore !== 'p1') fail(5, `批准后 current_stage=${csBefore}，期望 p1`);
    record(5, 'PASS', `P0 Gate 批准: current_stage=p1`);

    // Step 6: 后端重启验证（若 BACKEND_PID 已设置）
    if (!BACKEND_PID || !BACKEND_START_CMD) {
      skip(6, `BACKEND_PID / BACKEND_START_CMD 未设置，跳过重启测试`);
      skip(7, `跳过（依赖步骤 6）`);
      skip(8, `跳过（依赖步骤 6）`);
      skip(9, `跳过（依赖步骤 6）`);
    } else {
      // Kill backend
      try {
        process.kill(BACKEND_PID, 'SIGTERM');
      } catch (e) {
        fail(6, `无法发送 SIGTERM 到 PID ${BACKEND_PID}: ${e.message}`);
      }
      await new Promise(r => setTimeout(r, 1500));

      // Restart backend
      exec(BACKEND_START_CMD, { detached: true, stdio: 'ignore' });
      const ready = await waitForBackend(25000);
      if (!ready) fail(6, '后端重启后 30s 内未响应 /api/health');
      record(6, 'PASS', '后端重启成功');

      // Step 7: 状态恢复验证
      const projAfter = await apiGet(`${BACKEND}/api/projects/${projectId}`);
      const csAfter = (projAfter.data || {}).current_stage;
      if (csAfter !== 'p1') fail(7, `重启后 current_stage=${csAfter}，期望 p1`);
      record(7, 'PASS', `重启后 current_stage=p1（DB 持久化通过）`);

      // Step 8: Evidence 恢复验证（WP-2）
      const evAfter = await apiGet(`${BACKEND}/api/projects/${projectId}/evidence`);
      const evListAfter = (evAfter.data || {}).evidence || [];
      if (evListAfter.length !== evidenceCount) {
        fail(8, `重启后 evidence=${evListAfter.length}，期望 ${evidenceCount}（WP-2 落库验证失败）`);
      }
      record(8, 'PASS', `重启后 Evidence 恢复: ${evListAfter.length} 条（WP-2 通过）`);

      // Step 9: Gate 与 checkpoint_ref 恢复（WP-6）
      // Use gates list endpoint (individual /gates/{id} not exposed)
      const gatesAfter = await apiGet(`${BACKEND}/api/projects/${projectId}/gates`);
      const gateList = (gatesAfter.data || {}).gates || [];
      const gateAfterItem = gateList.find(g => g.gate_id === gateId);
      if (!gateAfterItem) {
        fail(9, `重启后未找到 Gate ${gateId}（WP-6 失败）`);
      }
      if (gateAfterItem.checkpoint_ref !== checkpointRef) {
        fail(9, `重启后 Gate checkpoint_ref=${gateAfterItem.checkpoint_ref}，期望 ${checkpointRef}（WP-6 失败）`);
      }
      record(9, 'PASS', `重启后 Gate checkpoint_ref=${gateAfterItem.checkpoint_ref} 恢复（WP-6 通过）`);
    }

    // Step 10: 前端刷新 + 0 console error
    await page.reload({ waitUntil: 'networkidle', timeout: 10000 });
    await screenshot(page, '10-after-reload');
    if (consoleErrors.length > 0) fail(10, `Console errors: ${consoleErrors.join(', ')}`);
    record(10, 'PASS', `前端刷新: 0 console error`);

  } finally {
    await browser.close();
  }

  const total = results.length;
  const passed = results.filter(r => r.status === 'PASS').length;
  const skipped = results.filter(r => r.status === 'SKIP').length;
  const failed = results.filter(r => r.status === 'FAIL').length;

  console.log(`\n=== G10 场景四：持久性验证 ===`);
  console.log(`总计: ${total} 步 | 通过: ${passed} | 跳过: ${skipped} | 失败: ${failed}`);
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
