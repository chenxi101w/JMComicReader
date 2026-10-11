/* ═══════════════════════════════════════════════════════════
   书架拖拽纯逻辑（无 DOM 依赖，node 可单测）
   背景：WebView2 的 HTML5 DnD 半残（dragstart 会触发但拖不动，
   还会留僵死残影）→ 原生 DnD 一律禁用，用鼠标事件模拟拖拽，
   决策逻辑收口到本模块。
   ═══════════════════════════════════════════════════════════ */
(function (global) {
  'use strict';

  var LibraryDrag = {
    /** 起拖阈值：累计位移（px）。小于此视为普通点击 */
    THRESHOLD: 6,

    /** 是否超过起拖阈值 */
    shouldBegin: function (dx, dy) {
      return Math.abs(dx) + Math.abs(dy) >= this.THRESHOLD;
    },

    /**
     * 由拖拽源解析载荷。
     * @param {{isCard:boolean, jmId:string, catId:string}} srcInfo
     *   isCard=漫画卡片；否则看 catId（分类行；虚拟行无 catId 返回 null）
     * @returns {null|{type:'comic'|'cat', payload:string, dragCatId:number|null}}
     */
    resolvePayload: function (srcInfo) {
      if (!srcInfo) return null;
      if (srcInfo.isCard && srcInfo.jmId) {
        return { type: 'comic', payload: String(srcInfo.jmId), dragCatId: null };
      }
      if (!srcInfo.isCard && srcInfo.catId) {
        var cid = parseInt(srcInfo.catId, 10);
        if (!cid) return null;
        return { type: 'cat', payload: 'cat:' + cid, dragCatId: cid };
      }
      return null;
    },

    /**
     * 悬停目标行的高亮态。
     * @param {number|null} dragCatId 正在拖的分类 id（漫画拖拽传 null）
     * @param {number} tid 目标分类 id（虚拟行/顶层传 0）
     * @param {function(number,number):boolean} isDescendant (dragCatId, tid) => tid 是否为 dragCatId 的子孙
     * @returns {'over'|'invalid'}
     */
    targetState: function (dragCatId, tid, isDescendant) {
      if (dragCatId != null && tid && (tid === dragCatId || isDescendant(dragCatId, tid))) {
        return 'invalid';
      }
      return 'over';
    },

    /**
     * 是否应执行落点动作（mouseup 时判定）。
     * @param {boolean} started 是否已起拖
     * @param {number} clientX/clientY 落点坐标（用于 elementFromPoint 由调用方处理）
     */
    shouldDrop: function (started) {
      return !!started;
    }
  };

  global.LibraryDrag = LibraryDrag;
})(typeof globalThis !== 'undefined' ? globalThis : this);
