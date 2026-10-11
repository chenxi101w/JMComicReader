/**
 * 阅读器纯逻辑模块（无 DOM 依赖，可被 node 直接单测）。
 *
 * 从 app.js 的 initReaderPage（曾 481 行）抽出的三块判定/生成逻辑——
 * 它们今天被反复修改过（滚轮翻页/滑轨/览图），抽出来是为了能被
 * tools/selftest_lite.py 用 node 真正跑到，而不是只做符号存在性检查。
 */
(function (global) {
  'use strict';

  /** 滚轮累计阈值（deltaY），防触控板小步误触 */
  var WHEEL_ACC_THRESHOLD = 40;
  /** 翻页冷却（ms），防一次滚动连翻多页 */
  var WHEEL_FLIP_COOLDOWN = 240;

  /**
   * 滚轮动作判定。
   * @param {{deltaY:number, buttons:number}} ev 滚轮事件取值
   * @param {{mode:string, atTop:boolean, atBottom:boolean,
   *          acc:number, lockUntil:number, now:number}} ctx
   * @returns {{action:string, dir?:number, acc:number, lockUntil:number}}
   *   action: 'zoom' 右键+滚轮或以光标缩放模式
   *         | 'scroll' 原生滚动（scroll 模式）
   *         | 'scrollPage' 页内滑动（dir = ±1）
   *         | 'flip' 翻页（dir = ±1）
   *         | 'idle' 累计中或冷却中
   */
  function resolveWheelAction(ev, ctx) {
    if (ev.buttons & 2) return { action: 'zoom', acc: ctx.acc, lockUntil: ctx.lockUntil };
    if (ctx.mode === 'scroll') return { action: 'scroll', acc: ctx.acc, lockUntil: ctx.lockUntil };
    if (ctx.mode !== 'page') return { action: 'zoom', acc: ctx.acc, lockUntil: ctx.lockUntil };
    if (ctx.now < ctx.lockUntil) return { action: 'idle', acc: ctx.acc, lockUntil: ctx.lockUntil };
    var acc = ctx.acc + ev.deltaY;
    if (Math.abs(acc) < WHEEL_ACC_THRESHOLD) {
      return { action: 'idle', acc: acc, lockUntil: ctx.lockUntil };
    }
    var dir = acc > 0 ? 1 : -1;
    var lockUntil = ctx.now + WHEEL_FLIP_COOLDOWN;
    if (dir > 0 && !ctx.atBottom) return { action: 'scrollPage', dir: 1, acc: 0, lockUntil: lockUntil };
    if (dir < 0 && !ctx.atTop) return { action: 'scrollPage', dir: -1, acc: 0, lockUntil: lockUntil };
    return { action: 'flip', dir: dir, acc: 0, lockUntil: lockUntil };
  }

  /**
   * 进度滑轨：由点击/拖动的 Y 坐标算目标页索引（0 基）。
   * @returns {number} 越界会被夹到 [0, pageCount-1]；pageCount<=0 或轨道高为 0 → -1
   */
  function railIndex(clientY, railTop, railHeight, pageCount) {
    if (!railHeight || pageCount <= 0) return -1;
    var frac = Math.min(1, Math.max(0, (clientY - railTop) / railHeight));
    return Math.round(frac * (pageCount - 1));
  }

  /**
   * 缩略图览网格 HTML（当前页高亮 is-cur）。
   * @param {number} jmId
   * @param {string} chapterId
   * @param {Array} pages 页号数组
   * @param {number} currentIdx
   */
  function buildOverviewHtml(jmId, chapterId, pages, currentIdx) {
    return pages.map(function (p, i) {
      return '<div class="reader__grid__cell' + (i === currentIdx ? ' is-cur' : '') +
        '" data-idx="' + i + '">' +
        '<img loading="lazy" src="/api/comic/' + jmId + '/page/' + p +
          '?chapter=' + encodeURIComponent(chapterId) + '" alt="第 ' + (i + 1) + ' 页">' +
        '<span>' + (i + 1) + '</span></div>';
    }).join('');
  }

  /**
   * 连排模式：由滚动位置推当前页（0 基）。
   * 取「视口顶部往下 30%（最少 80px）」落在哪一页的纵向区间内。
   * @param {number} scrollTop stage.scrollTop
   * @param {number} viewportH 视口高
   * @param {number[]} tops 每页 offsetTop（升序）
   * @param {number[]} heights 每页高（0 = 未加载占位）
   * @returns {number} 无数据返回 -1；否则夹在 [0, 页数-1]
   */
  function columnIndex(scrollTop, viewportH, tops, heights) {
    if (!tops || !tops.length) return -1;
    var focus = scrollTop + Math.max(80, (viewportH || 0) * 0.3);
    var idx = 0;
    for (var i = 0; i < tops.length; i++) {
      if (tops[i] <= focus) idx = i; else break;
    }
    if (heights && heights[idx] === 0) {
      for (var j = idx; j > 0; j--) {
        if (heights[j] > 0) { idx = j; break; }
      }
    }
    return Math.min(idx, tops.length - 1);
  }

  global.ReaderLogic = {
    WHEEL_ACC_THRESHOLD: WHEEL_ACC_THRESHOLD,
    WHEEL_FLIP_COOLDOWN: WHEEL_FLIP_COOLDOWN,
    resolveWheelAction: resolveWheelAction,
    railIndex: railIndex,
    columnIndex: columnIndex,
    buildOverviewHtml: buildOverviewHtml
  };
})(this);
