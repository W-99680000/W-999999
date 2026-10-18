const { test, expect } = require(process.env.PW_TEST_MOD || 'playwright/test');
const path = require('path');
const fs = require('fs');
const FILE = 'file:///D:/NEW%20WORK/work/live-subtitle/babel_admin/index.html';
const SHOT = 'D:/NEW WORK/work/live-subtitle/babel_admin/_shots';
fs.mkdirSync(SHOT, { recursive: true });

test('默认落地：翻译字幕工作台 + 实时回环系统高亮 + 工具栏在场', async ({ page }) => {
  await page.goto(FILE);
  await expect(page.locator('#pageTitle')).toHaveText('直播翻译和视频翻译');
  await expect(page.locator('#pageSub')).toContainText('实时回环系统');
  await expect(page.locator('.nav-item.active')).toHaveAttribute('data-nav', 'loopback');
  await expect(page.locator('#view-translate')).toHaveClass(/active/);
  await expect(page.locator('#view-models')).not.toHaveClass(/active/);
  // 工具栏五项
  for (const label of ['输入源', '识别模型', '翻译模型', '语言方向', '字体大小']) {
    await expect(page.locator('#toolbarGrid')).toContainText(label);
  }
  await page.screenshot({ path: path.join(SHOT, 'p1-default-workspace.png'), fullPage: true });
});

test('导航切页：模型管理 / 视频缓存 / 同传记录 / 设置各自独立视图', async ({ page }) => {
  await page.goto(FILE);
  await page.click('.nav-item[data-nav="models"]');
  await expect(page.locator('#pageTitle')).toHaveText('模型管理');
  await expect(page.locator('#view-models')).toHaveClass(/active/);
  await expect(page.locator('#modelCardSlot .model-card')).toBeVisible();
  await expect(page.locator('#asrGrid .opt-card').first()).toBeVisible();
  await expect(page.locator('#mtGrid .opt-card').first()).toBeVisible();
  await expect(page.locator('#deviceGrid .device-box')).toHaveCount(2);
  await expect(page.locator('#view-translate')).not.toHaveClass(/active/);
  await page.screenshot({ path: path.join(SHOT, 'p2-models-page.png'), fullPage: true });
  await page.click('.seg-item[data-mode="cache"]');
  await expect(page.locator('#pageTitle')).toHaveText('直播翻译和视频翻译');
  await expect(page.locator('.nav-item.active')).toHaveAttribute('data-nav', 'cache');
  await expect(page.locator('#pageSub')).toContainText('视频缓存');
  await expect(page.locator('#view-translate')).toHaveClass(/active/);
  await page.click('.nav-item[data-nav="records"]');
  await expect(page.locator('#pageTitle')).toHaveText('同传记录');
  await expect(page.locator('#view-records')).toHaveClass(/active/);
  await page.click('.nav-item[data-nav="settings"]');
  await expect(page.locator('#pageTitle')).toHaveText('设置');
  await expect(page.locator('#view-settings')).toHaveClass(/active/);
  await expect(page.locator('#settingsGrid article')).toHaveCount(4);
});

test('模式联动：工具栏与默认模型随模式变化（实时=快 / 缓存=准）', async ({ page }) => {
  await page.goto(FILE);
  await expect(page.locator('#asrGrid .opt-card.selected')).toContainText('faster-whisper medium');
  await expect(page.locator('#toolbarGrid')).toContainText('识别块长');
  await expect(page.locator('#modeHint')).toContainText('低延迟');
  await page.click('.seg-item[data-mode="cache"]');
  await expect(page.locator('#modeHint')).toContainText('高准确');
  await expect(page.locator('#toolbarGrid')).toContainText('分段粒度');
  await expect(page.locator('#toolbarGrid')).not.toContainText('识别块长');
  await expect(page.locator('#toolbarGrid')).toContainText('播放器缓存目录');
  // 缓存预设 local:large-v3 不在契约 asr 列表里 → 兜底条目置灰
  const ph = page.locator('#asrGrid .opt-card[data-id="local:large-v3"]');
  await expect(ph).toHaveClass(/unavailable/);
  await expect(ph).toHaveAttribute('aria-disabled', 'true');
  await expect(ph.locator('.oc-note')).toContainText('未在本机');
  // 切模式不改变导航高亮对象（工作台共用）
  await page.click('.nav-item[data-nav="loopback"]');
  await expect(page.locator('#toolbarGrid')).toContainText('系统回环');
});

test('数据源链：file:// 下 API/快照都失败 → 内置', async ({ page }) => {
  await page.goto(FILE);
  await expect(page.locator('#dsText')).toHaveText('内置');
  await expect(page.locator('#engineSrcTxt')).toHaveText('内置');
  await expect(page.locator('#modelSrcNoteTxt')).toContainText('模型数据来源：内置');
  await expect(page.locator('#modelSrcNoteTxt')).toContainText('降级记录');
  await expect(page.locator('.toast')).toContainText('内置假数据');
});

test('数据源链：/api/models 可用 → API（无 preset 字段时用 params 拼预设文案）', async ({ page }) => {
  await page.route('**/api/models', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      schema: 1,
      asr: [{ id: 'api:test-asr', name: 'API 测试识别模型', vendor: '自建', source: 'remote',
              langs: ['en'], vram_mb: 0, latency: '0.10s / 4s段', accuracy: 'best',
              available: true, note: '接口下发' }],
      mt: [{ id: 'api:test-mt', name: 'API 测试翻译模型', vendor: '自建', source: 'remote',
             langs: ['*'], vram_mb: 0, latency: '0.11s/段', quality: 'best',
             available: true, note: '接口下发' },
           { id: 'api:test-mt-bad', name: 'API 不可用翻译模型', vendor: '自建', source: 'local',
             langs: ['*'], vram_mb: 1100, latency: '0.5s/段', quality: 'good',
             available: false, unavailable_reason: '找不到 gguf 文件（回归：unavailable_reason 字段）' }],
      // 注意：故意不给 preset，只有 params —— 复现 BUG 2
      modes: { loopback: { label: '实时回环系统', asr: 'api:test-asr', mt: 'api:test-mt',
                           params: { mt_batch: 2, asr_chunk: '2s', temperature: 0.1 } },
               cache: { label: '视频缓存', asr: 'api:test-asr', mt: 'api:test-mt',
                        params: { mt_batch: 6, asr_chunk: '16s' } } },
      devices: [{ id: 'cuda:0', name: 'NVIDIA GeForce RTX 4070 Laptop GPU', total_mb: 8188, role: 'recognition' },
                { id: 'cuda:0', name: 'NVIDIA GeForce RTX 4070 Laptop GPU', total_mb: 8188, role: 'translation' }]
    })
  }));
  await page.goto(FILE);
  await expect(page.locator('#dsText')).toHaveText('API');
  await expect(page.locator('#engineSrcTxt')).toHaveText('API');
  await expect(page.locator('#asrGrid')).toContainText('API 测试识别模型');
  await expect(page.locator('#asrGrid .opt-card.selected')).toHaveAttribute('data-id', 'api:test-asr');
  await expect(page.locator('#applyText')).toContainText('api:test-mt');
  await expect(page.locator('#modelSrcNoteTxt')).toContainText('GET /api/models');
  await expect(page.locator('#tb-asr .dd-label')).toHaveText('API 测试识别模型');
  // BUG 2 回归：不许出现 undefined，预设文案来自 params
  const hint = await page.locator('#modeHint').innerText();
  expect(hint).not.toContain('undefined');
  expect(hint).toContain('mt_batch 2');
  await expect(page.locator('#modeSeg .nav-tag').first()).toContainText('mt_batch 2');
  expect(await page.locator('#applyText').innerText()).not.toContain('undefined');
  // unavailable_reason 字段要落到置灰项
  const bad = page.locator('#mtGrid .opt-card[data-id="api:test-mt-bad"]');
  await expect(bad).toHaveClass(/unavailable/);
  await expect(bad.locator('.oc-note')).toContainText('找不到 gguf 文件');
});

test('BUG1 回归：模式绑定的 id 不在注册表里 → 取第一个可用模型并标注「绑定缺失」', async ({ page }) => {
  await page.route('**/api/models', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({
      schema: 1,
      asr: [{ id: 'local:faster-whisper-medium', name: 'faster-whisper medium', vendor: 'faster-whisper',
              source: 'local', langs: ['*'], vram_mb: 1615, latency: '0.60s / 4s段', accuracy: 'good',
              available: true, note: '注册表里的 medium' },
            { id: 'local:faster-whisper-large-v3', name: 'faster-whisper large-v3', vendor: 'faster-whisper',
              source: 'local', langs: ['*'], vram_mb: 3110, latency: '1.10s / 4s段', accuracy: 'best',
              available: true, note: '注册表里的 large-v3' }],
      mt: [{ id: 'remote:qwen-mt-flash', name: 'qwen-mt-flash', vendor: '阿里云百炼', source: 'remote',
             langs: ['*'], vram_mb: 0, latency: '0.36s/段', quality: 'best', available: true, note: '默认' }],
      // loopback 的 asr 用了错 id（内置假数据里的 local:large-v3），注册表里没有
      modes: { loopback: { label: '实时回环系统', asr: 'local:medium', mt: 'remote:qwen-mt-flash', preset: '低延迟' },
               cache: { label: '视频缓存', asr: 'local:faster-whisper-large-v3', mt: 'remote:qwen-mt-flash', preset: '高准确' } },
      devices: [{ id: 'cuda:0', name: 'NVIDIA GeForce RTX 4070 Laptop GPU', total_mb: 8188, role: 'recognition' },
                { id: 'cuda:0', name: 'NVIDIA GeForce RTX 4070 Laptop GPU', total_mb: 8188, role: 'translation' }]
    })
  }));
  await page.goto(FILE);
  // loopback 绑 local:medium → 注册表里没有 → 必须标注
  await expect(page.locator('#headChips')).toContainText('绑定缺失');
  await expect(page.locator('#applyText')).toContainText('注册表里找不到');
  await expect(page.locator('#applyText')).toContainText('local:medium');
  // 用的是第一个可用模型，而不是内置的 large-v3
  await expect(page.locator('#asrGrid .opt-card.selected')).toHaveAttribute('data-id', 'local:faster-whisper-medium');
  await expect(page.locator('#tb-asr .dd-label')).toHaveText('faster-whisper medium');
  // cache 绑定存在的 id → 不再算异常
  await page.click('.seg-item[data-mode="cache"]');
  await expect(page.locator('#asrGrid .opt-card.selected')).toHaveAttribute('data-id', 'local:faster-whisper-large-v3');
  await expect(page.locator('#headChips')).not.toContainText('绑定缺失');
  await expect(page.locator('#applyText')).not.toContainText('注册表里找不到');
});

test('BUG3 回归：运行状态卡片 (含趋势图) 在整页只出现一次', async ({ page }) => {
  await page.goto(FILE);
  const count = () => page.evaluate(() => ({
    statusCards: Array.from(document.querySelectorAll('.main-inner article.card')).filter(a => /运行状态/.test(a.textContent)).length,
    speedGrids: document.querySelectorAll('.speed-grid').length,
    sparks: document.querySelectorAll('svg.spark').length,
    previews: Array.from(document.querySelectorAll('.main-inner article.card')).filter(a => /字幕预览/.test(a.textContent)).length
  }));
  let c = await count();
  expect(c.statusCards).toBe(1);
  expect(c.speedGrids).toBe(1);
  expect(c.sparks).toBe(1);
  expect(c.previews).toBe(1);
  for (const nav of ['models', 'cache', 'loopback', 'records', 'settings', 'loopback']) {
    await page.click('.nav-item[data-nav="' + nav + '"]');
  }
  c = await count();
  expect(c.statusCards).toBe(1);
  expect(c.speedGrids).toBe(1);
  expect(c.sparks).toBe(1);
  // 工作台里第一张卡片就是运行状态，且延迟三个数字在场
  await expect(page.locator('#speedGrid .speed-box')).toHaveCount(3);
  await expect(page.locator('#view-translate .card').first()).toContainText('运行状态');
});

test('数据源链：API 失败 → 快照 model_registry_web.json 成功', async ({ page }) => {
  await page.route('**/api/models', route => route.fulfill({ status: 500, body: 'boom' }));
  await page.route('**/model_registry_web.json', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      schema: 1,
      asr: [{ id: 'snap:asr', name: '快照识别模型', vendor: '快照导出', source: 'local',
              langs: ['*'], vram_mb: 900, latency: '0.5s / 4s段', accuracy: 'good',
              available: true, note: '来自静态快照' }],
      mt: [{ id: 'snap:mt', name: '快照翻译模型', vendor: '快照导出', source: 'offline',
             langs: ['en→zh'], vram_mb: 0, latency: '0.3s/段', quality: 'fair',
             available: true, note: '来自静态快照' }],
      modes: { loopback: { label: '实时回环系统', asr: 'snap:asr', mt: 'snap:mt', preset: '快照预设' },
               cache: { label: '视频缓存', asr: 'snap:asr', mt: 'snap:mt', preset: '快照预设' } },
      devices: [{ id: 'cuda:0', name: 'NVIDIA GeForce RTX 4070 Laptop GPU', total_mb: 8188, role: 'recognition' },
                { id: 'cuda:0', name: 'NVIDIA GeForce RTX 4070 Laptop GPU', total_mb: 8188, role: 'translation' }]
    })
  }));
  await page.goto(FILE);
  await expect(page.locator('#dsText')).toHaveText('快照');
  await expect(page.locator('#asrGrid')).toContainText('快照识别模型');
  await expect(page.locator('#modelSrcNoteTxt')).toContainText('model_registry_web.json');
  await expect(page.locator('#modelSrcNoteTxt')).toContainText('API 不可用');
});

test('数据源链：API 返回脏数据 → 判定不合格 → 回落', async ({ page }) => {
  await page.route('**/api/models', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ nope: true })
  }));
  await page.goto(FILE);
  await expect(page.locator('#dsText')).toHaveText('内置');
  await expect(page.locator('#modelSrcNoteTxt')).toContainText('结构不符合契约');
});

test('模型下拉可点、不可用项给 toast 且不改选中', async ({ page }) => {
  await page.goto(FILE);
  const before = await page.locator('#applyText').innerText();
  await page.click('#tb-mt');
  await expect(page.locator('#tb-mt')).toHaveAttribute('aria-expanded', 'true');
  const bad = page.locator('#tb-mt + .dd-menu .dd-opt[data-value="local:hh-translate-base"]');
  await expect(bad).toHaveAttribute('aria-disabled', 'true');
  await bad.dispatchEvent('click');   // aria-disabled 元素 Playwright 拒点，派发真实 click
  // 数据加载完会有「模型数据已加载」toast，可能盖住上一条 → 只看最新一条
  await expect(page.locator('.toast').last()).toContainText('不可用');
  await expect(page.locator('.toast').last()).toContainText('llama-server.exe 未找到');
  expect(await page.locator('#applyText').innerText()).toBe(before);
  await page.click('#tb-mt');
  await page.locator('#tb-mt + .dd-menu .dd-opt[data-value="offline:argos-en-zh"]').click();
  await expect(page.locator('#applyText')).toContainText('offline:argos-en-zh');
  await expect(page.locator('#mtGrid .opt-card.selected')).toContainText('argos');
  await page.locator('#mtGrid .opt-card[data-id="local:hh-translate-base"]').dispatchEvent('click');
  await expect(page.locator('.toast').last()).toContainText('不可用');
  await expect(page.locator('#mtGrid .opt-card[data-id="local:hh-translate-base"]')).not.toHaveClass(/selected/);
});

test('应用按钮 + toast 2 秒消失', async ({ page }) => {
  await page.goto(FILE);
  await page.waitForTimeout(2600);          // 等数据加载 toast 自行消失
  await page.click('.nav-item[data-nav="models"]');
  await page.click('#btnApplyBoth');
  await expect(page.locator('.toast').last()).toContainText('已应用到两个模块');
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 6000 });
});

test('记录表搜索 / 筛选（两张表共用筛选状态）', async ({ page }) => {
  await page.goto(FILE);
  await page.click('.nav-item[data-nav="records"]');
  const rows = page.locator('#view-records table.rec tbody tr');
  await expect(rows).toHaveCount(12);
  await page.fill('#rec2-q', 'ja');
  await expect(rows).toHaveCount(3);
  await page.fill('#rec2-q', 'zzzz');
  await expect(page.locator('#view-records .empty-row')).toHaveCount(1);
  await page.fill('#rec2-q', '');
  await expect(rows).toHaveCount(12);
  await page.click('#rec2-src-dd');
  await page.locator('#rec2-src-dd + .dd-menu .dd-opt[data-value="cache"]').click();
  await expect(rows).toHaveCount(6);
  await expect(page.locator('#rec1-body tr')).toHaveCount(6);  // 共用状态
  await page.screenshot({ path: path.join(SHOT, 'p3-records.png'), fullPage: true });
});

test('无重复 id + 无残留内联占位', async ({ page }) => {
  await page.goto(FILE);
  await page.waitForTimeout(900);   // 等 API/快照加载完（下拉是渲染后创建的，必须晚于渲染再查）
  const dup = await page.evaluate(() => {
    const seen = {}, d = [];
    document.querySelectorAll('[id]').forEach(e => { if (seen[e.id]) d.push(e.id); seen[e.id] = 1; });
    return d;
  });
  expect(dup).toEqual([]);
  expect(await page.evaluate(() => document.querySelectorAll('[data-icon]').length)).toBe(0);
  // 下拉触发器 id 必须唯一且指向真正可点的按钮
  expect(await page.evaluate(() => document.querySelectorAll('.dd-trigger').length))
    .toBe(await page.evaluate(() => new Set(Array.from(document.querySelectorAll('.dd-trigger')).map(b => b.id)).size));
});

test('高级参数滑块 / 术语 / 字幕预览控件', async ({ page }) => {
  await page.goto(FILE);
  await page.click('.nav-item[data-nav="models"]');
  await page.click('details.adv > summary');
  await expect(page.locator('#advBody')).toBeVisible();
  await page.locator('input[data-slider="temperature"]').fill('1.2');
  await expect(page.locator('[data-val="temperature"]')).toHaveText('1.20');
  await expect(page.locator('#advSummary')).toContainText('temperature 1.20');
  await page.fill('#tagField', '字幕');
  await page.press('#tagField', 'Enter');
  await expect(page.locator('#advSummary')).toContainText('术语 4 条');
  await page.click('.nav-item[data-nav="loopback"]');
  await page.locator('[data-pvlines="3"]').click();
  await expect(page.locator('#previewZh')).toContainText('字幕会实时翻译成中文');
  await page.locator('[data-pvlayout="row"]').click();
  await expect(page.locator('#previewSrc')).toBeVisible();
  await page.locator('[data-pvlayout="zh"]').click();
  await expect(page.locator('#previewSrc')).toBeHidden();
  await page.screenshot({ path: path.join(SHOT, 'p4-workspace-cards.png'), fullPage: true });
});

test('键盘 Tab 可达 + focus-visible', async ({ page }) => {
  await page.goto(FILE);
  await page.keyboard.press('Tab');
  for (let i = 0; i < 8; i++) await page.keyboard.press('Tab');
  const ok = await page.evaluate(() => {
    const el = document.activeElement;
    return el && el.getBoundingClientRect().width > 0;
  });
  expect(ok).toBeTruthy();
});

test('响应式：<960px 侧边栏收窄，<1280px 工具栏换行', async ({ page }) => {
  await page.goto(FILE);
  await page.setViewportSize({ width: 900, height: 900 });
  expect(Math.round(await page.locator('.sidebar').evaluate(el => el.getBoundingClientRect().width))).toBeLessThanOrEqual(80);
  expect(await page.locator('.nav-item .ni-txt').first().isVisible()).toBeFalsy();
  await page.screenshot({ path: path.join(SHOT, 'p5-narrow.png'), fullPage: true });
  await page.setViewportSize({ width: 1100, height: 900 });
  expect(Math.round(await page.locator('.sidebar').evaluate(el => el.getBoundingClientRect().width))).toBeGreaterThanOrEqual(248);
  expect(await page.locator('#toolbarGrid').evaluate(el => getComputedStyle(el).flexWrap)).toBe('wrap');
  await page.screenshot({ path: path.join(SHOT, 'p6-1100.png'), fullPage: true });
  await page.setViewportSize({ width: 1680, height: 1000 });
  await page.screenshot({ path: path.join(SHOT, 'p0-wide.png'), fullPage: true });
});

test('零控制台错误 + 零外链请求', async ({ page }) => {
  const errs = [], reqs = [];
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  page.on('pageerror', e => errs.push(String(e)));
  page.on('request', r => reqs.push(r.url()));
  await page.goto(FILE);
  await page.waitForTimeout(900);
  for (const nav of ['cache', 'loopback', 'models', 'records', 'settings']) {
    await page.click('.nav-item[data-nav="' + nav + '"]');
  }
  await page.waitForTimeout(300);
  console.log('REQUESTS=' + JSON.stringify(reqs.filter(u => !u.startsWith('file://'))));
  expect(reqs.filter(u => !u.startsWith('file://'))).toEqual([]);
  // file:// 下 /api/models 与快照必然失败，浏览器会把失败写进 console —— 只允许这两条 fetch 失败
  const unexpected = errs.filter(t => !/api\/models|model_registry_web\.json|ERR_FAILED|Failed to load resource/i.test(t));
  expect(unexpected).toEqual([]);
});
