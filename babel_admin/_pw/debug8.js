const { chromium } = require(process.env.PW_MOD);
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  const errs=[]; p.on('pageerror', e=>errs.push('PAGEERROR '+e.message));
  await p.goto(FILE); await p.waitForTimeout(500);
  await p.click('.nav-item[data-nav="models"]'); await p.waitForTimeout(200);
  console.log('models view: pv ctrls count = ' + await p.locator('#previewCtrls [data-pvlines]').count());
  await p.click('.nav-item[data-nav="loopback"]'); await p.waitForTimeout(300);
  const info = await p.evaluate(() => {
    const host = document.getElementById('previewCtrls');
    const v = document.getElementById('view-translate');
    const box = document.querySelector('.sub-box');
    return JSON.stringify({
      activeViews: Array.from(document.querySelectorAll('.view.active')).map(x=>x.id),
      ctrlsHTMLlen: host ? host.innerHTML.length : -1,
      pvlines: document.querySelectorAll('[data-pvlines]').length,
      pvlayout: document.querySelectorAll('[data-pvlayout]').length,
      hostVisible: host ? (host.getBoundingClientRect().width > 0) : false,
      viewDisplay: getComputedStyle(v).display,
      hostParentChain: (() => { let el = host, out=[]; while(el && el !== document.body){ out.push((el.id||el.className||el.tagName)); el = el.parentElement; } return out.join(' < '); })(),
      boxDisplay: box ? getComputedStyle(box).display : 'none'
    }, null, 1);
  });
  console.log(info);
  console.log('errs: ' + (errs.join('\n')||'none'));
  await b.close();
})();
