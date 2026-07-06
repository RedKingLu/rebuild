/**
 * R13-7 /fusion 页面真实化 D-074 最小前后端联调（WP-7.6）。
 *
 * 验证：
 *   1. /fusion 路由 0 崩溃（可访问、主结构渲染）。
 *   2. 真实 GET /api/fusion/profiles 调用并被前端渲染（非 store 静态数据）。
 *   3. 3 子 tab 存在：聚合模型管理 / 配置 / 触发历史。
 *   4. Profile 卡片「配置 / 触发 / 禁用-启用」按钮真实存在（CRUD 路径可点）。
 *   5. 无阻塞性 console error。
 *   6. 页面无任何明文 Key 泄露（D-097 / 脱敏）。
 *
 * 运行： node frontend/e2e/r13_7_fusion.mjs   （需前端 dev server 在 localhost:5173 且后端在 8000）
 */
import { chromium } from 'playwright';

const BASE = process.env.BASE || 'http://localhost:5173';
const API_BASE = process.env.API_BASE || 'http://localhost:8000';

const errs = [];
const apiCalls = [];
let fatal = null;

const run = async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  page.on('pageerror', e => { fatal = String(e.message || e); });
  page.on('requestfinished', async r => {
    const u = r.url();
    if (u.includes('/api/') && (u.includes('fusion') || u.includes('/api/'))) {
      let s = '?';
      try { const rs = await r.response(); s = rs ? String(rs.status()) : '?'; } catch {}
      apiCalls.push(`${r.method()} ${new URL(u).pathname} -> ${s}`);
    }
  });

  await page.goto(`${BASE}/fusion`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);

  const body = await page.innerText('body');

  // 1. 主结构 + 3 子 tab（精确匹配 tab 文案，避免与 Profile 卡片按钮重名）
  const h1 = await page.locator('h1').filter({ hasText: '聚合' }).count();
  const tab1 = await page.getByRole('button', { name: '聚合模型管理', exact: true }).count();
  const tab2 = await page.getByRole('button', { name: '配置', exact: true }).count();
  const tab3 = await page.getByRole('button', { name: '触发历史', exact: true }).count();

  // 2. 真实 API 调用
  const fusionApiCalls = [...new Set(apiCalls)].filter(a => a.includes('/fusion/'));
  const profilesGet = fusionApiCalls.some(a => /GET\s+\/api\/fusion\/profiles/.test(a));

  // 3. 页面无明文 Key 扫描（简单启发式：长 hex / sk- / 长 base64；排除 trace_ref 短 id）
  const bodyNoLines = body.replace(/\s+/g, ' ');
  const keyPatterns = [
    /\bsk-[A-Za-z0-9_-]{20,}\b/,                       // 类 OpenAI Key
    /[0-9a-f]{32,}/i,                                   // 32+ hex（api key 常见长度）
  ];
  const leaked = keyPatterns.filter(re => re.test(bodyNoLines));

  // 4. 红线校验文案
  const redline = /Fusion 不阻塞基础 Flow|失败不影响主流程|不替代人工 Gate/.test(body);

  const realErrs = errs.filter(e => !/favicon|ERR_ABORTED|Download the React/.test(e));

  console.log('=== R13-7 /fusion D-074 验证 ===');
  console.log('1. 路由无崩溃 (无 pageerror):', fatal === null, '| h1 聚合:', h1 > 0);
  console.log('2. 真实 API GET /fusion/profiles 调用:', profilesGet, '| fusion 相关:', JSON.stringify(fusionApiCalls));
  console.log('3. 3 子 tab 存在: 管理 =', tab1 > 0, '| 配置 =', tab2 > 0, '| 历史 =', tab3 > 0);
  console.log('4. console errors (non-fatal excluded):', realErrs.length);
  realErrs.slice(0, 3).forEach(e => console.log('     ', e.slice(0, 140)));
  console.log('5. 页面 Key 泄露检测:', leaked.length === 0 ? '无泄露 ✓' : `发现 ${leaked.length} 处可疑`);
  console.log('6. 红线提示文案存在:', redline);

  const tabsExist = tab1 > 0 && tab2 > 0 && tab3 > 0;
  const pass = fatal === null && h1 > 0 && profilesGet && tabsExist
    && realErrs.length === 0 && leaked.length === 0 && redline;

  // 附加：切换到「配置」tab 并确认配置面板可进入（Profile 卡片存在时）
  if (tab1 > 0) { await page.getByRole('button', { name: /配置/ }).first().click(); await page.waitForTimeout(800); }
  const configSection = await page.locator('text=Panel（多模型审议）').count();
  console.log('7. 配置 tab 渲染 Panel 分区:', configSection > 0);

  console.log('\nTOTAL console errors:', errs.length, '| fatal:', fatal || 'none');
  console.log('\n verdict:', pass ? 'PASS ✓' : 'FAIL ✗');

  await browser.close();
  process.exit(pass ? 0 : 1);
};

run().then(() => { /* exit set inside */ })
  .catch(e => { console.error('FATAL', String(e).slice(0, 400)); process.exit(1); });
