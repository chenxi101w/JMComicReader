/* LibraryDrag 纯逻辑单测（node 直跑，无 DOM） */
'use strict';
require('../web/static/js/library-drag-logic.js');
var L = global.LibraryDrag;

var pass = 0, fail = 0;
function check(cond, name) {
  if (cond) { pass++; console.log('[OK ] ' + name); }
  else { fail++; console.log('[FAIL] ' + name); }
}

// ── 阈值 ──
check(L.THRESHOLD === 6, '阈值 = 6px');
check(L.shouldBegin(0, 0) === false, '零位移不起拖');
check(L.shouldBegin(3, 2) === false, '5px 不起拖（普通点击放行）');
check(L.shouldBegin(6, 0) === true, '水平 6px 起拖');
check(L.shouldBegin(0, 6) === true, '垂直 6px 起拖');
check(L.shouldBegin(-4, -3) === true, '负向 7px 起拖（取绝对值）');

// ── 载荷解析 ──
var card = L.resolvePayload({ isCard: true, jmId: '454204', catId: undefined });
check(card && card.type === 'comic' && card.payload === '454204' && card.dragCatId === null,
      '漫画卡片 → payload=纯 jmId，dragCatId=null');

var cat = L.resolvePayload({ isCard: false, jmId: undefined, catId: '7' });
check(cat && cat.type === 'cat' && cat.payload === 'cat:7' && cat.dragCatId === 7,
      '分类行 → payload=cat:<id>，dragCatId=7');

check(L.resolvePayload({ isCard: false, jmId: undefined, catId: undefined }) === null,
      '虚拟行（无 catId）→ 不可拖');
check(L.resolvePayload(null) === null, '空源 → null');
check(L.resolvePayload({ isCard: true, jmId: '', catId: '' }) === null, '空 id → null');

// ── 目标高亮态 ──
var isDesc = function (dragId, tid) { return tid === 99; }; // 约定：tid=99 是任何分类的子孙
check(L.targetState(null, 3, isDesc) === 'over', '拖漫画 → 目标永远是 over');
check(L.targetState(7, 8, isDesc) === 'over', '拖分类到别的分类 → over');
check(L.targetState(7, 7, isDesc) === 'invalid', '拖到自己 → invalid');
check(L.targetState(7, 99, isDesc) === 'invalid', '拖到自己子孙 → invalid');
check(L.targetState(7, 0, isDesc) === 'over', '目标=顶层/虚拟行 → over（可移回顶层）');

// ── 落点判定 ──
check(L.shouldDrop(true) === true, '已起拖 → 执行落点');
check(L.shouldDrop(false) === false, '未起拖 → 普通点击');

console.log('----------------------------------------');
console.log('LibraryDrag 单测：' + pass + ' 通过 / ' + fail + ' 失败');
process.exit(fail ? 1 : 0);
