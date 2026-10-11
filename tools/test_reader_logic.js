/**
 * reader-logic.js 的单元测试（node 直接运行，无需浏览器/DOM）。
 *
 * 被 tools/selftest_lite.py 调用：node tools/test_reader_logic.js
 * 退出码 0 = 全过；1 = 有失败（会打印明细）。
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.join(__dirname, '..', 'web', 'static', 'js', 'reader-logic.js');
const src = fs.readFileSync(file, 'utf8');
const ctx = {};
vm.createContext(ctx);
vm.runInContext(src, ctx);
const RL = ctx.ReaderLogic;

let failed = 0;
function ok(cond, name) {
  if (cond) {
    console.log('[OK ] ' + name);
  } else {
    failed++;
    console.log('[FAIL] ' + name);
  }
}

if (!RL) {
  console.log('[FAIL] reader-logic.js 未导出 ReaderLogic');
  process.exit(1);
}

/* ── resolveWheelAction ─────────────────────────────────── */
const base = { mode: 'page', atTop: false, atBottom: false, acc: 0, lockUntil: 0, now: 1000 };

ok(RL.resolveWheelAction({ deltaY: 100, buttons: 2 }, base).action === 'zoom',
  '右键+滚轮 → zoom（任何模式）');
ok(RL.resolveWheelAction({ deltaY: 100, buttons: 0 }, { ...base, mode: 'scroll' }).action === 'scroll',
  'scroll 模式 → 原生滚动');
ok(RL.resolveWheelAction({ deltaY: 100, buttons: 0 }, { ...base, mode: 'zoom' }).action === 'zoom',
  'zoom 模式 → 以光标缩放');
ok(RL.resolveWheelAction({ deltaY: 10, buttons: 0 }, base).action === 'idle',
  'page 模式小步 deltaY → idle（防触控板误触）');
const afterAcc = RL.resolveWheelAction({ deltaY: 10, buttons: 0 }, base);
ok(afterAcc.acc === 10, 'idle 时累计 acc=10');
ok(RL.resolveWheelAction({ deltaY: 100, buttons: 0 },
  { ...base, acc: 39, atBottom: true }).action === 'flip',
  '累计跨过阈值（39+100 且已到底）→ flip 判定');
ok(RL.resolveWheelAction({ deltaY: 100, buttons: 0 },
  { ...base, atBottom: false }).action === 'scrollPage',
  'page 模式未到底 → 页内滑动');
ok(RL.resolveWheelAction({ deltaY: 100, buttons: 0 },
  { ...base, atBottom: true }).action === 'flip',
  'page 模式已到底 → 翻下一页');
ok(RL.resolveWheelAction({ deltaY: -100, buttons: 0 },
  { ...base, atTop: true }).action === 'flip',
  'page 模式已到顶向上 → 翻上一页');
ok(RL.resolveWheelAction({ deltaY: -100, buttons: 0 },
  { ...base, atTop: false }).action === 'scrollPage',
  'page 模式未到顶向上 → 页内滑动');
const cooled = RL.resolveWheelAction({ deltaY: 100, buttons: 0 },
  { ...base, lockUntil: 2000, now: 1000 });
ok(cooled.action === 'idle' && cooled.lockUntil === 2000, '冷却期内 → idle 且不重置锁');
const flipped = RL.resolveWheelAction({ deltaY: 100, buttons: 0 }, { ...base, atBottom: true });
ok(flipped.lockUntil === base.now + RL.WHEEL_FLIP_COOLDOWN, '翻页判定会设置冷却锁');

/* ── railIndex ──────────────────────────────────────────── */
ok(RL.railIndex(0, 100, 200, 10) === 0, '滑轨顶部 → 第 0 页');
ok(RL.railIndex(300, 100, 200, 10) === 9, '滑轨底部 → 最后一页');
ok(RL.railIndex(200, 100, 200, 11) === 5, '滑轨正中部 → 中间页（四舍五入）');
ok(RL.railIndex(-50, 100, 200, 10) === 0, '越界夹到 [0, n-1]');
ok(RL.railIndex(9999, 100, 200, 10) === 9, '越界夹到 [0, n-1]');
ok(RL.railIndex(100, 100, 0, 10) === -1, '轨道高 0 → -1（未布局）');
ok(RL.railIndex(100, 100, 200, 0) === -1, '无页面 → -1');

/* ── buildOverviewHtml ──────────────────────────────────── */
const html = RL.buildOverviewHtml(777, 'c1', [1, 2, 3], 1);
ok((html.match(/reader__grid__cell/g) || []).length === 3, '览图 HTML：每页一个格子');
ok(html.indexOf('data-idx="1"') !== -1 && /data-idx="1"/.test(html.split('is-cur')[0] + '') !== null,
  '览图 HTML：含 data-idx');
ok(/\bis-cur\b/.test(html), '览图 HTML：当前页高亮');
ok(html.indexOf('/api/comic/777/page/2?chapter=c1') !== -1, '览图 HTML：图片源带章节参数');
ok((html.match(/is-cur/g) || []).length === 1, '览图 HTML：仅一个高亮');

/* ── columnIndex（连排模式当前页推算）───────────────────── */
ok(RL.columnIndex(0, 600, [0, 500, 1200], [500, 700, 800]) === 0, '连排：顶部 → 第 0 页');
ok(RL.columnIndex(600, 600, [0, 500, 1200], [500, 700, 800]) === 1, '连排：滚到第 2 页内 → 第 1 页（0基）');
ok(RL.columnIndex(2000, 600, [0, 500, 1200], [500, 700, 800]) === 2, '连排：超出末页 → 夹到最后一页');
ok(RL.columnIndex(100, 600, [0, 500, 1200], [0, 700, 800]) === 0, '连排：当前页未加载(高0) → 回退最近已加载页');
ok(RL.columnIndex(0, 600, [], []) === -1, '连排：无页面 → -1');
ok(RL.columnIndex(0, 0, [0, 500], [400, 600]) === 0, '连排：视口高 0 → 仍按 80px 焦点线推算');

if (failed) {
  console.log('失败 ' + failed + ' 项');
  process.exit(1);
}
console.log('reader-logic 单测全部通过');
