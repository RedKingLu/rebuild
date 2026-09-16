// R18-1 G-3（D-074）浏览器级联调留证：L5 高风险命令 Gate 渲染 + 决策路由核验
//
// 验证目标（V26.2-验收标准 §1 G-3 / §2.1 R18-1-04）：
//  1. 页面在运行期真实发起 API 并渲染真实响应（非 mock）
//  2. l5_high_risk_command 渲染为「高风险命令审批 Gate（L5）」且展示被拦命令原文
//  3. 决策请求打到通用 Gate 端点 /gates/{id}/decision，
//     **不是**阶段晋级端点 promotion-decision（否则用户"批准"会误推进阶段）
import { chromium } from 'playwright';

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
const CHROMIUM_EXEC = (process.env.PLAYWRIGHT_CHROMIUM_EXEC || '').trim();
const LAUNCH_OPTS = { headless: true, ...(CHROMIUM_EXEC ? { executablePath: CHROMIUM_EXEC } : {}) };

const BASE = 'http://localhost:5173';
const PID = process.argv[2];
const GATE_ID = process.argv[3];
const errs = [];
const apiCalls = [];
const decisionCalls = [];

const run = async () => {
  const browser = await chromium.launch(LAUNCH_OPTS);
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  page.on('requestfinished', async r => {
    const u = r.url();
    if (!u.includes('/api/')) return;
    let s = '?'; try { const rs = await r.response(); s = rs ? rs.status() : '?'; } catch {}
    const path = u.split('localhost:5173')[1]?.split('?')[0];
    apiCalls.push(`${r.method()} ${path} -> ${s}`);
    if (u.includes('decision')) decisionCalls.push(`${r.method()} ${path} -> ${s}`);
  });

  console.log('=== 1. 导航（运行期真实请求）===');
  await page.goto(`${BASE}/projects/${PID}/workspace`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);
  console.log(`  真实 API 请求数: ${apiCalls.length}`);
  apiCalls.filter(c => c.includes('/gates')).slice(0, 5).forEach(c => console.log('   ', c));

  console.log('\n=== 2. 跳过新项目引导向导（其浮层会拦截点击）===');
  for (const cand of ['跳过引导', '跳过']) {
    const loc = page.locator(`button:has-text("${cand}")`);
    if (await loc.count() > 0) { await loc.first().click(); console.log(`  已点击: ${cand}`); break; }
  }
  await page.waitForTimeout(1500);

  console.log('\n=== 3. 打开右侧 Gate 页签 ===');
  const opened = await page.evaluate(() => {
    const els = [...document.querySelectorAll('button, [role="tab"], .tab, li, span, div')];
    const t = els.find(e => (e.textContent || '').trim() === 'Gate' && e.children.length === 0);
    if (t) { t.click(); return true; }
    return false;
  });
  console.log(`  Gate 页签点击: ${opened ? '成功' : '未找到（回退：直接检查页面）'}`);
  await page.waitForTimeout(2500);

  console.log('\n=== 3. L5 Gate 渲染核验 ===');
  const html = await page.content();
  const checks = {
    'L5 中文标签「高风险命令审批 Gate（L5）」': html.includes('高风险命令审批 Gate（L5）'),
    '被拦命令原文 chown -R root /': html.includes('chown -R root /'),
    '槽位说明 build_verified': html.includes('build_verified'),
    'gate_id 出现在页面': html.includes(GATE_ID),
  };
  for (const [k, v] of Object.entries(checks)) console.log(`  ${v ? '✅' : '❌'} ${k}`);
  await page.screenshot({ path: '/tmp/r18_g3_before.png', fullPage: true });
  console.log('  截图: /tmp/r18_g3_before.png');

  console.log('\n=== 4. 展开 Gate 详情（决策按钮在 Modal 内）===');
  const expanded = await page.evaluate(() => {
    const el = [...document.querySelectorAll('div')]
      .find(d => (d.textContent || '').includes('高风险命令审批 Gate（L5）') && d.style.cursor === 'pointer');
    if (el) { el.click(); return true; }
    return false;
  });
  console.log(`  展开触发: ${expanded ? '成功' : '未命中 cursor:pointer 头部'}`);
  await page.waitForTimeout(1500);

  console.log('\n=== 5. 定位并点击决策按钮（reject 需先填原因）===');
  const labels = await page.evaluate(() =>
    [...document.querySelectorAll('button')].map(b => b.textContent?.trim()).filter(Boolean).slice(0, 30));
  console.log('  可见按钮:', JSON.stringify(labels));

  // reject 必填原因，否则前端只报错不发请求
  const ta = page.locator('textarea');
  if (await ta.count() > 0) {
    await ta.first().fill('R18-1 G-3 D-074 联调验证：确认 L5 Gate 可裁决且决策打到通用 Gate 端点。此为验证性拒绝。');
    console.log('  已填写拒绝原因');
  } else {
    console.log('  ⚠️ 未找到原因输入框');
  }

  let clicked = null;
  for (const cand of ['拒绝', '驳回', 'reject']) {
    const loc = page.locator(`button:has-text("${cand}")`);
    if (await loc.count() > 0) { await loc.first().click(); clicked = cand; break; }
  }
  if (!clicked) {
    const txt = await page.locator('body').innerText();
    console.log('  ❌ 未找到决策按钮；页面可见文本片段：');
    console.log(txt.slice(0, 1200));
  } else {
    console.log(`  已点击: ${clicked}`);
    await page.waitForTimeout(3500);
    console.log('\n=== 6. 决策路由核验（关键）===');
    decisionCalls.forEach(c => console.log('   ', c));
    const generic = decisionCalls.some(c => c.includes(`/gates/${GATE_ID}/decision`));
    const promotion = decisionCalls.some(c => c.includes('promotion-decision'));
    console.log(`  ${generic ? '✅' : '❌'} 打到通用 Gate 端点 /gates/${GATE_ID}/decision`);
    console.log(`  ${promotion ? '❌ 严重：误打阶段晋级端点' : '✅ 未打到 promotion-decision'}`);
    await page.screenshot({ path: '/tmp/r18_g3_after.png', fullPage: true });
    console.log('  截图: /tmp/r18_g3_after.png');
  }

  console.log('\n=== 5. 浏览器 console 错误 / 5xx ===');
  console.log(`  console error 数: ${errs.length}`);
  errs.slice(0, 5).forEach(e => console.log('   ', e.slice(0, 160)));
  const bad = apiCalls.filter(c => / -> 5\d\d$/.test(c));
  console.log(`  5xx 响应数: ${bad.length}`);
  bad.slice(0, 5).forEach(c => console.log('   ', c));

  await browser.close();
};
run().catch(e => { console.error('脚本异常:', e.message); process.exit(1); });
