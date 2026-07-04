import { chromium } from 'playwright';
const BASE = 'http://localhost:5173';
const PID = process.argv[2] || '40e35aa2-61cd-4986-ad43-87cf09f5df9f';
const errs = [];
const apiCalls = [];
const run = async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  page.on('requestfinished', async r => {
    const u = r.url();
    if (u.includes('/api/')) {
      let s = '?'; try { const rs = await r.response(); s = rs ? rs.status() : '?'; } catch {}
      apiCalls.push(`${r.method()} ${u.split('localhost:5173')[1]?.split('?')[0]} -> ${s}`);
    }
  });

  await page.goto(`${BASE}/projects/${PID}/workspace`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3500);

  // Open P4 stage via FlowRail
  const clicked = await page.evaluate(() => {
    const nodes = [...document.querySelectorAll('.rail .stage')];
    const p4 = nodes.find(n => n.textContent?.toUpperCase().includes('P4'));
    if (p4) { p4.click(); return 'rail-p4'; }
    return null;
  });
  await page.waitForTimeout(1800);

  const body = await page.innerText('body');
  const wired = await page.locator('text=P4 执行').count();
  const apis = [...new Set(apiCalls)];
  const taskgraphCalled = apis.some(a => a.includes('stages/p4/taskgraph'));
  const fileCalled = apis.some(a => a.includes('/file'));
  const mockBanner = /mock|Mock|模拟数据/.test(body);
  const realErrs = errs.filter(e => !/favicon|ERR_ABORTED/.test(e));

  console.log('navigated:', clicked);
  console.log('1. StagePageP4 renders header (P4 执行):', wired > 0);
  console.log('2. real taskgraph API called (no mock):', taskgraphCalled, '| file API:', fileCalled);
  console.log('3. api calls:', JSON.stringify(apis));
  console.log('4. no mock banner:', !mockBanner);
  console.log('5. blocking console errors:', realErrs.length);
  realErrs.slice(0, 3).forEach(e => console.log('    ', e.slice(0, 120)));

  await browser.close();
};
run().then(() => { console.log('\nTOTAL console errors:', errs.length); })
  .catch(e => { console.error('FATAL', String(e).slice(0, 300)); process.exit(1); });
