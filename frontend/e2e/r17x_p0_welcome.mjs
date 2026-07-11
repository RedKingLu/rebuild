// R17-X P0 联调 E2E：验证「引导完成后不自动执行 P0 → 前端一次性欢迎页 → 点『开始』
// 触发 POST /onboarding/execute → P0 图启动 → 欢迎页消失」这条闭环真实可用（D-074 浏览器级证据）。
//
// 诚实边界：P0 真实执行需 LLM 真实调用。若 Key 失效导致图 escalate/失败，不影响本轮核心
// （欢迎页显示 + 点开始 + execute 请求发出 + 图启动）；脚本如实记录 P0 跑到哪一步，不伪造完成。
import { chromium } from 'playwright';
const BASE = 'http://localhost:5173';
const API = 'http://localhost:8000';
const SHOT = '/tmp';

const steps = [];
function rec(name, ok, detail) { steps.push({ name, ok, detail }); console.log(`[${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`); }

const browser = await chromium.launch();
const ctx = await browser.newContext();
const page = await ctx.newPage();
const consoleErrors = [];
ctx.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
ctx.on('requestfailed', r => { const u = r.url(); if (!u.includes('/events') && !u.includes('/stream')) consoleErrors.push('REQFAIL ' + u); });

let pid = '';
try {
  // ── Step 1：API 建项目（manual，无 git，快） ──
  const pr = await fetch(`${API}/api/projects`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: `R17X-${Date.now()}`, source_type: 'manual', source_config: {} }),
  });
  pid = (await pr.json()).data.project_id;
  rec('Step1 建项目', !!pid, `project_id=${pid}`);

  // ── Step 2：complete（plan 模式）→ 不自动执行 P0 ──
  const cr = await fetch(`${API}/api/projects/${pid}/onboarding/complete`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ execution_mode: 'plan' }),
  });
  const cj = (await cr.json()).data;
  const contractOk = cj.graph_driven === false && cj.gate_id === null && (cj.review?.passed ?? null) === null;
  rec('Step2a complete 契约(graph_driven=false/gate_id=null/review.passed=null)', contractOk,
      `graph_driven=${cj.graph_driven} gate_id=${cj.gate_id} review.passed=${cj.review?.passed} next=${JSON.stringify(cj.next)}`);

  // 后端断言：workspace 无 active P0 gate、p0=pending、onboarding_done=true
  const ws1 = (await (await fetch(`${API}/api/projects/${pid}/workspace`)).json()).data;
  const p0v1 = ws1.active_run?.stage_status?.p0 ?? null;
  const noGate = ws1.active_gate == null;
  rec('Step2b 无 active P0 gate + p0=pending + onboarding_done', noGate && p0v1 === 'pending' && ws1.project?.onboarding_done === true,
      `active_gate=${JSON.stringify(ws1.active_gate)} p0=${p0v1} onboarding_done=${ws1.project?.onboarding_done}`);

  // ── Step 3：进入 Workspace，确认欢迎页可见 ──
  await page.goto(`${BASE}/projects/${pid}/workspace`, { waitUntil: 'domcontentloaded', timeout: 20000 });
  const welcome = page.getByText('欢迎进入 rebuild 平台', { exact: false }).first();
  await welcome.waitFor({ state: 'visible', timeout: 20000 });
  const startBtn = page.getByRole('button', { name: /开始/ }).first();
  const startVisible = await startBtn.isVisible().catch(() => false);
  const welcomeVisible = await welcome.isVisible();
  rec('Step3 欢迎页 + 「开始」按钮可见', welcomeVisible && startVisible, `welcomeVisible=${welcomeVisible} startBtnVisible=${startVisible}`);
  await page.screenshot({ path: `${SHOT}/r17x_p0_1_welcome.png`, fullPage: true });

  // ── Step 4：点击「开始」→ 前端发 POST /onboarding/execute → P0 图启动 ──
  const execReqP = page.waitForRequest(r => r.url().includes('/onboarding/execute') && r.method() === 'POST', { timeout: 15000 });
  await startBtn.click();
  let execFired = false, execUrl = '';
  try { const rq = await execReqP; execFired = true; execUrl = rq.url(); } catch { /* not fired */ }
  rec('Step4a 点击「开始」触发 POST /onboarding/execute', execFired, execUrl.replace(API, '').replace(BASE, ''));

  // 轮询后端确认 P0 图启动：p0 脱离 pending（in_progress/waiting_gate/...）或出现 gate。
  let p0v2 = p0v1, gate2 = null, started = false;
  for (let i = 0; i < 40; i++) {
    const ws = (await (await fetch(`${API}/api/projects/${pid}/workspace`)).json()).data;
    p0v2 = ws.active_run?.stage_status?.p0 ?? null;
    gate2 = ws.active_gate;
    if ((p0v2 && p0v2 !== 'pending') || gate2) { started = true; break; }
    await page.waitForTimeout(1500);
  }
  rec('Step4b P0 图启动（p0 脱离 pending 或出现 Gate）', started, `p0=${p0v2} active_gate.type=${gate2?.gate_type ?? null} active_gate.status=${gate2?.gate_status ?? null}`);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOT}/r17x_p0_2_after_start.png`, fullPage: true });

  // ── Step 5：欢迎页消失 ──
  await page.waitForTimeout(500);
  const welcomeGone = !(await page.getByText('欢迎进入 rebuild 平台', { exact: false }).first().isVisible().catch(() => false));
  rec('Step5 P0 启动后欢迎页不再显示', welcomeGone, `welcomeGone=${welcomeGone}`);
  await page.screenshot({ path: `${SHOT}/r17x_p0_3_welcome_gone.png`, fullPage: true });

} catch (e) {
  rec('EXCEPTION', false, e.message);
} finally {
  console.log('\n=== R17-X P0 欢迎页联调 E2E 汇总 ===');
  console.log('project_id:', pid);
  const core = steps.filter(s => ['Step3 欢迎页 + 「开始」按钮可见', 'Step4a 点击「开始」触发 POST /onboarding/execute', 'Step4b P0 图启动（p0 脱离 pending 或出现 Gate）', 'Step5 P0 启动后欢迎页不再显示'].some(k => s.name === k));
  console.log('核心闭环(欢迎页→点开始→execute→图启动→欢迎消失):', core.every(s => s.ok) ? 'ALL PASS' : 'HAS FAIL');
  console.log('Console errors:', consoleErrors.length);
  if (consoleErrors.length) console.log(consoleErrors.slice(0, 8).join('\n'));
  console.log('screenshots: /tmp/r17x_p0_1_welcome.png, /tmp/r17x_p0_2_after_start.png, /tmp/r17x_p0_3_welcome_gone.png');
  await browser.close();
}
