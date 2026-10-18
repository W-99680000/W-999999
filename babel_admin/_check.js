// 临时自检脚本（不属于交付物；检查后删除）
const fs = require('fs');
const p = 'D:/NEW WORK/work/live-subtitle/babel_admin/index.html';
const s = fs.readFileSync(p, 'utf8');
const out = [];
out.push('bytes=' + Buffer.byteLength(s, 'utf8'));
out.push('doctype=' + /^<!DOCTYPE html>/i.test(s));
out.push('html_open=' + (s.match(/<html[\s>]/g) || []).length + ' html_close=' + (s.match(/<\/html>/g) || []).length);
out.push('head=' + (s.match(/<head>/g) || []).length + '/' + (s.match(/<\/head>/g) || []).length);
out.push('body=' + (s.match(/<body>/g) || []).length + '/' + (s.match(/<\/body>/g) || []).length);
out.push('style=' + (s.match(/<style>/g) || []).length + '/' + (s.match(/<\/style>/g) || []).length);
out.push('script=' + (s.match(/<script>/g) || []).length + '/' + (s.match(/<\/script>/g) || []).length);
out.push('aside=' + (s.match(/<aside[\s>]/g) || []).length + '/' + (s.match(/<\/aside>/g) || []).length);
out.push('main=' + (s.match(/<main[\s>]/g) || []).length + '/' + (s.match(/<\/main>/g) || []).length);
out.push('article=' + (s.match(/<article[\s>]/g) || []).length + '/' + (s.match(/<\/article>/g) || []).length);
out.push('div=' + (s.match(/<div[\s>]/g) || []).length + '/' + (s.match(/<\/div>/g) || []).length);
out.push('span=' + (s.match(/<span[\s>]/g) || []).length + '/' + (s.match(/<\/span>/g) || []).length);
out.push('button=' + (s.match(/<button[\s>]/g) || []).length + '/' + (s.match(/<\/button>/g) || []).length);
out.push('section=' + (s.match(/<section[\s>]/g) || []).length + '/' + (s.match(/<\/section>/g) || []).length);
out.push('svg=' + (s.match(/<svg[\s>]/g) || []).length + '/' + (s.match(/<\/svg>/g) || []).length);
out.push('table=' + (s.match(/<table[\s>]/g) || []).length + '/' + (s.match(/<\/table>/g) || []).length);
out.push('residual_data_icon=' + ((s.match(/data-icon/g) || []).length));
out.push('external_refs_http=' + ((s.match(/https?:\/\/(?!dashscope)/g) || []).length));
out.push('external_refs_src_href_cdn=' + ((s.match(/(src|href)\s*=\s*["'](?!#)/g) || []).length));
// 提取 script 做语法检查
const m = s.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { out.push('SCRIPT_EXTRACT=FAIL'); }
else {
  fs.writeFileSync('D:/NEW WORK/work/live-subtitle/babel_admin/_script.js', m[1], 'utf8');
  out.push('script_bytes=' + Buffer.byteLength(m[1], 'utf8'));
}
// BABEL_MODELS 字段检查
const need = ['schema:', 'asr:', 'mt:', 'modes:', 'devices:',
  'id:', 'name:', 'vendor:', 'source:', 'langs:', 'vram_mb:', 'latency:', 'accuracy:', 'available:', 'note:',
  'quality:', 'loopback:', 'cache:', 'label:', 'preset:', 'total_mb:', 'role:'];
need.forEach(function(k){ if (s.indexOf(k) < 0) out.push('MISSING_CONTRACT_KEY=' + k); });
out.push('contract_keys_ok');
console.log(out.join('\n'));
