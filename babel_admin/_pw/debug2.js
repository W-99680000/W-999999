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
  await p.click('.nav-item[data-nav="records"]');
  await p.waitForTimeout(200);
  const info = await p.evaluate(() => {
    const out = {};
    const q = document.getElementById('rec1-q');
    const r = q.getBoundingClientRect();
    out.rect = { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y) };
    out.offsetParent = q.offsetParent ? q.offsetParent.className || q.offsetParent.tagName : 'null';
    out.display = getComputedStyle(q).display;
    out.vis = getComputedStyle(q).visibility;
    out.opacity = getComputedStyle(q).opacity;
    const v = document.getElementById('view-records');
    out.viewDisplay = getComputedStyle(v).display;
    out.viewRect = (() => { const x = v.getBoundingClientRect(); return { w: Math.round(x.width), h: Math.round(x.height) }; })();
    out.ancestors = [];
    let el = q;
    while (el && el !== document.documentElement) {
      const cs = getComputedStyle(el);
      const rr = el.getBoundingClientRect();
      out.ancestors.push((el.tagName + '.' + (el.className || '')).slice(0, 46) + ' disp=' + cs.display + ' ov=' + cs.overflow + ' wh=' + Math.round(rr.width) + 'x' + Math.round(rr.height));
      el = el.parentElement;
    }
    out.rows = document.querySelectorAll('#rec1-body tr').length;
    out.rowCount = document.querySelectorAll('#view-records table.rec tbody tr').length;
    return out;
  });
  console.log(JSON.stringify(info, null, 1));
  try {
    await p.fill('#rec1-q', 'ja', { timeout: 4000 });
    console.log('FILL OK, value=' + await p.inputValue('#rec1-q'));
    console.log('rows after=' + await p.locator('#view-records table.rec tbody tr').count());
  } catch (e) { console.log('FILL FAILED: ' + String(e).split('\n')[0]); }
  console.log(logs.join('\n') || '(no logs)');
  await b.close();
})();
