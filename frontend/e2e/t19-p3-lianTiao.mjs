import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const PID = readFileSync('/tmp/t19_pid.txt', 'utf-8').trim();
const FRONTEND = 'http://localhost:5173';
const URL = `${FRONTEND}/projects/${PID}/workspace`;
const errs = [];

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1400, height: 1050 } });
page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });

await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
await page.waitForTimeout(2000);
const skip = page.locator('text=跳过引导');
if (await skip.count()) { await skip.first().click(); await page.waitForTimeout(500); }

await page.locator('text=P3 规划').first().click();
await page.waitForTimeout(1500);
await page.screenshot({ path: '/tmp/t19_p3_page.png', fullPage: true });

const checks = {
  '页头 P3 规划': 'text=P3 规划 — 方案',
  '待审计划草案标记': 'text=待审计划草案',
  'Stage Plan 卡片': 'text=Stage Plan（阶段计划）',
  '范围/不做': 'text=明确不做',
  'P5 验证策略': 'text=P5 验证策略',
  'Task Plan 批次': 'text=Task Plan（任务计划',
  '高风险需 Gate': 'text=需 P3→P4 用户 Gate 批准',
  'TaskGraph DAG': 'text=TaskGraph（DAG 简化视图）',
  '边策略清单': 'text=边策略（依赖关系）',
  '并行边': 'text=并行',
  '规划模型 id': 'text=glm-5.2',
  '任务节点标题': 'text=替换闭源依赖为信创版本',
};
let allPass = true;
for (const [name, sel] of Object.entries(checks)) {
  const ok = (await page.locator(sel).count()) > 0;
  if (!ok) allPass = false;
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}`);
}
const apiData = await page.evaluate(async (pid) => {
  const r = await fetch(`/api/projects/${pid}/planning-summary`);
  return (await r.json()).data;
}, PID);
const apiReal = apiData?.available === true && apiData?.task_graph?.node_count === 3 && apiData?.task_graph?.edge_count === 3;
console.log(`  [${apiReal ? 'PASS' : 'FAIL'}] 真实端点响应 available=true, nodes=3, edges=3`);

console.log('CONSOLE ERRORS:', errs.length);
errs.slice(0, 8).forEach(e => console.log('  ', e.slice(0, 160)));
await browser.close();
if (!allPass || !apiReal || errs.length > 0) { console.log('\nRESULT: FAIL'); process.exit(1); }
console.log('\nRESULT: PASS');
