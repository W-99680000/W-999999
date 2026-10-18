const { chromium } = require(process.env.PW_MOD);
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  await p.goto(FILE); await p.waitForTimeout(500);
  console.log(await p.evaluate(() => {
    const slot = document.getElementById('modelCardSlot');
    const card = document.querySelector('.model-card');
    return JSON.stringify({
      slotExists: !!slot,
      slotChildren: slot ? slot.children.length : -1,
      slotHTML: slot ? slot.innerHTML.slice(0, 60) : '',
      cardExists: !!card,
      cardParent: card ? card.parentElement.id || card.parentElement.className : '',
      asrGrid: !!document.getElementById('asrGrid'),
      mtGrid: !!document.getElementById('mtGrid'),
      optCards: document.querySelectorAll('.opt-card').length,
      viewModelsHTML: document.getElementById('view-models').innerHTML.length,
      modelCardSlotIndex: document.getElementById('view-models').innerHTML.indexOf('model-card')
    });
  }));
  await p.click('.nav-item[data-nav="models"]'); await p.waitForTimeout(200);
  console.log('models page: model card visible = ' + await p.locator('.model-card').isVisible() + ', opt-cards = ' + await p.locator('.opt-card').count());
  await b.close();
})();
