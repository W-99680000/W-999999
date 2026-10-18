const { chromium } = require(process.env.PW_MOD);
const fs = require('fs');
const SHOT = 'D:/NEW WORK/work/live-subtitle/babel_admin/_shots';
fs.mkdirSync(SHOT, { recursive: true });
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1050 } });
  const errs = []; p.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  p.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text().slice(0,140)); });
  await p.goto('http://127.0.0.1:8791/', { waitUntil: 'load' });
  await p.waitForTimeout(1500);
  const probe = await p.evaluate(() => {
    const cards = Array.from(document.querySelectorAll('.opt-card'));
    const bad = cards.filter(c => c.classList.contains('unavailable'));
    return {
      sourceChip: document.getElementById('dsText').textContent,
      sideSource: document.getElementById('engineSrcTxt').textContent,
      title: document.getElementById('pageTitle').textContent,
      activeNav: (document.querySelector('.nav-item.active')||{dataset:{}}).dataset.nav,
      asrCands: document.querySelectorAll('#asrGrid .opt-card').length,
      mtCands: document.querySelectorAll('#mtGrid .opt-card').length,
      totalCards: cards.length,
      toolbarAsr: document.querySelector('#tb-asr .dd-label').textContent,
      toolbarMt: document.querySelector('#tb-mt .dd-label').textContent,
      hint: document.getElementById('modeHint').textContent,
      unavailable: bad.map(c => c.dataset.id + ' :: ' + c.querySelector('.oc-note').innerText.trim()),
      wiredBadges: document.querySelectorAll('#asrGrid .meta-pill, #mtGrid .meta-pill').length,
      usedBy: Array.from(document.querySelectorAll('#mtGrid .meta-pill')).map(e => e.innerText).filter(t => t.includes('被')),
      hasUndefined: /undefined|NaN/.test(document.body.innerText),
      deviceTag: document.getElementById('deviceTag').textContent,
      note: document.getElementById('modelSrcNoteTxt').textContent
    };
  });
  console.log(JSON.stringify(probe, null, 1));
  await p.screenshot({ path: SHOT + '/live1-workspace.png', fullPage: true });
  await p.click('.nav-item[data-nav="models"]'); await p.waitForTimeout(400);
  await p.screenshot({ path: SHOT + '/live2-models.png', fullPage: true });
  await p.click('.seg-item[data-mode="cache"]'); await p.waitForTimeout(400);
  console.log('cache mode: asr=' + await p.locator('#tb-asr .dd-label').innerText() + ' | mt=' + await p.locator('#tb-mt .dd-label').innerText() + ' | anomalyChip=' + (await p.locator('#headChips').innerText()).includes('绑定缺失'));
  await p.screenshot({ path: SHOT + '/live3-cache.png', fullPage: true });
  console.log('errors: ' + (errs.join(' || ') || 'none'));
  await b.close();
})();
