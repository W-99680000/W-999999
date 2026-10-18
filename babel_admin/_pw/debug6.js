const { chromium } = require(process.env.PW_MOD);
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe' });
  const p = await b.newPage({ viewport: { width: 1680, height: 1000 } });
  const errs=[]; p.on('pageerror', e=>errs.push('PAGEERROR '+e.message));
  const reqs=[]; p.on('requestfailed', r=>reqs.push('FAILED '+r.url()+' :: '+(r.failure()&&r.failure().errorText)));
  await p.route('**/api/models', route => route.fulfill({ status:200, contentType:'application/json', body: JSON.stringify({
    schema:1,
    asr:[{id:'api:test-asr',name:'API 测试识别模型',vendor:'自建',source:'remote',langs:['en'],vram_mb:0,latency:'0.10s / 4s段',accuracy:'best',available:true,note:'接口下发'}],
    mt:[{id:'api:test-mt',name:'API 测试翻译模型',vendor:'自建',source:'remote',langs:['*'],vram_mb:0,latency:'0.11s/段',quality:'best',available:true,note:'接口下发'}],
    modes:{loopback:{label:'实时回环系统',asr:'api:test-asr',mt:'api:test-mt',params:{mt_batch:2,asr_chunk:'2s',temperature:0.1}},
           cache:{label:'视频缓存',asr:'api:test-asr',mt:'api:test-mt',params:{mt_batch:6,asr_chunk:'16s'}}},
    devices:[{id:'cuda:0',name:'NVIDIA GeForce RTX 4070 Laptop GPU',total_mb:8188,role:'recognition'},{id:'cuda:0',name:'NVIDIA GeForce RTX 4070 Laptop GPU',total_mb:8188,role:'translation'}]
  })}));
  await p.goto(FILE); await p.waitForTimeout(700);
  console.log(await p.evaluate(() => JSON.stringify({
    ds: document.getElementById('dsText').textContent,
    asrCount: document.querySelectorAll('#asrGrid .opt-card').length,
    selected: (document.querySelector('#asrGrid .opt-card.selected')||{}).outerHTML ? document.querySelector('#asrGrid .opt-card.selected').dataset.id : 'NONE',
    asrGridParent: document.getElementById('asrGrid').parentElement.parentElement.className,
    modelCardInSlot: !!document.querySelector('#modelCardSlot .model-card'),
    modelCardParent: document.querySelector('.model-card').parentElement.id || document.querySelector('.model-card').parentElement.className,
    speedGrids: document.querySelectorAll('.speed-grid').length,
    toolbarAsr: document.querySelector('#tb-asr .dd-label').textContent,
    hint: document.getElementById('modeHint').textContent,
    applyText: document.getElementById('applyText').innerText.slice(0,150)
  }, null, 1)));
  console.log('ERRORS: ' + (errs.join('\n')||'none'));
  console.log('REQFAIL: ' + (reqs.join('\n')||'none'));
  await b.close();
})();
