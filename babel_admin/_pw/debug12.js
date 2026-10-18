const { chromium } = require(process.env.PW_MOD);
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  p.on('console', m => { if (m.text().startsWith('DBG')) console.log('  ' + m.text()); });
  await p.goto(FILE); await p.waitForTimeout(600);
  await p.evaluate(() => {
    ['click','mousedown','focusin','blur','focusout'].forEach(t => document.addEventListener(t, e => {
      const dd = e.target.closest && e.target.closest('.dropdown');
      console.log('DBG doc ' + t + ' target=' + (e.target.id || e.target.className || e.target.tagName) + ' dd=' + (dd ? (dd.querySelector('.dd-trigger').id) : 'none') + ' openNow=' + document.querySelectorAll('.dropdown.open').length);
    }, true));
  });
  const st = async (t) => console.log(t + ' open=' + await p.evaluate(() => document.querySelectorAll('.dropdown.open').length + ' tbmtOpen=' + document.getElementById('tb-mt').parentNode.classList.contains('open')));
  await st('boot');
  await p.click('#tb-mt'); await p.waitForTimeout(150); await st('after open click');
  await p.evaluate(() => document.querySelector('#tb-mt ~ .dd-menu .dd-opt[data-value="local:hh-translate-base"]').click()); await p.waitForTimeout(150); await st('after unavailable click');
  await p.click('#tb-mt'); await p.waitForTimeout(200); await st('after 2nd trigger click');
  await b.close();
})();
