const { chromium } = require(process.env.PW_MOD || 'playwright');
const fs = require('fs');
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  const logs = [];
  p.on('console', m => logs.push(m.type() + ': ' + m.text()));
  p.on('pageerror', e => logs.push('PAGEERROR: ' + e.message));
  await p.goto(FILE);
  await p.waitForTimeout(300);

  console.log('=== duplicate ids ===');
  console.log(JSON.stringify(await p.evaluate(() => {
    const seen = {}, dup = [];
    document.querySelectorAll('[id]').forEach(e => { if (seen[e.id]) dup.push(e.id); seen[e.id] = 1; });
    return dup;
  })));

  console.log('=== dropdown toggle probe ===');
  const probe = await p.evaluate(() => {
    const t = document.getElementById('tb-mt');
    const out = [];
    const dump = l => out.push(l + ' open=' + t.parentNode.classList.contains('open') + ' aria=' + t.getAttribute('aria-expanded'));
    dump('init');
    t.click(); dump('after native click 1');
    t.click(); dump('after native click 2');
    t.click(); dump('after native click 3');
    // 模拟真实鼠标事件（bubbles + composed）
    t.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    dump('after dispatch MouseEvent');
    return out;
  });
  console.log(probe.join('\n'));

  console.log('=== playwright trust click ===');
  await p.locator('#tb-mt').click();
  console.log('after pw click: ' + await p.evaluate(() => {
    const t = document.getElementById('tb-mt');
    return 'open=' + t.parentNode.classList.contains('open') + ' aria=' + t.getAttribute('aria-expanded');
  }));

  console.log('=== toolbar rebuild keeps listeners? ===');
  console.log(await p.evaluate(() => {
    const t = document.getElementById('tb-mt');
    return 'menuBound=' + (t.parentNode.querySelector('.dd-menu').dataset.bound || 'none');
  }));

  console.log('=== logs ===');
  console.log(logs.join('\n') || '(none)');
  await b.close();
})();
