const { chromium } = require(process.env.PW_MOD);
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  await p.goto(FILE); await p.waitForTimeout(500);
  console.log(await p.evaluate(() => {
    const el = document.getElementById('rec2-src');
    let chain = [], n = el;
    while (n && n !== document.body){ chain.push(n.tagName + (n.id ? '#'+n.id : '') + (n.className ? '.'+String(n.className).split(' ')[0] : '')); n = n.parentElement; }
    const parent = el.parentElement;
    return JSON.stringify({
      chain: chain.join(' < '),
      parentClass: parent.className,
      parentChildren: Array.from(parent.children).map(c => c.tagName + (c.id?'#'+c.id:'') + '.' + String(c.className).split(' ')[0]),
      menuExists: !!document.getElementById('rec2-src-dd'),
      menuCountGlobal: document.querySelectorAll('.dd-menu').length,
      nextSibling: el.nextElementSibling ? el.nextElementSibling.tagName + '.' + String(el.nextElementSibling.className).split(' ')[0] : 'none'
    }, null, 1);
  }));
  await b.close();
})();
