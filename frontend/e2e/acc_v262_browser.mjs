// V26.2 独立验收：浏览器级验证（历史上 G-3 只有接口级证据，本脚本补浏览器级）
//
// 覆盖任务书 B.1~B.6：
//   1 项目列表/概览能看到新建项目，且状态字段与后端一致
//   2 Workspace 各阶段页面渲染后端真实数据（非 mock / 非空态）
//   3 Gate 交互：真实 Gate 文案 / 风险等级 / action_approval 待执行入参真实渲染
//   4 Evidence / Trace 面板展示真实产出
//   5 每步截图
//   6 每页 console error 计数（非零如实报出具体内容）
import { chromium } from 'playwright';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';

// ── B-V262-E2E-SCRIPT-HYGIENE（P2）────────────────────────────────────────────
// 本脚本此前偏离 e2e/ 下 24 个主流脚本的写法，同时踩中两个已登记缺陷模式：
//   缺陷 1 · 未声明依赖：`import { chromium } from 'playwright-core'`，而 package.json
//     声明的是 `playwright`（playwright-core 只是它的传递依赖）⇒ 这正是
//     B-V262-UNDECLARED-DEP 点名的"用了却未在清单声明"，只不过那条记在平台**替用户**做的
//     依赖校验上，本条是平台自己犯同一个错。改用已声明的 `playwright`（解除条件 ①③）。
//   缺陷 2 · 硬编码开发者主目录：`executablePath` 写死
//     `<开发者主目录>/.cache/ms-playwright/chromium-XXXX/chrome-linux64/chrome`，
//     与 Q-R22-9 同族，且本文件会随转公开一起公开。
// 改法（解除条件 ①）：默认**不指定** executablePath，交给 playwright 自己解析它安装的
// 浏览器（主流 24 个脚本的写法）；确需指定时读环境变量 `PLAYWRIGHT_CHROMIUM_EXEC`，
// **不设任何缺省值** —— 缺失就用 playwright 自带的，绝不静默回落到别人机器上的路径。
// 环境变量显式设成空串视为"没设"，同样不静默用旧路径。
const __dirname = dirname(fileURLToPath(import.meta.url));
// 仓库根：本文件在 <repo>/frontend/e2e/ 下 ⇒ 上溯两级（解除条件 ②，复用主流脚本的
// fileURLToPath + resolve 写法，不另造路径推导）。
const REPO_ROOT = resolve(__dirname, '..', '..');
const CHROMIUM_EXEC = (process.env.PLAYWRIGHT_CHROMIUM_EXEC || '').trim();
const LAUNCH_OPTS = { headless: true, ...(CHROMIUM_EXEC ? { executablePath: CHROMIUM_EXEC } : {}) };
const BASE = 'http://127.0.0.1:5173';
const PID = process.argv[2];
const LABEL = process.argv[3] || 'run';
// 输出目录相对仓库根解析（解除条件 ②）；可经 ACC_BROWSER_OUT_DIR 覆盖到别处，无缺省绝对路径。
const OUT = process.env.ACC_BROWSER_OUT_DIR
  ? resolve(process.env.ACC_BROWSER_OUT_DIR, LABEL)
  : resolve(REPO_ROOT, '产物/草稿/V26.2-独立验收-浏览器证据', LABEL);

import { mkdirSync } from 'fs';
mkdirSync(OUT, { recursive: true });

const pageErrs = {};   // page -> [error text]
const apiCalls = [];
let cur = 'init';

const shot = async (page, name) => {
  const p = `${OUT}/${name}.png`;
  await page.screenshot({ path: p, fullPage: true });
  console.log(`   [截图] ${p}`);
  return p;
};

const errCount = (k) => (pageErrs[k] || []).length;

const run = async () => {
  const browser = await chromium.launch(LAUNCH_OPTS);
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });

  page.on('console', m => {
    if (m.type() === 'error') {
      (pageErrs[cur] = pageErrs[cur] || []).push(m.text().slice(0, 300));
    }
  });
  page.on('pageerror', e => {
    (pageErrs[cur] = pageErrs[cur] || []).push('PAGEERROR: ' + String(e.message).slice(0, 300));
  });
  page.on('requestfinished', async r => {
    const u = r.url();
    if (!u.includes('/api/')) return;
    let s = '?'; try { const rs = await r.response(); s = rs ? rs.status() : '?'; } catch {}
    apiCalls.push(`${r.method()} ${u.replace(BASE, '').split('?')[0]} -> ${s}`);
  });

  // ---------- 1. 项目列表 ----------
  cur = 'projects_list';
  console.log('\n=== 1. 项目列表页 ===');
  await page.goto(`${BASE}/projects`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);
  const listHtml = await page.content();
  const listBody = await page.evaluate(() => document.body.innerText);
  console.log(`  项目名出现在页面: ${listBody.includes('V26.2-独立验收-MicroOA完整真跑')}`);
  console.log(`  project_id 片段出现: ${listHtml.includes(PID.slice(0, 8))}`);
  // 抓出该项目所在行的可见文本
  const row = await page.evaluate((pid) => {
    const els = [...document.querySelectorAll('tr, li, div, a')];
    const t = els.filter(e => (e.innerText || '').includes('V26.2-独立验收-MicroOA完整真跑'));
    if (!t.length) return null;
    const smallest = t[t.length - 1];
    return (smallest.innerText || '').replace(/\n+/g, ' | ').slice(0, 400);
  }, PID);
  console.log(`  该项目行可见文本: ${row}`);
  console.log(`  console errors: ${errCount(cur)}`);
  await shot(page, '01-项目列表');

  // ---------- 2. 项目详情 ----------
  cur = 'project_detail';
  console.log('\n=== 2. 项目详情页 ===');
  await page.goto(`${BASE}/projects/${PID}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);
  const detBody = await page.evaluate(() => document.body.innerText);
  for (const k of ['modernization', '软件现代化', 'MicroOA', 'deepseek', 'p0', 'p1', '1018']) {
    if (detBody.includes(k)) console.log(`  详情页含真实字段 "${k}": true`);
  }
  console.log(`  console errors: ${errCount(cur)}`);
  await shot(page, '02-项目详情');

  // ---------- 3. Workspace ----------
  cur = 'workspace';
  console.log('\n=== 3. Workspace ===');
  await page.goto(`${BASE}/projects/${PID}/workspace`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(5000);
  for (const cand of ['跳过引导', '跳过']) {
    const loc = page.locator(`button:has-text("${cand}")`);
    if (await loc.count() > 0) { await loc.first().click(); console.log(`  已跳过引导浮层: ${cand}`); break; }
  }
  await page.waitForTimeout(2500);
  console.log(`  console errors: ${errCount(cur)}`);
  await shot(page, '03-workspace-默认');

  // ---------- 4. 各阶段页 ----------
  for (const st of ['P0', 'P1', 'P2', 'P3', 'P4', 'P5']) {
    cur = `stage_${st}`;
    const clicked = await page.evaluate((s) => {
      const els = [...document.querySelectorAll('button, [role="tab"], li, span, div, a')];
      const t = els.find(e => {
        const tx = (e.textContent || '').trim();
        return (tx === s || tx.startsWith(s + ' ') || tx === s.toLowerCase()) && e.children.length === 0;
      });
      if (t) { t.click(); return true; }
      return false;
    }, st);
    await page.waitForTimeout(3000);
    const body = await page.evaluate(() => document.body.innerText);
    const mockish = /mock|占位|静态演示|static_demo|not_connected/i.test(body);
    console.log(`\n  --- ${st} 页签(点中=${clicked}) ---`);
    console.log(`  可见正文长度: ${body.length}  含 mock/占位标记: ${mockish}`);
    console.log(`  正文摘录: ${body.replace(/\n+/g, ' | ').slice(0, 500)}`);
    console.log(`  console errors: ${errCount(cur)}`);
    await shot(page, `04-stage-${st}`);
  }

  // ---------- 5. Gate 面板 ----------
  cur = 'gate_panel';
  console.log('\n=== 5. Gate 面板（真实 Gate 文案 / 风险等级 / 待执行入参）===');
  await page.evaluate(() => {
    const els = [...document.querySelectorAll('button, [role="tab"], li, span, div')];
    const t = els.find(e => (e.textContent || '').trim() === 'Gate' && e.children.length === 0);
    if (t) t.click();
  });
  await page.waitForTimeout(3000);
  const gateBody = await page.evaluate(() => document.body.innerText);
  const gchecks = {
    'L4 风险等级可见': /L4/.test(gateBody),
    'action_approval 语义可见': /高风险工具|审批|action_approval/.test(gateBody),
    '待执行入参标签可见': /待执行入参/.test(gateBody),
    '入参原文 run_safe_command 可见': /run_safe_command/.test(gateBody),
    '入参原文 find source 可见': /find source/.test(gateBody),
    '脱敏标记（已脱敏）可见': /已脱敏/.test(gateBody),
  };
  for (const [k, v] of Object.entries(gchecks)) console.log(`  ${v ? 'OK ' : '-- '}${k}: ${v}`);
  console.log(`  Gate 面板正文摘录: ${gateBody.replace(/\n+/g, ' | ').slice(0, 900)}`);
  console.log(`  console errors: ${errCount(cur)}`);
  await shot(page, '05-Gate面板');

  // ---------- 6. Evidence / Trace ----------
  for (const tab of ['Evidence', 'Trace', '证据', '审计', 'Audit']) {
    cur = `panel_${tab}`;
    const ok = await page.evaluate((s) => {
      const els = [...document.querySelectorAll('button, [role="tab"], li, span, div')];
      const t = els.find(e => (e.textContent || '').trim() === s && e.children.length === 0);
      if (t) { t.click(); return true; }
      return false;
    }, tab);
    if (!ok) { console.log(`\n  --- ${tab} 页签未找到 ---`); continue; }
    await page.waitForTimeout(2500);
    const b = await page.evaluate(() => document.body.innerText);
    console.log(`\n  --- ${tab} 面板 ---`);
    console.log(`  正文摘录: ${b.replace(/\n+/g, ' | ').slice(0, 600)}`);
    console.log(`  console errors: ${errCount(cur)}`);
    await shot(page, `06-panel-${tab}`);
  }

  // ---------- 汇总 ----------
  console.log('\n=== API 调用（真实运行期请求，去重）===');
  [...new Set(apiCalls)].slice(0, 40).forEach(c => console.log('  ' + c));
  console.log(`\n=== console error 汇总（按页）===`);
  let tot = 0;
  for (const [k, v] of Object.entries(pageErrs)) {
    tot += v.length;
    console.log(`  ${k}: ${v.length}`);
    [...new Set(v)].slice(0, 6).forEach(e => console.log(`      - ${e}`));
  }
  console.log(`  合计 console error: ${tot}`);
  await browser.close();
};

run().catch(e => { console.error('SCRIPT FAILED:', e); process.exit(1); });
