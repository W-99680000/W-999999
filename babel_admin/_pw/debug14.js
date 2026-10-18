const { chromium } = require(process.env.PW_MOD);
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  await p.goto(FILE); await p.waitForTimeout(500);
  const rows = () => p.locator('#view-records table.rec tbody tr').count();
  const dump = async (t) => console.log(t.padEnd(30) + ' rows=' + await rows() + ' rec2srcOpen=' + await p.evaluate(() => { const e = document.getElementById('rec2-src'); return e ? e.parentNode.classList.contains('open') : 'MISSING'; }) + ' activeView=' + await p.evaluate(() => (document.querySelector('.view.active')||{}).id));
  await p.click('.nav-item[data-nav="records"]'); await p.waitForTimeout(200); await dump('records view');
  await p.fill('#rec2-q', 'ja'); await p.waitForTimeout(200); await dump('fill ja');
  await p.fill('#rec2-q', 'zzzz'); await p.waitForTimeout(200); await dump('fill zzzz');
  await p.fill('#rec2-q', ''); await p.waitForTimeout(200); await dump('fill empty');
  try { await p.click('#rec2-src', { timeout: 5000 }); console.log('trigger click OK'); } catch(e) { console.log('trigger click FAIL ' + String(e).split('\n')[0]); }
  await p.waitForTimeout(200); await dump('after trigger click');
  console.log('menu opts = ' + await p.locator('#rec2-src ~ .dd-menu .dd-opt').count());
  try { await p.locator('#rec2-src ~ .dd-menu .dd-opt[data-value="cache"]').click({ timeout: 5000 }); console.log('option click OK'); } catch(e) { console.log('option click FAIL ' + String(e).split('\n')[0]); }
  await p.waitForTimeout(200); await dump('after option click');
  await b.close();
})();
