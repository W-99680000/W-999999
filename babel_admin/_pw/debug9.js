const { chromium } = require(process.env.PW_MOD);
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  await p.goto(FILE); await p.waitForTimeout(500);
  const dump = async (tag) => console.log(tag + ' :: ' + await p.evaluate(() => JSON.stringify({
    menus: document.querySelectorAll('.dd-menu').length,
    triggers: document.querySelectorAll('.dd-trigger').length,
    rec2srcTrigger: !!document.getElementById('rec2-src'),
    rec2srcSiblingMenu: !!document.querySelector('#rec2-src + .dd-menu'),
    rec2HostHTML: (document.getElementById('recordsHostFull')||{}).innerHTML ? document.getElementById('recordsHostFull').innerHTML.length : 0,
    rec2rows: document.querySelectorAll('#rec2-body tr').length,
    pvlines: document.querySelectorAll('[data-pvlines]').length,
    advBody: (document.getElementById('advBody')||{}).innerHTML ? document.getElementById('advBody').innerHTML.length : 0
  })));
  await dump('boot');
  await p.click('.nav-item[data-nav="records"]'); await p.waitForTimeout(200); await dump('records');
  await p.fill('#rec2-q', 'ja'); await p.waitForTimeout(200); await dump('after-fill');
  await p.click('.nav-item[data-nav="models"]'); await p.waitForTimeout(200); await dump('models');
  await p.click('.nav-item[data-nav="loopback"]'); await p.waitForTimeout(200); await dump('loopback');
  await b.close();
})();
