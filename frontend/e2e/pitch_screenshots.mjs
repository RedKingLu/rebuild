// V26.3 路演截图：01_home（项目列表）+ 02_workspace（项目工作区）
// 浅色主题（前端默认 light，此处显式设置以防 localStorage 残留）；视口 1512x945（16:10，宽度≥1440）
import { chromium } from 'playwright';
import { mkdirSync } from 'fs';

const BASE = 'http://127.0.0.1:5173';
const PROJECT_ID = '7c8e309a-4e0f-4b85-b85f-87d8f41c121a'; // MicroOA-V26.3-modernization，真实全流程运行样本（非客户数据）
const OUT = '/tmp/rebuild_screenshots';
mkdirSync(OUT, { recursive: true });

const run = async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1512, height: 945 } });
  const page = await ctx.newPage();

  // 1) 项目列表页
  await page.goto(`${BASE}/projects`, { waitUntil: 'networkidle' });
  await page.evaluate(() => { document.documentElement.setAttribute('data-theme', 'light'); localStorage.setItem('theme','light'); });
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/01_home.png`, fullPage: false });
  console.log('01_home.png done');

  // 2) 项目工作区首页
  await page.goto(`${BASE}/projects/${PROJECT_ID}/workspace`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  // 关闭可能出现的浮层/提示（若存在关闭按钮则点击，不存在则忽略）
  for (const sel of ['.modal-close', '[aria-label="Close"]', '.toast button']) {
    const el = await page.$(sel).catch(() => null);
    if (el) await el.click().catch(() => {});
  }
  await page.screenshot({ path: `${OUT}/02_workspace.png`, fullPage: false });
  console.log('02_workspace.png done');

  await browser.close();
};

run().catch(e => { console.error('FAILED', e); process.exit(1); });
