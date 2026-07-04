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

  // Open the 文件 activity
  await page.locator('button[title="文件"]').first().click();
  await page.waitForTimeout(1200);

  const tabCount = async () => page.locator('.tabbar .tab').count();
  const tabTitles = async () => (await page.locator('.tabbar .tab').allInnerTexts()).map(s => s.replace(/×/g, '').trim());

  const baseline = await tabCount();
  console.log('baseline tabs:', baseline, JSON.stringify(await tabTitles()));

  // Collect file leaves (.node that are not directories/root)
  const leaves = page.locator('.tree .node:not(.root)');
  const n = await leaves.count();
  console.log('file leaves found:', n);
  if (n < 2) { console.log('SKIP: need >=2 files to prove singleton'); await browser.close(); return; }

  // Open first file
  await leaves.nth(0).click();
  await page.waitForTimeout(1000);
  const afterA = await tabCount();
  const titleA = (await tabTitles()).join('|');
  console.log('after open file #1: tabs =', afterA, '|', titleA);

  // Open a DIFFERENT file
  await leaves.nth(1).click();
  await page.waitForTimeout(1000);
  const afterB = await tabCount();
  const titleB = (await tabTitles()).join('|');
  console.log('after open file #2: tabs =', afterB, '|', titleB);

  // Assertions
  const singleton = (afterA === baseline + 1) && (afterB === afterA);
  console.log(singleton
    ? `PASS singleton: opening a 2nd file did NOT add a tab (baseline ${baseline} → ${afterA} → ${afterB})`
    : `FAIL singleton: baseline ${baseline}, afterA ${afterA}, afterB ${afterB}`);

  // Edit/preview toggle present in FileView
  const hasPreview = await page.getByRole('button', { name: '预览' }).count();
  const hasEdit = await page.getByRole('button', { name: '编辑' }).count();
  console.log(`toggle: 预览=${hasPreview} 编辑=${hasEdit} → ${hasPreview > 0 && hasEdit > 0 ? 'PASS' : 'FAIL'}`);

  // Try switching to 编辑 (if enabled) and back to 预览
  try {
    await page.getByRole('button', { name: '编辑' }).first().click({ timeout: 1500 });
    await page.waitForTimeout(500);
    const hasTextarea = await page.locator('textarea').count();
    console.log('after click 编辑: textarea count =', hasTextarea);
    await page.getByRole('button', { name: '预览' }).first().click({ timeout: 1500 });
    await page.waitForTimeout(400);
    console.log('switched back to 预览 OK');
  } catch (e) { console.log('toggle interaction note:', String(e).slice(0, 100)); }

  await browser.close();
};
run().then(() => { console.log('\nCONSOLE ERRORS:', errs.length); errs.slice(0, 15).forEach(e => console.log('  ', e.slice(0, 160))); })
  .catch(e => { console.error('FATAL', String(e).slice(0, 300)); process.exit(1); });
