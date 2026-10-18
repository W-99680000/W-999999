const { chromium } = require(process.env.PW_MOD);
const fs = require('fs');
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
const SHOT = 'D:/NEW WORK/work/live-subtitle/babel_admin/_shots';
fs.mkdirSync(SHOT, { recursive: true });
const asr = [], mt = [];
for (let i = 1; i <= 9; i++) asr.push({ id: 'local:asr-' + i, name: '本地识别模型 ' + i, vendor: 'faster-whisper', source: 'local',
  langs: ['en','ja'], vram_mb: 800 + i * 200, latency: (0.4 + i * 0.05).toFixed(2) + 's / 4s段', accuracy: i > 6 ? 'best' : 'good', available: true, note: '本机权重' });
for (let i = 1; i <= 8; i++) mt.push({ id: 'mt-' + i, name: '翻译模型 ' + i, vendor: i % 2 ? '阿里云百炼' : 'llama.cpp', source: i % 2 ? 'remote' : 'local',
  langs: ['*'], vram_mb: i % 2 ? 0 : 1100, latency: (0.3 + i * 0.04).toFixed(2) + 's/段', quality: i > 5 ? 'best' : 'good', available: true, note: '默认' });
mt.push({ id: 'local:hh-translate-large', name: 'HY-MT 7B (GGUF)', vendor: 'llama.cpp', source: 'local', langs: ['*'], vram_mb: 4700,
  latency: '0.9s/段', quality: 'best', available: false, unavailable_reason: '找不到 gguf 文件：models/HY-MT-7B.gguf' });
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  const errs = []; p.on('pageerror', e => errs.push('PAGEERROR ' + e.message));
  p.on('console', m => { if (m.type() === 'error') errs.push('CONSOLE ' + m.text().slice(0, 120)); });
  await p.route('**/api/models', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    schema: 1, asr, mt,
    modes: { loopback: { label: '实时回环系统', goal: '低延迟', asr: 'local:asr-2', mt: 'mt-1', asr_stream: { chunk: '2s' }, params: { mt_batch: 2, temperature: 0.1 } },
             cache:    { label: '视频缓存',     goal: '高准确', asr: 'local:asr-7', mt: 'mt-6', params: { mt_batch: 6, chunk: '16s' } } },
    devices: [{ id: 'cuda:0', name: 'NVIDIA GeForce RTX 4070 Laptop GPU', total_mb: 8188, role: 'recognition' },
              { id: 'cuda:0', name: 'NVIDIA GeForce RTX 4070 Laptop GPU', total_mb: 8188, role: 'translation' }]
  }) }));
  await p.goto(FILE); await p.waitForTimeout(900);
  const probe = await p.evaluate(() => {
    const txt = document.body.innerText;
    const cands = Array.from(document.querySelectorAll('.opt-card'));
    const bad = cands.filter(c => c.classList.contains('unavailable'));
    return {
      dataSource: document.getElementById('dsText').textContent,
      sideSource: document.getElementById('engineSrcTxt').textContent,
      title: document.getElementById('pageTitle').textContent,
      subtitleHasMode: document.getElementById('pageSub').innerText.includes('实时回环系统'),
      toolbarLabels: Array.from(document.querySelectorAll('#toolbarGrid .field-label')).map(e => e.innerText.trim()),
      toolbarAsr: document.querySelector('#tb-asr .dd-label').textContent,
      toolbarMt: document.querySelector('#tb-mt .dd-label').textContent,
      selectedAsr: (document.querySelector('#asrGrid .opt-card.selected')||{dataset:{}}).dataset.id,
      asrCands: document.querySelectorAll('#asrGrid .opt-card').length,
      mtCands: document.querySelectorAll('#mtGrid .opt-card').length,
      unavailable: bad.map(c => c.dataset.id + ' :: ' + (c.querySelector('.oc-note')||{}).innerText),
      unavailableClickable: bad.every(c => c.getAttribute('aria-disabled') === 'true'),
      statusCards: Array.from(document.querySelectorAll('.main-inner article.card')).filter(a => /运行状态/.test(a.textContent)).length,
      hasUndefined: /undefined/.test(txt),
      hint: document.getElementById('modeHint').textContent,
      vram: document.getElementById('deviceTag').textContent
    };
  });
  console.log(JSON.stringify(probe, null, 1));
  await p.screenshot({ path: SHOT + '/f1-workspace.png', fullPage: true });
  await p.click('.nav-item[data-nav="models"]'); await p.waitForTimeout(300);
  await p.screenshot({ path: SHOT + '/f2-models.png', fullPage: true });
  await p.click('.seg-item[data-mode="cache"]'); await p.waitForTimeout(300);
  console.log('cache: asr=' + await p.locator('#tb-asr .dd-label').innerText() + ' | mt=' + await p.locator('#tb-mt .dd-label').innerText() + ' | hint=' + await p.locator('#modeHint').innerText());
  await p.screenshot({ path: SHOT + '/f3-cache.png', fullPage: true });
  await p.click('.nav-item[data-nav="records"]'); await p.waitForTimeout(300);
  await p.screenshot({ path: SHOT + '/f4-records.png', fullPage: true });
  await p.click('.nav-item[data-nav="settings"]'); await p.waitForTimeout(200);
  await p.screenshot({ path: SHOT + '/f5-settings.png', fullPage: true });
  console.log('errors: ' + (errs.join(' || ') || 'none'));
  await b.close();
})();
