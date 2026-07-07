import { chromium } from 'playwright';
const BASE='http://localhost:5173';
const PID='ba061cfd-3dff-48c6-a917-ffa1e3b68946';
const detailPath='/projects/'+PID;
const wsPath='/projects/'+PID+'/workspace';
const pages=[['/','home'],[detailPath,'detail'],['/models','models'],['/resources','resources'],['/integrations','integrations'],['/settings','settings'],[wsPath,'workspace']];
const browser=await chromium.launch();
const ctx=await browser.newContext();
const errors=[];
ctx.on('console',m=>{ if(m.type()==='error') errors.push(m.location().url+': '+m.text()); });
ctx.on('requestfailed',r=>errors.push('FAILED '+r.url()));
for(const [url,name] of pages){
  const page=await ctx.newPage();
  try{
    const res=await page.goto(BASE+url,{waitUntil:'networkidle',timeout:15000});
    const status=res?res.status():'--';
    await page.waitForTimeout(600);
    const t=await page.title();
    console.log('['+name+'] '+status+' "'+t+'"');
  }catch(e){ console.log('['+name+'] ERROR '+e.message.slice(0,120)); }
  await page.close();
}
await browser.close();
console.log('--- console errors ---');
errors.slice(0,40).forEach(e=>console.log('  ',e));
console.log('TOTAL ERRORS:', errors.length);
