import { chromium } from 'playwright';
const BASE = 'http://localhost:5173';
const PID = process.argv[2] || '40e35aa2-61cd-4986-ad43-87cf09f5df9f';
const errs = [];
const run = async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await page.goto(`${BASE}/projects/${PID}/workspace`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3500);

  // UX-4.1: task-pipeline top bar shows the real stage pipeline (P0..P6)
  const pipelineStages = await page.locator('text=/P[0-6]/').count();
  console.log('1. task pipeline present (P* markers in bar):', pipelineStages >= 3, `(${pipelineStages} stage markers)`);

  // Switch to the central Agent tab (click Agent activity)
  await page.locator('button[title="Agent"]').first().click();
  await page.waitForTimeout(800);

  // UX-4.2: send a message that triggers tool calls, then verify tool blocks render
  const ta = page.locator('textarea').first();
  const toolBefore = await page.getByText('工具调用', { exact: false }).count();
  await ta.fill('一句话总结项目');
  await page.locator('button', { hasText: '发送' }).first().click();

  // Wait for tool execution blocks to appear (up to ~120s — real LLM + tools)
  let toolBlocks = 0;
  for (let i = 0; i < 60; i++) {
    await page.waitForTimeout(2000);
    toolBlocks = await page.getByText('工具调用', { exact: false }).count();
    if (toolBlocks > 0) break;
  }
  console.log('2. tool execution blocks rendered (🔧 工具调用):', toolBlocks > 0, `(${toolBlocks} blocks)`);

  // Wait for stream to finish (reply text present, no "回复中")
  let done = false;
  for (let i = 0; i < 40; i++) {
    await page.waitForTimeout(2000);
    const b = await page.innerText('body');
    if (!b.includes('回复中…') && (b.includes('Agent ·') || b.includes('项目'))) { done = true; break; }
  }
  console.log('3. agent finished replying:', done);

  // UX-4.3: specialist role badge + run status present in header
  const hasSpecialist = await page.locator('text=/执行 Agent|验收 Agent|Gate Agent/').count();
  console.log('4. specialist role badge visible:', hasSpecialist > 0, `(${hasSpecialist})`);

  await browser.close();
};
run().then(() => { console.log('\nCONSOLE ERRORS:', errs.length); errs.slice(0, 15).forEach(e => console.log('  ', e.slice(0, 160))); })
  .catch(e => { console.error('FATAL', String(e).slice(0, 300)); process.exit(1); });
