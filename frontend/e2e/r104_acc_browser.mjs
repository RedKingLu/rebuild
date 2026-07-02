// R10-4 独立验收 — 浏览器端到端证据 (Node Playwright, chromium headless)
// 独立编写(未采信施工 e2e 脚本)。真实前端:5173 → 真实后端:8000。
import { chromium } from 'playwright';
import fs from 'fs';

const GIT_PID = '5b86272c-aff9-458d-9964-3534ff1e66e9';
const BASE = 'http://localhost:5173';
const SHOT = '/tmp/r104_shots';
fs.mkdirSync(SHOT, { recursive: true });

const consoleErrors = [];
const apiCalls = [];

const run = async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('requestfinished', async req => {
    const u = req.url();
    if (u.includes('/api/')) {
      let st = '?'; try { const r = await req.response(); st = r ? r.status() : '?'; } catch {}
      apiCalls.push(`${req.method()} ${u.split('localhost:5173')[1]?.split('?')[0]} -> ${st}`);
    }
  });
  const shot = async t => { await page.screenshot({ path: `${SHOT}/${t}.png`, fullPage: true }); console.log(`[shot] ${t}`); };

  console.log('=== 1. 首页 ===');
  await page.goto(BASE, { waitUntil: 'networkidle' }); await page.waitForTimeout(1500);
  await shot('01_home'); console.log('title:', await page.title());

  console.log('=== 2. 项目列表 ===');
  await page.goto(`${BASE}/projects`, { waitUntil: 'networkidle' }); await page.waitForTimeout(1500);
  await shot('02_projects');
  const listBody = await page.innerText('body');
  console.log("列表含'信创迁移验收-ERP':", listBody.includes('信创迁移验收-ERP'));

  console.log('=== 3. 浏览器原生新建项目 ===');
  await page.goto(`${BASE}/projects/new`, { waitUntil: 'networkidle' }); await page.waitForTimeout(1200);
  await shot('03_create');
  const nInputs = await page.locator('input, textarea, select').count();
  console.log('create 页控件数:', nInputs);
  try {
    await page.locator("input[type='text'], input:not([type])").first().fill('浏览器新建验收项目-P2P3');
    await page.waitForTimeout(300); await shot('03b_filled');
    const btns = await page.locator('button').allInnerTexts();
    console.log('create 按钮:', JSON.stringify(btns));
  } catch (e) { console.log('create fill err:', String(e).slice(0,120)); }

  console.log('=== 4. git 项目工作区 ===');
  await page.goto(`${BASE}/projects/${GIT_PID}/workspace`, { waitUntil: 'networkidle' }); await page.waitForTimeout(2800);
  await shot('04_workspace');
  const wbody = await page.innerText('body');
  console.log('工作区文本(前700):', wbody.slice(0,700).replace(/\n/g,' | '));
  const labels = [...new Set((await page.locator('button, [role=tab], a').allInnerTexts()).flat().map(s=>s.trim()).filter(Boolean))];
  console.log('工作区标签/按钮(前40):', JSON.stringify(labels.slice(0,40)));

  for (const label of ['P2','P3','P1','评估','规划','Gate']) {
    try {
      const loc = page.getByText(label, { exact:false }).first();
      if (await loc.count() > 0) {
        await loc.click({ timeout: 3000 }); await page.waitForTimeout(1800);
        await shot(`05_stage_${label}`);
        const seg = (await page.innerText('body')).slice(0,450).replace(/\n/g,' | ');
        console.log(`[${label}] 点击后:`, seg);
      }
    } catch (e) { console.log(`[${label}] 点击失败:`, String(e).slice(0,100)); }
  }
  await browser.close();
};

run().then(() => {
  console.log('\n===== CONSOLE ERRORS =====');
  console.log('total:', consoleErrors.length);
  consoleErrors.slice(0,30).forEach(e => console.log('  ', e.slice(0,200)));
  console.log('\n===== /api 请求(去重, 前50) =====');
  [...new Set(apiCalls)].slice(0,50).forEach(c => console.log('  ', c));
}).catch(e => { console.error('FATAL', e); process.exit(1); });
