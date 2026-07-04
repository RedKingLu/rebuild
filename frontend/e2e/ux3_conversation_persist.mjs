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

  const centerText = async () => (await page.locator('.tabbar').innerText()).replace(/\s+/g, ' ').trim();

  // 1. Open Agent sidebar (left activity)
  await page.locator('button[title="Agent"]').first().click();
  await page.waitForTimeout(1200);
  const sidebarText1 = await page.locator('[class*="tree"] , .empty').first().innerText().catch(() => '');
  const sidebarVisible = await page.getByText('对话历史', { exact: false }).count();
  console.log('1. Agent sidebar shows 对话历史:', sidebarVisible > 0);

  // 2. Send a message via the chat input (central Agent tab)
  await page.locator('button[title="Agent"]').first().click(); // ensure central agent tab
  await page.waitForTimeout(500);
  const ta = page.locator('textarea').first();
  await ta.fill('你好');
  await page.locator('button', { hasText: '发送' }).first().click();
  // wait for a non-trivial agent reply (up to ~120s)
  let replied = false;
  for (let i = 0; i < 60; i++) {
    await page.waitForTimeout(2000);
    // streaming indicator gone & some agent bubble appeared
    const agentBubbles = await page.locator('div', { hasText: 'Agent' }).count();
    const body = await page.innerText('body');
    if (body.includes('Agent ·') && !body.includes('回复中…')) { replied = true; break; }
  }
  console.log('2. agent replied in chat:', replied);

  // 3. Sidebar should now list the new conversation
  await page.locator('button[title="Agent"]').first().click();
  await page.waitForTimeout(800);
  const hasConvo = await page.getByText('执行 Agent', { exact: false }).count();
  const newBtn = await page.getByText('＋ 新建', { exact: false }).count();
  console.log('3. sidebar lists specialist convo (执行 Agent present):', hasConvo > 0, '| ＋ 新建:', newBtn > 0);

  // 4. Reload page -> open Agent sidebar -> conversation persists (history remains)
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3500);
  await page.locator('button[title="Agent"]').first().click();
  await page.waitForTimeout(1200);
  const hasConvoAfterReload = await page.getByText('执行 Agent', { exact: false }).count();
  console.log('4. conversation persists after reload:', hasConvoAfterReload > 0);

  await browser.close();
};
run().then(() => { console.log('\nCONSOLE ERRORS:', errs.length); errs.slice(0, 15).forEach(e => console.log('  ', e.slice(0, 160))); })
  .catch(e => { console.error('FATAL', String(e).slice(0, 300)); process.exit(1); });
