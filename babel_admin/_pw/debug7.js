const { chromium } = require(process.env.PW_MOD);
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  const errs=[]; p.on('pageerror', e=>errs.push('PAGEERROR '+e.message));
  await p.goto(FILE); await p.waitForTimeout(2600);
  const st = async (tag) => console.log(tag + ' :: ' + await p.evaluate(() => {
    const t = document.getElementById('tb-mt');
    const m = t && t.parentNode.querySelector('.dd-menu');
    const opt = m && m.querySelector('.dd-opt[data-value="offline:argos-en-zh"]');
    const r = opt ? opt.getBoundingClientRect() : null;
    return JSON.stringify({ open: t.parentNode.classList.contains('open'), aria: t.getAttribute('aria-expanded'),
      menuDisp: m ? getComputedStyle(m).display : 'no-menu', opt: r ? Math.round(r.width)+'x'+Math.round(r.height) : 'no-opt',
      optHTMLlen: opt ? opt.innerHTML.length : 0, tbmtSameNode: t === window.__t });
  }));
  await p.evaluate(() => { window.__t = document.getElementById('tb-mt'); });
  await st('before');
  await p.click('#tb-mt'); await p.waitForTimeout(250);
  await st('opened');
  try { await p.click('#tb-mt + .dd-menu .dd-opt[data-value="offline:argos-en-zh"]', { timeout: 5000 }); console.log('CLICK OK'); }
  catch(e) { console.log('CLICK FAIL: ' + String(e).split('\n').slice(0,6).join(' | ')); }
  await st('after');
  console.log('errs: ' + (errs.join('\n')||'none'));
  await b.close();
})();
