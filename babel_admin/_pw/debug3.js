const { chromium } = require(process.env.PW_MOD || 'playwright');
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  const logs = [];
  p.on('pageerror', e => logs.push('PAGEERROR: ' + e.message));
  p.on('console', m => { if (m.type() === 'error') logs.push('CONSOLE: ' + m.text()); });
  await p.goto(FILE);
  await p.waitForTimeout(200);

  const dumpState = async (tag) => {
    const s = await p.evaluate(() => ({
      rec1q: (() => { const e = document.getElementById('rec1-q'); if (!e) return 'MISSING'; const r = e.getBoundingClientRect(); return Math.round(r.width) + 'x' + Math.round(r.height); })(),
      rec2q: (() => { const e = document.getElementById('rec2-q'); if (!e) return 'MISSING'; const r = e.getBoundingClientRect(); return Math.round(r.width) + 'x' + Math.round(r.height); })(),
      rec1bodyRows: document.querySelectorAll('#rec1-body tr').length,
      rec2bodyRows: document.querySelectorAll('#rec2-body tr').length,
      activeView: Array.from(document.querySelectorAll('.view')).filter(v => v.classList.contains('active')).map(v => v.id),
      tbmt: (() => { const e = document.getElementById('tb-mt'); return e ? 'expanded=' + e.getAttribute('aria-expanded') + ' open=' + e.parentNode.classList.contains('open') : 'MISSING'; })(),
      apply: (document.getElementById('applyText') || {}).innerText || 'MISSING'
    }));
    console.log('[' + tag + '] ' + JSON.stringify(s));
  };

  await dumpState('boot');
  await p.click('.nav-item[data-nav="records"]');
  await p.waitForTimeout(200);
  await dumpState('records-view');
  try { await p.fill('#rec1-q', 'ja', { timeout: 4000 }); console.log('rec1 fill OK rows=' + await p.locator('#view-records table.rec tbody tr').count()); }
  catch (e) { console.log('rec1 fill FAIL'); }
  try { await p.fill('#rec2-q', 'ja', { timeout: 4000 }); console.log('rec2 fill OK'); }
  catch (e) { console.log('rec2 fill FAIL'); }

  // 回到模型页做下拉测试
  await p.click('.nav-item[data-nav="models"]');
  await p.waitForTimeout(150);
  await p.click('#tb-mt');
  await p.waitForTimeout(150);
  const before = await p.locator('#applyText').innerText();
  await p.locator('#tb-mt + .dd-menu .dd-opt[data-value="local:hh-translate-base"]').dispatchEvent('click');
  await p.waitForTimeout(200);
  const after = await p.locator('#applyText').innerText();
  console.log('before-identical=' + (before === after));
  console.log('before=' + JSON.stringify(before));
  console.log('after =' + JSON.stringify(after));
  console.log('toast=' + JSON.stringify(await p.locator('.toast').allInnerTexts()));
  console.log('tbmt after=' + await p.evaluate(() => { const e = document.getElementById('tb-mt'); return e.getAttribute('aria-expanded') + '/' + e.parentNode.classList.contains('open'); }));
  console.log(logs.join('\n') || '(no logs)');
  await b.close();
})();
