// R17-1B 独立验收巡检脚本（本轮自写，非复用 R17-1A）
import { chromium } from 'playwright';

const BASE = 'http://localhost:5173';
const PID = 'ba061cfd-3dff-48c6-a917-ffa1e3b68946';
const routes = [
  ['/', 'home'],
  ['/projects', 'projects'],
  [`/projects/${PID}`, 'project-detail'],
  ['/models', 'models'],
  ['/resources', 'resources'],
  ['/integrations', 'integrations'],
  ['/settings', 'settings'],
  ['/fusion', 'fusion'],
  ['/community', 'community'],
  ['/docs', 'docs'],
];

const browser = await chromium.launch();
const results = [];
for (const [path, name] of routes) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const consoleErrors = [];
  const netFailed = [];
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 160)); });
  page.on('requestfailed', r => netFailed.push(`${r.method()} ${r.url().slice(0, 90)} :: ${r.failure()?.errorText || ''}`));
  let status = 0, mock = false, future = false;
  try {
    const resp = await page.goto(BASE + path, { waitUntil: 'domcontentloaded', timeout: 15000 });
    status = resp ? resp.status() : 0;
    await page.waitForTimeout(1500);
    const body = (await page.content()).toLowerCase();
    mock = /全局\s*mock|mock\s*横幅|体验壳|mock banner/.test(body);
    future = /规划中|占位|暂未开放|敬请期待|future/.test(body);
  } catch (e) {
    status = `ERR:${(e.message || '').slice(0, 60)}`;
  }
  results.push({ name, path, status, consoleErrors, netFailed, future, mock });
  await ctx.close();
}
await browser.close();
for (const r of results) {
  console.log(`[${r.status}] ${r.name.padEnd(16)} console_err=${r.consoleErrors.length} net_failed=${r.netFailed.length} future=${r.future} globalMock=${r.mock}`);
  r.consoleErrors.slice(0, 3).forEach(e => console.log(`      CONSOLE: ${e}`));
  r.netFailed.slice(0, 3).forEach(e => console.log(`      NETFAIL: ${e}`));
}
const totalErr = results.reduce((a, r) => a + r.consoleErrors.length, 0);
console.log(`\nSUMMARY: routes=${results.length} total_console_errors=${totalErr}`);
