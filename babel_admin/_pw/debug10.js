const { chromium } = require(process.env.PW_MOD);
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  const errs=[]; p.on('pageerror', e=>errs.push('PAGEERROR '+e.message));
  const step = async (tag, fn) => { try { await fn(); console.log('OK   ' + tag); } catch(e){ console.log('FAIL ' + tag + ' :: ' + String(e).split('\n')[0]); } };
  await p.goto(FILE); await p.waitForTimeout(500);
  await p.click('.nav-item[data-nav="loopback"]'); await p.waitForTimeout(200);
  await step('open #tb-mt', () => p.click('#tb-mt'));
  console.log('   state after open: ' + await p.evaluate(() => {
    const t = document.getElementById('tb-mt');
    const m = t.parentNode.querySelector('.dd-menu');
    const o = m.querySelector('.dd-opt[data-value="local:hh-translate-base"]');
    const r = o.getBoundingClientRect();
    return JSON.stringify({ open: t.parentNode.classList.contains('open'), aria: t.getAttribute('aria-expanded'),
      menuDisp: getComputedStyle(m).display, optRect: Math.round(r.x)+','+Math.round(r.y)+' '+Math.round(r.width)+'x'+Math.round(r.height),
      inViewport: r.top >= 0 && r.bottom <= window.innerHeight, scrollY: window.scrollY });
  }));
  await step('click unavailable opt', () => p.locator('#tb-mt ~ .dd-menu .dd-opt[data-value="local:hh-translate-base"]').dispatchEvent('click'));
  await step('open #tb-mt again', () => p.click('#tb-mt'));
  await step('click argos opt', () => p.locator('#tb-mt ~ .dd-menu .dd-opt[data-value="offline:argos-en-zh"]').click({ timeout: 8000 }));
  await step('click unavailable card', () => p.locator('#mtGrid .opt-card[data-id="local:hh-translate-base"]').dispatchEvent('click'));
  console.log('errs: ' + (errs.join('\n')||'none'));
  await b.close();
})();
