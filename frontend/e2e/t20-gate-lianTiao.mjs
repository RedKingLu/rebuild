import { chromium } from 'playwright';
import { readFileSync } from 'fs';

const PID = readFileSync('/tmp/t20_pid.txt', 'utf-8').trim();
const URL = `http://localhost:5173/projects/${PID}/workspace`;
const errs = [];
const posts = [];  // capture promotion-decision calls

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
page.on('request', r => { if (r.method() === 'POST' && r.url().includes('/promotion-decision')) posts.push(r.url()); });

await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
await page.waitForTimeout(2000);
// dismiss onboarding robustly (it can re-render on refetch) — retry until gone
for (let i = 0; i < 4; i++) {
  const s = page.locator('text=跳过引导');
  if (await s.count()) { await s.first().click().catch(() => {}); await page.waitForTimeout(600); }
  if ((await page.locator('text=项目引导').count()) === 0) break;
}
await page.waitForTimeout(400);

const check = async (name, sel) => { const ok = (await page.locator(sel).count()) > 0; console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}`); return ok; };
let all = true;

// 1. Gate banner (stage-agnostic, P2 gate) visible
all = await check('Gate 横幅 阶段晋级', 'text=阶段晋级 Gate') && all;
// open the gate review modal
await page.locator('text=点击查看审核材料').first().click({ timeout: 8000 });
await page.waitForTimeout(800);
await page.screenshot({ path: '/tmp/t20_gate_modal.png', fullPage: true });

// 2. Modal title mentions P2
all = await check('Modal 标题 P2 阶段晋级', 'text=P2 阶段晋级') && all;
// 3. P2 审核材料 listed (NOT p1 fallback)
all = await check('材料 评估报告', 'text=评估报告 (p2_assessment_report)') && all;
all = await check('材料 风险清单', 'text=风险清单 (p2_risk_list)') && all;
all = await check('材料 阻塞项', 'text=阻塞项清单 (p2_blocker_list)') && all;
all = await check('材料 验证缺口', 'text=验证缺口 (p2_validation_gaps)') && all;
all = await check('材料 资源需求', 'text=资源需求 (p2_resource_needs)') && all;
// 4. click a material → real content loads (risk L4 from seeded p2_risk_list)
await page.locator('text=风险清单 (p2_risk_list)').first().click();
await page.waitForTimeout(600);
all = await check('材料内容真实加载 (L4/glibc)', 'text=glibc') && all;
// 5. decision buttons present
all = await check('决策按钮 批准', 'button:has-text("批准")') && all;
all = await check('决策按钮 请求修改', 'button:has-text("请求修改")') && all;
all = await check('决策按钮 拒绝', 'button:has-text("拒绝")') && all;

// 6. click 批准 → promotion-decision POST fires
await page.locator('button:has-text("批准")').first().click();
await page.waitForTimeout(1200);
const decided = posts.some(u => u.includes('/stages/p2/promotion-decision'));
console.log(`  [${decided ? 'PASS' : 'FAIL'}] 批准触发 P2 promotion-decision POST`);
all = all && decided;

console.log('CONSOLE ERRORS:', errs.length);
errs.slice(0, 8).forEach(e => console.log('  ', e.slice(0, 160)));
await browser.close();
if (!all || errs.length > 0) { console.log('\nRESULT: FAIL'); process.exit(1); }
console.log('\nRESULT: PASS');
