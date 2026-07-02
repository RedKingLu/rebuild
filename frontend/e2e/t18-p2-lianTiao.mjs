import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const PID = readFileSync('/tmp/t18_pid.txt', 'utf-8').trim();
const FRONTEND = 'http://localhost:5173';
const URL = `${FRONTEND}/projects/${PID}/workspace`;
const errs = [];

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });

await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
await page.waitForTimeout(2000);

// 关闭项目引导浮窗（点“跳过引导”）
const skip = page.locator('text=跳过引导');
if (await skip.count()) { await skip.first().click(); await page.waitForTimeout(500); }

// 点击左导航 “P2 评估”
await page.locator('text=P2 评估').first().click();
await page.waitForTimeout(1500);
await page.screenshot({ path: '/tmp/t18_p2_page.png', fullPage: true });

// 断言 6 类产出标题 + analysis_only 标记渲染
const checks = {
  '页头 P2 评估': 'text=P2 评估 — 风险',
  'analysis_only 标记': 'text=辅助分析',
  '评估报告': 'text=评估报告',
  '风险清单': 'text=风险清单',
  '阻塞项清单': 'text=阻塞项清单',
  '不确定项清单': 'text=不确定项清单',
  '验证缺口清单': 'text=验证缺口清单',
  '资源需求建议': 'text=资源需求建议',
  'L5 风险分组': 'text=L5 · 最高',
  'L4 风险分组': 'text=L4 · 高风险',
  '风险来源/依据': 'text=glibc',
  '分析模型 id': 'text=glm-5.2',
};
let allPass = true;
for (const [name, sel] of Object.entries(checks)) {
  const ok = (await page.locator(sel).count()) > 0;
  if (!ok) allPass = false;
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}`);
}

// 断言页面确实调用了真实端点（网络层）——直接在浏览器上下文再请求一次校验字段
const apiData = await page.evaluate(async (pid) => {
  const r = await fetch(`/api/projects/${pid}/assessment-summary`);
  return (await r.json()).data;
}, PID);
const apiReal = apiData?.available === true && apiData?.risk_list?.length === 4;
console.log(`  [${apiReal ? 'PASS' : 'FAIL'}] 真实端点响应 available=true, risk_list=4`);

console.log('CONSOLE ERRORS:', errs.length);
errs.slice(0, 8).forEach(e => console.log('  ', e.slice(0, 160)));
await browser.close();

if (!allPass || !apiReal || errs.length > 0) { console.log('\nRESULT: FAIL'); process.exit(1); }
console.log('\nRESULT: PASS');
