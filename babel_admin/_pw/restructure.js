// 一次性结构外科：把「模型选择」卡片从 模型管理 视图搬到 #modelCardSlot
const fs = require('fs');
const p = 'D:/NEW WORK/work/live-subtitle/babel_admin/index.html';
let s = fs.readFileSync(p, 'utf8');
const startMark = '        <!-- 模型选择卡片 -->\n        <article class="card pad-lg model-card">\n';
const endMark = '        </article>\n\n        <!-- 运行状态卡片 -->';
const i = s.indexOf(startMark);
const j = s.indexOf(endMark);
if (i < 0 || j < 0) { console.log('MARK_NOT_FOUND i=' + i + ' j=' + j); process.exit(1); }
const block = s.slice(i + '        <!-- 模型选择卡片 -->\n'.length, j + '        </article>\n'.length);
const before = s.slice(0, i);
const after = s.slice(j + '        </article>\n'.length);
const oldLen = s.length;
// 去掉搬走后的空行残留
let cleaned = (before + after).replace(/\n{3,}/g, '\n\n');
// 缩进从 8 空格降到 6（放进 slot 里也保持可读）
const reindented = block.split('\n').map(l => l.startsWith('        ') ? l.slice(2) : l).join('\n');
const slot = '        <div id="modelCardSlot"></div>';
if (cleaned.indexOf(slot) < 0) { console.log('SLOT_NOT_FOUND'); process.exit(1); }
cleaned = cleaned.replace(slot, slot + '\n        <!-- 模型选择卡片（源节点，boot 时移入 slot 以便两页共用同一实例） -->\n' + reindented.trimEnd());
fs.writeFileSync(p, cleaned, 'utf8');
console.log('old=' + oldLen + ' new=' + cleaned.length);
console.log('slotIndex=' + cleaned.indexOf('modelCardSlot'));
console.log('cardIndex=' + cleaned.indexOf('model-card'));
console.log('viewModelsEnd=' + cleaned.indexOf('id="view-translate"'));
