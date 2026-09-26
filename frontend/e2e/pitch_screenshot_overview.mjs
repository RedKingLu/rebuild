// V26.3 路演截图：00_overview（概览页）
// 浅色主题；视口 1512x945（16:10，宽度≥1440）
import { chromium } from 'playwright';
import { mkdirSync } from 'fs';

const BASE = 'http://127.0.0.1:5173';
const OUT = '/tmp/rebuild_screenshots';
mkdirSync(OUT, { recursive: true });

const run = async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1512, height: 945 } });
  const page = await ctx.newPage();

  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
  await page.evaluate(() => { document.documentElement.setAttribute('data-theme', 'light'); localStorage.setItem('theme', 'light'); });
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  for (const sel of ['.modal-close', '[aria-label="Close"]', '.toast button']) {
    const el = await page.$(sel).catch(() => null);
    if (el) await el.click().catch(() => {});
  }
  await page.screenshot({ path: `${OUT}/00_overview.png`, fullPage: false });
  console.log('00_overview.png done');
  await browser.close();
};

run().catch(e => { console.error('FAILED', e); process.exit(1); });
