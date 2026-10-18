const { chromium } = require(process.env.PW_MOD);
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  await p.goto(FILE); await p.waitForTimeout(600);
  await p.click('#tb-mt');
  await p.evaluate(() => document.querySelector('#tb-mt ~ .dd-menu .dd-opt[data-value="local:hh-translate-base"]').click());
  await p.click('#tb-mt'); await p.waitForTimeout(200);
  console.log(await p.evaluate(() => {
    const t = document.getElementById('tb-mt');
    const m = t.parentNode.querySelector('.dd-menu');
    const o = m.querySelector('.dd-opt[data-value="offline:argos-en-zh"]');
    const r = o.getBoundingClientRect();
    const mid = { x: r.x + r.width/2, y: r.y + r.height/2 };
    const topEl = document.elementFromPoint(mid.x, mid.y);
    return JSON.stringify({
      open: t.parentNode.classList.contains('open'),
      aria: t.getAttribute('aria-expanded'),
      menuDisp: getComputedStyle(m).display,
      optRect: Math.round(r.x)+','+Math.round(r.y)+' '+Math.round(r.width)+'x'+Math.round(r.height),
      inViewport: r.top>=0 && r.bottom<=innerHeight,
      elementFromPoint: topEl ? (topEl.tagName + '.' + (topEl.className||'') + '#' + (topEl.id||'')) : 'null',
      isInsideOption: topEl ? !!topEl.closest('.dd-opt') : false,
      scrollY: window.scrollY, innerH: innerHeight
    }, null, 1);
  }));
  await b.close();
})();
