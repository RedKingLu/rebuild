// R17-5: OverviewPage "上次退出项目" card must show real project after entering workspace,
// and must NOT show a "Mock" badge.
import { chromium } from 'playwright';
const BASE = 'http://localhost:5173';

const browser = await chromium.launch();
const ctx = await browser.newContext();
const page = await ctx.newPage();
const errors = [];
ctx.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
ctx.on('requestfailed', r => errors.push('FAILED ' + r.url()));

try {
  // 1) Go to projects page
  await page.goto(BASE + '/projects', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(800);

  // 2) Find the first project "进入工作区" link/button and click it
  const projectLinks = page.locator('a[href*="/projects/"], button').filter({ hasText: /进入工作区|打开工作区|查看/ });
  const count = await projectLinks.count();
  if (count === 0) {
    throw Error('No project entry link found on /projects');
  }
  // Click first project row's "进入工作区" action
  // Try to click a link that navigates to workspace (href contains /workspace)
  const wsLink = page.locator('a[href*="/workspace"]').first();
  const wsCount = await wsLink.count();
  if (wsCount > 0) {
    await wsLink.click();
  } else {
    // fallback: click first project row then find workspace button
    await projectLinks.first().click();
  }
  await page.waitForTimeout(1500);

  // 3) Go back to dashboard / overview (route is '/')
  await page.goto(BASE + '/', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(800);

  // 4) Assert: "上次退出项目" card contains project name text and NOT "Mock"
  const overviewCard = page.locator('.card').filter({ hasText: '上次退出的项目' });
  const cardText = await overviewCard.innerText();

  const containsProjectName = !cardText.includes('暂无');
  const containsMock = /Mock/i.test(cardText);

  console.log('--- "上次退出项目" card content ---');
  console.log(cardText.split('\n').filter(Boolean).slice(0, 6).map(l => '  ' + l).join('\n'));
  console.log('--- assertions ---');
  console.log('contains project name (not empty state):', containsProjectName);
  console.log('contains "Mock" badge:', containsMock);

  if (!containsProjectName) throw Error('FAIL: card still shows empty state, lastProjectId not read');
  if (containsMock) throw Error('FAIL: card still contains "Mock" badge');

  console.log('\nRESULT: PASS');
} catch (e) {
  console.log('\nRESULT: FAIL -', e.message);
  process.exitCode = 1;
} finally {
  await browser.close();
  console.log('console errors:', errors.length);
  errors.slice(0, 10).forEach(e => console.log('  ', e.slice(0, 150)));
}
