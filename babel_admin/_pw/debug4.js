const { chromium } = require(process.env.PW_MOD || 'playwright');
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  await p.goto(FILE);
  await p.waitForTimeout(600);
  const count = async (tag) => {
    const s = await p.evaluate(() => ({
      articles: document.querySelectorAll('.main-inner article.card').length,
      statusCards: Array.from(document.querySelectorAll('.main-inner article.card')).filter(a => /运行状态/.test(a.textContent)).length,
      speedGrids: document.querySelectorAll('.speed-grid, #speedGrid').length,
      spark: document.querySelectorAll('svg.spark').length,
      previews: Array.from(document.querySelectorAll('.main-inner article.card')).filter(a => /字幕预览/.test(a.textContent)).length,
      recCards: Array.from(document.querySelectorAll('.main-inner article.card')).filter(a => /同传记录/.test(a.textContent)).length,
      modelCards: document.querySelectorAll('#modelCardSlot .model-card').length,
      viewTranslateChildren: Array.from(document.querySelectorAll('#view-translate > *')).map(e => e.tagName + '.' + (e.className || e.id)).join(' | '),
      activeViews: Array.from(document.querySelectorAll('.view.active')).map(v => v.id)
    }));
    console.log('[' + tag + '] ' + JSON.stringify(s, null, 1));
  };
  await count('boot');
  await p.click('.nav-item[data-nav="models"]'); await p.waitForTimeout(150);
  await p.click('.nav-item[data-nav="loopback"]'); await p.waitForTimeout(150);
  await count('after-round-trip');
  console.log('toolbar asr label = ' + await p.locator('#tb-asr .dd-label').innerText());
  console.log('toolbar mt label  = ' + await p.locator('#tb-mt .dd-label').innerText());
  console.log('modeHint = ' + await p.locator('#modeHint').innerText());
  await b.close();
})();
