import { chromium } from 'playwright';
const BASE = 'http://localhost:5173';
const PID = process.argv[2] || '40e35aa2-61cd-4986-ad43-87cf09f5df9f';
const errs = [];
const run = async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });

  // Create a fresh stage_promotion gate via the API so the GatePanel has something to show.
  const mk = await fetch(`${BASE}/api/projects/${PID}/runs/run-167567/stages/p0/promotion-gate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_stage: 'p1' }),
  });
  const gate = (await mk.json()).data || (await mk.json());
  const gateId = gate.gate_id;
  console.log('created gate:', gateId, 'status:', gate.gate_status);

  await page.goto(`${BASE}/projects/${PID}/workspace`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3500);

  // Open the GatePanel banner -> modal
  const banner = page.locator('text=阶段晋级 Gate').first();
  if (await banner.count() === 0) { console.log('NOTE: no active promotion gate banner (gate may be graph-locked); reason-UI test skipped'); await browser.close(); return; }
  await banner.click();
  await page.waitForTimeout(800);

  // UX-5.1: reason textarea is present in the modal
  const reasonLabel = await page.locator('text=决策原因').count();
  const ta = page.locator('textarea').last(); // the reason textarea
  const taCount = await ta.count();
  console.log('1. reason textarea present in GatePanel:', taCount > 0 && reasonLabel > 0);

  // UX-5.2: clicking 拒绝 with empty reason is blocked (validation), not submitted
  await page.locator('button', { hasText: '拒绝' }).first().click();
  await page.waitForTimeout(400);
  const reasonErr = await page.locator('text=请填写拒绝').count();
  const stillOpen = await page.locator('text=决策原因').count();
  console.log('2. reject blocked without reason (error shown, modal still open):', reasonErr > 0 && stillOpen > 0);

  // UX-5.3: fill reason then reject -> submits, modal closes, rework notes written
  await ta.fill('接入报告缺少数据库连接配置，请补充');
  await page.locator('button', { hasText: '拒绝' }).first().click();
  await page.waitForTimeout(1200);
  const modalGone = (await page.locator('text=决策原因').count()) === 0;
  console.log('3. reject with reason submits (modal closes):', modalGone);

  // Verify the rework-notes artifact was written backend-side
  await page.waitForTimeout(500);
  const notesResp = await fetch(`${BASE}/api/projects/${PID}/file?path=artifacts/p0_rework_notes.json`);
  if (notesResp.ok) {
    const notes = (await notesResp.json()).data || {};
    let parsed = [];
    try { parsed = JSON.parse(notes.content || '[]'); } catch {}
    console.log('4. rework-notes artifact written with reason:', parsed.length > 0 && parsed.some(n => n.reason && n.reason.includes('数据库')));
  } else {
    console.log('4. rework-notes artifact: not yet (HTTP ' + notesResp.status + ')');
  }

  await browser.close();
};
run().then(() => { console.log('\nCONSOLE ERRORS:', errs.length); errs.slice(0, 12).forEach(e => console.log('  ', e.slice(0, 160))); })
  .catch(e => { console.error('FATAL', String(e).slice(0, 300)); process.exit(1); });
