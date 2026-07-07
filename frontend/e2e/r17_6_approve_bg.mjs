// R17-6: GatePanel approve must close the modal IMMEDIATELY (HTTP returns 202,
// graph runs in background). Before the fix, HTTP blocked until the graph finished,
// leaving the modal stuck open for minutes.
import { chromium } from 'playwright';
const BASE = 'http://localhost:5173';
const API = 'http://localhost:8000';

async function _createProjectAndStartGraph() {
  // Create a fresh manual project (no git clone needed — fast)
  const pr = await fetch(`${API}/api/projects`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: `R176-${Date.now()}`, source_type: 'manual', source_config: {} }),
  });
  const pid = (await pr.json()).data.project_id;
  // Complete onboarding in auto mode → creates stage_promotion gate
  await fetch(`${API}/api/projects/${pid}/onboarding/complete`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ execution_mode: 'auto' }),
  });
  // Drive the graph to create the gate
  await fetch(`${API}/api/projects/${pid}/onboarding/execute`, { method: 'POST' });
  return pid;
}

const browser = await chromium.launch();
const ctx = await browser.newContext();
const page = await ctx.newPage();
const errors = [];
ctx.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
ctx.on('requestfailed', r => errors.push('FAILED ' + r.url()));

let result = 'UNKNOWN';
try {
  const pid = await _createProjectAndStartGraph();
  // Navigate directly to the workspace (domcontentloaded — SSE keeps network "busy")
  await page.goto(`${BASE}/projects/${pid}/workspace`, { waitUntil: 'domcontentloaded', timeout: 15000 });

  // Poll for the gate banner to appear (graph runs in background)
  const gateBanner = page.locator('text=阶段晋级 Gate').or(page.locator('text=项目启动确认')).first();
  await gateBanner.waitFor({ state: 'visible', timeout: 30000 }).catch(() => {});

  // Click banner to expand modal
  await gateBanner.click();
  await page.waitForTimeout(500);

  // Click approve
  const approveBtn = page.locator('button').filter({ hasText: '批准' }).first();
  const approveCount = await approveBtn.count();
  if (approveCount === 0) {
    throw Error('No 批准 button found in modal');
  }

  const t0 = Date.now();
  await approveBtn.click();

  // Wait for modal to close — the "Gate 审核" modal title should disappear
  await page.waitForFunction(() => !document.body.innerText.includes('Gate 审核'), { timeout: 5000 });
  const elapsed = Date.now() - t0;

  // Assert: modal closed quickly (HTTP returned immediately, graph in background)
  const modalGone = !(await page.evaluate(() => document.body.innerText.includes('Gate 审核')));
  if (modalGone && elapsed < 4000) {
    result = `PASS — modal closed in ${elapsed}ms (< 4s), graph runs in background`;
  } else {
    result = `FAIL — modalGone=${modalGone} elapsed=${elapsed}ms`;
  }
} catch (e) {
  result = 'FAIL — ' + e.message;
} finally {
  console.log('\n=== R17-6 approve-immediate-close ===');
  console.log('Result:', result);
  console.log('console errors:', errors.length);
  if (errors.length > 0) console.log(errors.slice(0, 5).join('\n'));
  await browser.close();
}
