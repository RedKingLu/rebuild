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

  await page.locator('button[title="Agent"]').first().click();
  await page.waitForTimeout(600);

  const body = await page.innerText('body');

  // UX-4 corrected: NO 7-stage pipeline progress bar (接入/P0-P6 stage flow)
  const hasOldPipeline = body.includes('任务') && /P0.*›.*P6|接入.*画像.*评估/.test(body);
  const hasTaskOverview = await page.getByText('当前任务', { exact: false }).count();
  const hasTaskContextLabel = await page.getByText('执行 Agent', { exact: false }).count()
    || body.includes('执行 Agent');
  console.log('1. old 7-stage pipeline ABSENT:', !hasOldPipeline);
  console.log('2. task overview present (当前任务):', hasTaskOverview > 0);
  console.log('3. specialist role in bar (执行 Agent):', hasTaskContextLabel > 0);

  // Send a message -> status should update to a real-time phase (思考中/🔧/完成)
  const ta = page.locator('textarea').first();
  await ta.fill('一句话总结');
  await page.locator('button', { hasText: '发送' }).first().click();

  // Within a few seconds the status indicator should show 思考中 or 🔧 (real-time)
  let sawStatus = false;
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(1500);
    const b = await page.innerText('body');
    if (/思考中|🔧|调用工具|等待 Gate|本轮完成/.test(b)) { sawStatus = true; break; }
  }
  console.log('4. live real-time status updates during stream:', sawStatus);

  // wait for completion
  for (let i = 0; i < 40; i++) {
    await page.waitForTimeout(2000);
    if (!await page.innerText('body').then(b => b.includes('回复中…'))) break;
  }
  const bodyEnd = await page.innerText('body');
  const hasDone = /本轮完成|✅/.test(bodyEnd);
  console.log('5. status reaches done/completion:', hasDone);

  await browser.close();
};
run().then(() => { console.log('\nCONSOLE ERRORS:', errs.length); errs.slice(0, 12).forEach(e => console.log('  ', e.slice(0, 160))); })
  .catch(e => { console.error('FATAL', String(e).slice(0, 300)); process.exit(1); });
