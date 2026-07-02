import { chromium } from 'playwright';
import fs from 'fs';
const BASE = 'http://localhost:5173';
const SHOT = '/tmp/r104_shots';
const PID = process.argv[2] || '5b86272c-aff9-458d-9964-3534ff1e66e9';
const consoleErrors = []; const apiCalls = [];
const run = async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('console', m => { if (m.type()==='error') consoleErrors.push(m.text()); });
  page.on('requestfinished', async r => { const u=r.url(); if(u.includes('/api/')){ let s='?';try{const rs=await r.response();s=rs?rs.status():'?';}catch{} apiCalls.push(`${r.method()} ${u.split('localhost:5173')[1]?.split('?')[0]} -> ${s}`);} });
  const shot = async t => { await page.screenshot({path:`${SHOT}/${t}.png`,fullPage:true}); console.log('[shot]',t); };
  console.log('=== 工作区', PID, '===');
  await page.goto(`${BASE}/projects/${PID}/workspace`, { waitUntil:'domcontentloaded' });
  await page.waitForTimeout(4000);
  await shot('04_workspace');
  const wbody = await page.innerText('body');
  console.log('文本(前900):', wbody.slice(0,900).replace(/\n/g,' | '));
  const labels = [...new Set(await page.locator('button, [role=tab], a').allInnerTexts())].map(s=>s.trim()).filter(Boolean);
  console.log('标签(前50):', JSON.stringify(labels.slice(0,50)));
  for (const label of ['P2','P3','评估','规划','P1','P0','Gate','阶段']) {
    try { const loc = page.getByText(label,{exact:false}).first();
      if (await loc.count()>0){ await loc.click({timeout:2500}); await page.waitForTimeout(1600);
        await shot(`05_${label}`); console.log(`[${label}]`, (await page.innerText('body')).slice(0,500).replace(/\n/g,' | ')); }
    } catch(e){ console.log(`[${label}] fail`, String(e).slice(0,80)); }
  }
  await browser.close();
};
run().then(()=>{ console.log('\nCONSOLE ERRORS:', consoleErrors.length); consoleErrors.slice(0,25).forEach(e=>console.log('  ',e.slice(0,200)));
  console.log('\n/api 请求(去重):'); [...new Set(apiCalls)].slice(0,60).forEach(c=>console.log('  ',c)); }).catch(e=>{console.error('FATAL',String(e).slice(0,300));process.exit(1);});
