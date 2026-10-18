const { chromium } = require(process.env.PW_MOD);
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  await p.goto(FILE); await p.waitForTimeout(800);
  // 工具条绑定检查
  console.log(await p.evaluate(() => {
    const t = document.getElementById('tb-mt'); const m = t.parentNode.querySelector('.dd-menu');
    return 'tb-mt menuBount=' + (m.dataset.bound||'none') + ' menuParent=' + m.parentNode.className + ' triggerParent=' + t.parentNode.className;
  }));
  await p.click('#tb-mt');
  await p.evaluate(() => console.log('DBG before opt click, selected=' + (document.querySelector('#tb-mt ~ .dd-menu .dd-opt[data-value="offline:argos-en-zh"]') ? 'found' : 'MISSING')));
  await p.locator('#tb-mt + .dd-menu .dd-opt[data-value="offline:argos-en-zh"]').click(); await p.waitForTimeout(300);
  console.log('after opt click: applyText=' + (await p.locator('#applyText').innerText()).slice(0, 60));
  console.log('toolbar label=' + await p.locator('#tb-mt .dd-label').innerText());
  // 记录表
  await p.click('.nav-item[data-nav="records"]'); await p.waitForTimeout(200);
  console.log(await p.evaluate(() => {
    const t = document.getElementById('rec2-src-dd'); const m = t ? document.querySelector('#rec2-src-dd + .dd-menu') : null;
    return 'rec2-src-dd exists=' + !!t + ' menuFound=' + !!m + ' opts=' + (m ? m.querySelectorAll('.dd-opt').length : 0) + ' menuParentTag=' + (m ? m.parentNode.tagName : '-');
  }));
  await p.click('#rec2-src-dd'); await p.waitForTimeout(200);
  await p.locator('#rec2-src-dd + .dd-menu .dd-opt[data-value="cache"]').click(); await p.waitForTimeout(300);
  console.log('rows after src=cache filter: ' + await p.locator('#view-records table.rec tbody tr').count());
  await b.close();
})();
