const { chromium } = require(process.env.PW_MOD);
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  await p.goto(FILE); await p.waitForTimeout(600);
  const st = async (t) => console.log(t.padEnd(34) + ' open=' + await p.evaluate(() => document.getElementById('tb-mt').parentNode.classList.contains('open')));
  await st('init');
  await p.click('#tb-mt'); await p.waitForTimeout(150); await st('A: pw click trigger');
  await p.locator('#tb-mt ~ .dd-menu .dd-opt[data-value="local:hh-translate-base"]').dispatchEvent('click'); await p.waitForTimeout(150); await st('B: dispatchEvent unavailable');
  await p.click('#tb-mt'); await p.waitForTimeout(150); await st('C: pw click trigger');
  await p.click('#tb-mt'); await p.waitForTimeout(150); await st('D: pw click trigger again');
  await b.close();
})();
