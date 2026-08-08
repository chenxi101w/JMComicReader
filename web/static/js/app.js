/* ============================================================
   JM Reader — 全局逻辑中枢
   主题 · 导航 · Toast/Modal · 分类树 · 下载调度 · 页面路由
   ============================================================ */

/* ── 主题 ──────────────────────────────────────────────── */
const Theme = {
  get() { return document.documentElement.getAttribute('data-theme') || 'light'; },
  set(t) {
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem('jm_theme', t); } catch (e) {}
    document.querySelectorAll('.theme-toggle i').forEach(icon => {
      icon.className = t === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
    });
  },
  sync(el) {
    const icon = el && el.querySelector('i');
    if (icon) icon.className = this.get() === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
  },
  toggle() { this.set(this.get() === 'dark' ? 'light' : 'dark'); },
  save() {
    api('POST', '/api/settings', { theme: this.get() }).then(r => {
      if (r.success) setAppConfig('theme', this.get());
    });
  }
};

/* ── 列表型本地存储工厂（收藏标签 / 作者 / 常用标签 / 历史 共用）──
   消除原先每个 store 重复书写 list/save/add/remove 的样板代码。 */
function createListStore(KEY, cap = 50, opts = {}) {
  const prepend = opts.prepend !== false; // 默认新项插到最前；QuickTags 用 append
  return {
    KEY,
    list() { try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (_) { return []; } },
    save(arr) { try { localStorage.setItem(KEY, JSON.stringify(arr.slice(0, cap))); } catch (_) {} },
    add(v) { v = (v || '').trim(); if (!v) return; const a = this.list(); if (!a.includes(v)) { prepend ? a.unshift(v) : a.push(v); this.save(a); } },
    remove(v) { this.save(this.list().filter(x => x !== v)); },
    contains(v) { return this.list().includes(v); },
    toggle(v) { this.contains(v) ? this.remove(v) : this.add(v); },
    move(from, to) { const a = this.list(); const [x] = a.splice(from, 1); if (x !== undefined) { a.splice(to, 0, x); this.save(a); } }
  };
}

/* ── 收藏标签（全局，搜索页 + 详情页共用）────────────────── */
const FavTags = createListStore('jm_fav_tags', 50);

/* ── 收藏作者（全局）──────────────────────────────────────── */
const FavAuthors = createListStore('jm_fav_authors', 50);

/* ── 搜索历史（全局，下拉与偏好库共用，提升到模块级便于统一管理）── */
const SearchHistory = createListStore('jm_search_history', 10);

/* ── 搜索偏好持久化（排序 / 时间 / 方向）──────────────────── */
const SearchPrefs = {
  KEY: 'jm_search_prefs',
  get() { try { return JSON.parse(localStorage.getItem(this.KEY) || '{}'); } catch (_) { return {}; } },
  set(p) { try { localStorage.setItem(this.KEY, JSON.stringify({ ...this.get(), ...p })); } catch (_) {} }
};

/* ── 前端本地设置（阅读器预加载、标签高亮等，无需后端重建）── */
const Settings = {
  get(key, def) {
    try { const v = localStorage.getItem('jm_setting_' + key); return v === null ? def : JSON.parse(v); }
    catch (_) { return def; }
  },
  set(key, val) { try { localStorage.setItem('jm_setting_' + key, JSON.stringify(val)); } catch (_) {} }
};

/* ── 搜索偏好“库”：历史 / 收藏标签 / 收藏作者 的统一管理入口 ──
   关键能力：误删可恢复（删除只移入回收站，库面板可一键恢复）。
   下拉面板的删除也走这里，保证只有一条删除代码路径。 */
const PrefLibrary = (() => {
  const cats = {
    history: { store: SearchHistory, label: '历史' },
    tags:    { store: FavTags,       label: '标签' },
    authors: { store: FavAuthors,    label: '作者' },
  };
  const trashKey = (c) => 'jm_lib_trash_' + c;
  const getTrash = (c) => { try { return JSON.parse(localStorage.getItem(trashKey(c)) || '[]'); } catch (_) { return []; } };
  const setTrash = (c, a) => { try { localStorage.setItem(trashKey(c), JSON.stringify(a.slice(0, 50))); } catch (_) {} };
  return {
    cats,
    list(c) { return cats[c].store.list(); },
    add(c, v) { if (v && v.trim()) cats[c].store.add(v.trim()); },
    edit(c, oldV, newV) {
      newV = (newV || '').trim(); if (!newV || !oldV) return false;
      const a = cats[c].store.list(); const i = a.indexOf(oldV);
      if (i < 0) return false;
      if (a.includes(newV)) a.splice(i, 1); else a[i] = newV;
      cats[c].store.save(a); return true;
    },
    // 可恢复删除：从库移除并存入回收站
    remove(c, v) {
      if (!cats[c].store.list().includes(v)) return false;
      cats[c].store.remove(v);
      const t = getTrash(c).filter(x => x !== v); t.unshift(v); setTrash(c, t);
      return true;
    },
    restore(c, v) {
      const t = getTrash(c); if (!t.includes(v)) return false;
      setTrash(c, t.filter(x => x !== v));
      cats[c].store.add(v); return true;
    },
    trash(c) { return getTrash(c); },
    clearTrash(c) { setTrash(c, []); }
  };
})();

/* ── 搜索偏好“库”面板：历史 / 标签 / 作者 的统一管理（误删可恢复）──
   放在模块级，供设置页的「搜索偏好库」钻取面板调用。
   依赖：PrefLibrary（数据 + 回收站）、toastUndo（撤销提示）、escapeHtml。 */
function initPrefLib() {
  const toggle = document.getElementById('prefLibToggle');
  const body = document.getElementById('prefLibBody');
  if (!body) return;
  function render() {
    body.querySelectorAll('.pref-lib-block').forEach(block => {
      const cat = block.dataset.cat;
      const listEl = block.querySelector('[data-list]');
      const trashEl = block.querySelector('[data-trash]');
      const arr = PrefLibrary.list(cat);
      listEl.innerHTML = arr.length
        ? arr.map(v => `<span class="sp-chip"><span class="sp-chip__name">${escapeHtml(v)}</span><button class="sp-chip__edit" data-v="${escapeHtml(v)}" title="编辑"><i class="fas fa-pen"></i></button><button class="sp-chip__del" data-v="${escapeHtml(v)}" title="删除"><i class="fas fa-times"></i></button></span>`).join('')
        : `<div class="manage-empty">暂无项目</div>`;
      listEl.querySelectorAll('.sp-chip__del').forEach(b => b.onclick = () => {
        const v = b.dataset.v;
        PrefLibrary.remove(cat, v);
        render();
        toastUndo(`已删除「${v}」，点此恢复`, () => { PrefLibrary.restore(cat, v); render(); });
      });
      listEl.querySelectorAll('.sp-chip__edit').forEach(b => b.onclick = () => {
        const oldV = b.dataset.v;
        const newV = prompt('修改：', oldV);
        if (newV == null) return;
        if (!PrefLibrary.edit(cat, oldV, newV.trim())) { toast('修改失败或内容已存在', 'warn'); return; }
        render();
      });
      const trash = PrefLibrary.trash(cat);
      if (trash.length) {
        trashEl.hidden = false;
        trashEl.innerHTML = `<div class="pref-lib-trash__head">已删除（可恢复）<button class="sp-clear-trash" type="button" title="清空回收站">清空</button></div>` +
          trash.map(v => `<span class="sp-chip sp-chip--trash"><span class="sp-chip__name">${escapeHtml(v)}</span><button class="sp-restore" data-v="${escapeHtml(v)}" title="恢复"><i class="fas fa-rotate-left"></i></button></span>`).join('');
        trashEl.querySelector('.sp-clear-trash').onclick = () => { PrefLibrary.clearTrash(cat); render(); };
        trashEl.querySelectorAll('.sp-restore').forEach(b => b.onclick = () => { PrefLibrary.restore(cat, b.dataset.v); render(); });
      } else {
        trashEl.hidden = true; trashEl.innerHTML = '';
      }
    });
  }
  if (toggle) {
    const doToggle = () => {
      body.hidden = !body.hidden;
      toggle.classList.toggle('open', !body.hidden);
      if (!body.hidden) render();
    };
    toggle.addEventListener('click', doToggle);
    toggle.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); doToggle(); } });
  }
  body.querySelectorAll('[data-add-btn]').forEach(btn => {
    const block = btn.closest('.pref-lib-block');
    const cat = block.dataset.cat;
    const input = block.querySelector('[data-add-input]');
    const add = () => {
      const v = input.value.trim();
      if (!v) { toast('请输入内容', 'warn'); return; }
      if (PrefLibrary.list(cat).includes(v)) { toast('已存在', 'warn'); return; }
      PrefLibrary.add(cat, v); input.value = ''; render();
    };
    btn.onclick = add;
    input.addEventListener('keydown', e => { if (e.key === 'Enter') add(); });
  });
}

/* ── 常用标签（钉在搜索栏右侧的快捷标签，支持拖拽排序）────────
   与收藏标签不同：新项追加到末尾（prepend:false）。 */
const QuickTags = createListStore('jm_quick_tags', 15, { prepend: false });

/* ── API ───────────────────────────────────────────────── */
async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(url, opt);
  const data = await res.json().catch(() => ({}));
  return data;
}

/* ── 应用配置缓存（设置项在全局可用）──────────────────────── */
let appConfigCache = null;
async function loadAppConfig(force = false) {
  if (appConfigCache && !force) return appConfigCache;
  const r = await api('GET', '/api/settings');
  appConfigCache = r.success ? (r.data || {}) : {};
  return appConfigCache;
}
function setAppConfig(key, value) {
  if (!appConfigCache) appConfigCache = {};
  appConfigCache[key] = value;
}

/* ── Toast ─────────────────────────────────────────────── */
function toast(msg, type = 'info', ms = 2600) {
  const wrap = document.getElementById('toastWrap');
  if (!wrap) return;
  const el = document.createElement('div');
  el.className = `toast toast--${type}`;
  const icon = type === 'success' ? 'fa-check-circle' : type === 'error' ? 'fa-circle-exclamation'
    : type === 'warn' ? 'fa-triangle-exclamation' : 'fa-circle-info';
  el.innerHTML = `<i class="fas ${icon}"></i><span></span>`;
  el.querySelector('span').textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateY(10px)'; }, ms - 300);
  setTimeout(() => el.remove(), ms);
}
function toastClosable(msg, type = 'info', ms = 3000) {
  const wrap = document.getElementById('toastWrap');
  if (!wrap) return;
  const el = document.createElement('div');
  el.className = `toast toast--${type} toast--closable`;
  const icon = type === 'success' ? 'fa-check-circle' : type === 'error' ? 'fa-circle-exclamation'
    : type === 'warn' ? 'fa-triangle-exclamation' : 'fa-circle-info';
  el.innerHTML = `<i class="fas ${icon}"></i><span></span><button class="toast__close" title="关闭"><i class="fas fa-times"></i></button>`;
  el.querySelector('span').textContent = msg;
  const t = setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateY(10px)'; }, ms - 300);
  const t2 = setTimeout(() => { if (el.parentNode) el.remove(); }, ms);
  el.querySelector('.toast__close').onclick = () => { clearTimeout(t); clearTimeout(t2); el.remove(); };
  wrap.appendChild(el);
}
// 带“撤销”按钮的提示，用于可恢复的删除操作
function toastUndo(msg, onUndo, ms = 4500) {
  const wrap = document.getElementById('toastWrap');
  if (!wrap) { if (onUndo) onUndo(); return; }
  const el = document.createElement('div');
  el.className = 'toast toast--info toast--undo';
  el.innerHTML = `<i class="fas fa-rotate-left"></i><span></span><button class="toast__undo" type="button">撤销</button>`;
  el.querySelector('span').textContent = msg;
  let done = false;
  const finish = () => { if (el.parentNode) el.remove(); };
  const t = setTimeout(() => { el.style.opacity = '0'; setTimeout(finish, 300); }, ms);
  el.querySelector('.toast__undo').onclick = () => {
    if (done) return; done = true; clearTimeout(t);
    if (onUndo) onUndo(); finish();
  };
  wrap.appendChild(el);
}

/* ── Modal ─────────────────────────────────────────────── */
function openModal({ title, sub, bodyHtml, actions = [], center = false, grip = true }) {
  const root = document.getElementById('modalRoot');
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal-panel ${center ? 'modal-panel--center' : ''}">
      ${grip && !center ? '<div class="modal-grip"></div>' : ''}
      <h3 class="modal-title">${title}</h3>
      ${sub ? `<p class="modal-sub">${sub}</p>` : ''}
      <div class="modal-body">${bodyHtml}</div>
      <div class="modal-actions">${actions.map(a => `<button class="btn ${a.cls || ''}" data-act="${a.key}">${a.label}</button>`).join('')}</div>
    </div>`;
  root.appendChild(overlay);
  const panel = overlay.querySelector('.modal-panel');
  overlay.addEventListener('click', e => { if (e.target === overlay) closeModal(); });
  const close = () => overlay.remove();
  overlay.querySelector('.modal-actions').addEventListener('click', e => {
    const b = e.target.closest('[data-act]'); if (!b) return;
    const act = actions.find(a => a.key === b.dataset.act);
    if (act && act.onClick) act.onClick(close, overlay);
  });
  return { close, overlay, panel };
}
function closeModal() {
  const m = document.querySelector('.modal-overlay');
  if (m) m.remove();
}

/* ── 波纹反馈 ──────────────────────────────────────────── */
function attachRipple(root = document) {
  root.querySelectorAll('.btn, .nav-item, .quick-tag, .cat-item').forEach(el => {
    if (el.dataset.rippled) return;
    el.dataset.rippled = '1';
    el.addEventListener('click', e => {
      const r = el.getBoundingClientRect();
      const size = Math.max(r.width, r.height);
      const s = document.createElement('span');
      s.className = 'ripple';
      s.style.width = s.style.height = size + 'px';
      s.style.left = (e.clientX - r.left - size / 2) + 'px';
      s.style.top = (e.clientY - r.top - size / 2) + 'px';
      el.appendChild(s);
      setTimeout(() => s.remove(), 600);
    });
  });
}

/* ── 分类树选择器（支持新建父/子/层级分类） ─────────────── */
async function pickCategories(preset = [], onConfirm) {
  let tree = await api('GET', '/api/categories?tree=1');
  tree = tree.data || [];
  const selected = new Set(preset);

  function flattenWithParent(nodes, parentId = null) {
    let out = [];
    for (const n of nodes) {
      out.push({ id: n.id, name: n.name, parent_id: parentId });
      if (n.children && n.children.length) out = out.concat(flattenWithParent(n.children, n.id));
    }
    return out;
  }

  function renderNodes(nodes, depth = 0) {
    return nodes.map(n => {
      const hasChildren = n.children && n.children.length;
      const checked = selected.has(n.id) ? 'checked' : '';
      const childHtml = hasChildren ? `<div class="tree-children">${renderNodes(n.children, depth + 1)}</div>` : '';
      return `<div class="tree-node" style="padding-left:${depth * 14}px">
        <label class="tree-item">
          <input type="checkbox" value="${n.id}" ${checked} class="cat-cb" style="accent-color:var(--accent)">
          <i class="fas fa-folder tree-item__icon"></i>
          <span class="tree-item__name">${escapeHtml(n.name)}</span>
          ${n.comic_count != null ? `<span class="tree-item__count">${n.comic_count}</span>` : ''}
        </label>
        <button class="tree-add-child" data-add="${n.id}" title="在此分类下新建子分类"><i class="fas fa-plus"></i></button>
      </div>${childHtml}`;
    }).join('');
  }

  // 逐层创建/复用路径（A/B/C），返回叶子 id；中间层级已存在则复用
  async function createPath(raw) {
    const parts = raw.split('/').map(s => s.trim()).filter(Boolean);
    if (!parts.length) return null;
    let parentId = null, leafId = null;
    for (const p of parts) {
      const flat = flattenWithParent(tree);
      let found = null;
      for (const node of flat) { if (node.name === p && node.parent_id === parentId) { found = node; break; } }
      if (found) { leafId = found.id; parentId = found.id; continue; }
      const r = await api('POST', '/api/categories', { name: p, parent_id: parentId });
      if (!r.success || !r.data) { toast('创建分类失败：' + (r.message || ''), 'error'); return null; }
      leafId = r.data.id; parentId = leafId;
      const tt = await api('GET', '/api/categories?tree=1');
      tree = tt.data || tree;
    }
    return leafId;
  }

  const { close, overlay: ov } = openModal({
    title: '选择分类',
    sub: '可多选；点 + 在所选分类下建子分类；名称用 / 分隔可一次建“父/子”层级',
    bodyHtml: `<div class="tree" id="pickTree">${renderNodes(tree)}</div>
      <div style="margin-top:12px"><input class="batch-textarea" id="newCatName" placeholder="或输入新分类名（支持 A/B/C 层级），确认时自动创建" style="min-height:40px"></div>`,
    actions: [
      { key: 'cancel', label: '取消', cls: 'btn--ghost', onClick: c => c() },
      { key: 'ok', label: '确定', cls: 'btn--primary', onClick: async (c, ov) => {
        const checks = [...ov.querySelectorAll('.cat-cb:checked')].map(cb => parseInt(cb.value));
        const newName = ov.querySelector('#newCatName').value.trim();
        if (newName) {
          const lid = await createPath(newName);
          if (lid != null) checks.push(lid);
        }
        c();
        onConfirm(checks);
      }}
    ]
  });

  function rerender() {
    const el = ov.querySelector('#pickTree');
    if (!el) return;
    el.innerHTML = renderNodes(tree);
    el.querySelectorAll('.tree-add-child').forEach(b => {
      b.onclick = async () => {
        const name = prompt('在该分类下新建子分类名称：');
        if (!name || !name.trim()) return;
        const r = await api('POST', '/api/categories', { name: name.trim(), parent_id: parseInt(b.dataset.add) });
        if (r.success && r.data) {
          const tt = await api('GET', '/api/categories?tree=1');
          tree = tt.data || tree;
          selected.add(r.data.id);
          rerender();
          toast('已创建子分类', 'success');
        } else { toast('创建失败', 'error'); }
      };
    });
  }
  rerender();
}

/* ── 下载调度 ─────────────────────────────────────────── */
function monitorProgress(downloadId, label) {
  const tick = async () => {
    const r = await api('GET', `/api/download/progress/${downloadId}`);
    if (!r.success) return;
    const d = r.data;
    if (d.status === 'completed' || d.status === 'downloaded') {
      toast(`${label} 下载完成`, 'success');
    } else if (d.status === 'error') {
      toast(`${label} 失败：${d.message || ''}`, 'error');
    } else if (d.status === 'starting' || d.status === 'downloading') {
      if (d.progress != null && d.progress < 100) {
        // 轻量提示，不刷屏
      }
      setTimeout(tick, 1500);
    }
  };
  setTimeout(tick, 800);
}

async function downloadComic(jmId, cats = [], label) {
  label = label || `JM-${jmId}`;
  const r = await api('POST', `/api/download/${jmId}`, { category_ids: cats });
  if (r.success) {
    toast(`${label} 开始下载，可在「下载」页查看进度`, 'info');
    if (r.download_id) monitorProgress(r.download_id, label);
  } else {
    if (r.downloaded) toast('该漫画已下载', 'warn');
    else toast(r.message || '下载失败', 'error');
  }
  return r;
}

async function batchDownload(ids, cats = []) {
  if (!ids.length) { toast('请先输入漫画 ID', 'warn'); return; }
  const r = await api('POST', '/api/download/batch', { ids, category_ids: cats });
  if (r.success) {
    const started = (r.data || []).filter(d => d.status === 'downloading').length;
    const skipped = (r.data || []).filter(d => d.status === 'skipped').length;
    toast(`已启动 ${started} 本，跳过 ${skipped} 本`, 'success');
    (r.data || []).forEach(d => { if (d.download_id) monitorProgress(d.download_id, `JM-${d.jm_id}`); });
  } else {
    toast(r.message || '批量下载失败', 'error');
  }
}

/* ── 批量下载入口（搜索页 / 下载管理页共用）── */
function initBatchBar() {
  const inputs = document.getElementById('batchInputs');
  const cnt = document.getElementById('batchCount');
  if (!inputs) return;
  function collectIds() {
    return [...new Set([...inputs.querySelectorAll('input')].map(i => i.value.trim()).filter(s => /^\d+$/.test(s)).map(Number))];
  }
  function syncCount() {
    if (cnt) cnt.textContent = `${collectIds().length} 个`;
  }
  function addBox(focus = true) {
    const row = document.createElement('div');
    row.className = 'batch-row';
    row.innerHTML = `<input type="text" inputmode="numeric" placeholder="JM号" maxlength="10">
      <button class="rm" title="移除"><i class="fas fa-times"></i></button>`;
    const inp = row.querySelector('input');
    const rm = row.querySelector('.rm');
    rm.onclick = () => { row.remove(); syncCount(); };
    inp.addEventListener('input', () => {
      syncCount();
      const allRows = inputs.querySelectorAll('.batch-row');
      if (inp.value.trim() && row === allRows[allRows.length - 1]) addBox(false);
    });
    inputs.appendChild(row);
    if (focus) inp.focus();
    syncCount();
  }
  inputs.innerHTML = '';
  addBox(false); addBox(false);
  syncCount();
  const addB = document.getElementById('addBatchBox');
  const clearB = document.getElementById('clearBatch');
  const dlB = document.getElementById('batchDownloadBtn');
  if (addB) addB.onclick = () => addBox(true);
  if (clearB) clearB.onclick = () => { inputs.innerHTML = ''; addBox(false); addBox(false); syncCount(); };
  if (dlB) dlB.onclick = async () => {
    const ids = collectIds();
    if (!ids.length) { toast('请先输入漫画 ID', 'warn'); return; }
    pickCategories([], (cats) => batchDownload(ids, cats));
  };
}

/* ── 工具 ─────────────────────────────────────────────── */
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, m => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]
  ));
}
function parseIds(text) {
  return [...new Set(
    String(text || '').split(/[\s,，]+/).map(s => s.trim()).filter(s => /^\d+$/.test(s)).map(Number)
  )];
}
function renderPageChapter(pages, chapterCount) {
  if (document.body.classList.contains('hide-page-chapter')) return '';
  const p = pages > 0 ? `${pages} 页` : '';
  const ch = chapterCount > 0 ? `${chapterCount} 章` : '';
  const text = [p, ch].filter(Boolean).join(' · ');
  return text ? `<div class="comic-card__meta">${escapeHtml(text)}</div>` : '';
}

/* ── 拉黑 / 别名 辅助 ─────────────────────────────────────── */
function showBlockHits(kind, name, hits) {
  const arr = hits || [];
  const list = arr.slice(0, 25)
    .map(h => `<li>${escapeHtml(h.title || ('JM-' + h.jm_id))}</li>`).join('')
    + (arr.length > 25 ? `<li>…等 ${arr.length} 部</li>` : '');
  openModal({
    title: '本地命中提示',
    sub: `已拉黑${kind}「${escapeHtml(name)}」`,
    bodyHtml: arr.length
      ? `<p class="muted">本地书架中有 <b>${arr.length}</b> 部命中作品（已灰显）：</p><ul class="hit-list">${list}</ul>`
      : `<p class="muted">本地书架中没有命中作品。</p>`,
    actions: [{ key: 'ok', label: '知道了', cls: 'btn--primary', onClick: c => c() }],
  });
}
async function blockWork(jmId, title) {
  const r = await api('POST', '/api/blocklist', { type: 'work', value: String(jmId) });
  if (!r.success) { toast(r.message || '操作失败', 'error'); return; }
  toast(`已拉黑《${title || jmId}》`, 'success');
  document.querySelectorAll(`.comic-card[data-jm-id="${jmId}"]`).forEach(c => c.remove());
  const cfg = await loadAppConfig();
  if (cfg.show_block_hits !== '0') showBlockHits('作品', title || jmId, r.local_hits);
}
async function blockAuthor(author) {
  if (!author) return;
  const r = await api('POST', '/api/blocklist', { type: 'author', value: author });
  if (!r.success) { toast(r.message || '操作失败', 'error'); return; }
  toast(`已拉黑作者「${author}」`, 'success');
  document.querySelectorAll('.comic-card').forEach(c => { if (c.dataset.author === author) c.remove(); });
  const cfg = await loadAppConfig();
  if (cfg.show_block_hits !== '0') showBlockHits('作者', author, r.local_hits);
}
async function blockTag(tag) {
  if (!tag) return;
  const r = await api('POST', '/api/blocklist', { type: 'tag', value: tag });
  if (!r.success) { toast(r.message || '操作失败', 'error'); return; }
  toast(`已拉黑标签「${tag}」`, 'success');
  // 当前结果中命中该标签的卡片移除（与作者拉黑一致）
  document.querySelectorAll('.comic-card').forEach(card => {
    const tags = (card.dataset.tags || '').split(',').filter(Boolean);
    if (tags.includes(tag)) card.remove();
  });
  const cfg = await loadAppConfig();
  if (cfg.show_block_hits !== '0') showBlockHits('标签', tag, r.local_hits);
}
function showTagContextMenu(x, y, tag, el) {
  const old = document.getElementById('tagContextMenu');
  if (old) old.remove();
  const fav = FavTags.list().includes(tag);
  const menu = document.createElement('div');
  menu.id = 'tagContextMenu';
  menu.className = 'tag-context-menu';
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  menu.innerHTML = `
    <div class="tag-context-item" data-act="fav"><i class="fas fa-star"></i> ${fav ? '取消收藏' : '收藏'}「${escapeHtml(tag)}」</div>
    <div class="tag-context-item tag-context-item--danger" data-act="block"><i class="fas fa-ban"></i> 屏蔽此标签</div>
  `;
  document.body.appendChild(menu);
  const close = () => menu.remove();
  setTimeout(() => document.addEventListener('click', function handler(e) {
    if (!menu.contains(e.target)) { close(); document.removeEventListener('click', handler); }
  }), 0);
  menu.addEventListener('click', (e) => {
    const item = e.target.closest('[data-act]');
    if (!item) return;
    const act = item.dataset.act;
    if (act === 'fav') {
      if (fav) { FavTags.remove(tag); if (el) el.classList.remove('on'); }
      else { FavTags.add(tag); if (el) el.classList.add('on'); }
      toast(fav ? '已取消收藏' : '已收藏标签', 'success');
    } else if (act === 'block') {
      blockTag(tag);
    }
    close();
  });
}
async function unblockWork(jmId) {
  const r = await api('DELETE', '/api/blocklist/by-value', { type: 'work', value: String(jmId) });
  if (r.success) toast('已取消拉黑', 'success'); else toast(r.message || '取消失败', 'error');
}
async function unblockAuthor(author) {
  const r = await api('DELETE', '/api/blocklist/by-value', { type: 'author', value: author });
  if (r.success) toast('已取消拉黑', 'success'); else toast(r.message || '取消失败', 'error');
}
async function unblockComic(jmId, author) {
  const r = await api('GET', `/api/blocklist/affects?jm_id=${jmId}&author=${encodeURIComponent(author || '')}`);
  if (!r.success || !r.data || !r.data.length) { toast('未在黑名单中找到', 'warn'); return; }
  for (const id of r.data) await api('DELETE', `/api/blocklist/${id}`);
  toast('已取消拉黑', 'success');
}


/* ── 导航高亮 ─────────────────────────────────────────── */
function setupNav() {
  const p = location.pathname;
  const cur = p.startsWith('/home') ? 'home'
    : p.startsWith('/library') ? 'library'
    : p.startsWith('/settings') ? 'settings'
    : p.startsWith('/downloads') ? 'downloads'
    : p.startsWith('/reader') ? 'library'
    : 'search';
  document.querySelectorAll('.nav-item').forEach(n => {
    n.classList.toggle('nav-item--active', n.dataset.route === cur);
  });
}

/* ── 封面懒加载 ───────────────────────────────────────── */
function setupLazyCovers(root = document) {
  const imgs = root.querySelectorAll('img[data-cover]');
  if (!('IntersectionObserver' in window) || !imgs.length) {
    imgs.forEach(loadCover); return;
  }
  const io = new IntersectionObserver((ents) => {
    ents.forEach(e => { if (e.isIntersecting) { loadCover(e.target); io.unobserve(e.target); } });
  }, { rootMargin: '200px' });
  imgs.forEach(i => io.observe(i));
}
async function loadCover(img) {
  const jmId = img.dataset.cover;
  try {
    const r = await api('GET', `/api/cover/${jmId}`);
    if (r.success && r.data && r.data.cover) {
      img.src = r.data.cover;
      img.onerror = () => { img.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='3' height='4'%3E%3C/svg%3E"; };
    }
  } catch (e) {}
}

/* ═══════════════════════════════════════════════════════
   搜索页
   ═══════════════════════════════════════════════════════ */
/* 搜索结果卡片标签渲染：按 search_tag_limit 限制显示数量（0=全部） */
function cardTagsHtml(tags, selectedTags = null, favs = null) {
  if (!tags || !tags.length) return '';
  let shown = tags, extra = 0;
  let limit = 0;
  if (appConfigCache && appConfigCache.search_tag_limit !== undefined) {
    limit = parseInt(appConfigCache.search_tag_limit, 10) || 0;
  }
  if (limit > 0 && tags.length > limit) {
    shown = tags.slice(0, limit);
    extra = tags.length - limit;
  }
  if (favs === null) favs = FavTags.list();   // 调用方可传入已计算好的收藏集，避免逐卡解析
  const mode = Settings.get('fav_tag_highlight', 'same'); // same / dim / off
  let html = shown.map(t => {
    const isSel = selectedTags && selectedTags.has(t);
    const isFav = favs.includes(t);
    let cls = 'comic-card__tag';
    if (isSel) cls += ' on';
    else if (isFav && mode === 'same') cls += ' on';
    else if (isFav && mode === 'dim') cls += ' fav-dim';
    return `<span class="${cls}" data-tag="${escapeHtml(t)}">${escapeHtml(t)}</span>`;
  }).join('');
  if (extra > 0) html += `<span class="comic-card__tag comic-card__tag--more" title="还有 ${extra} 个标签">+${extra}</span>`;
  return html;
}

/* 漫画卡片渲染（模块级，搜索页 / 主页推荐 / 屏蔽预览 共用）
   opts.onAuthorClick(author)：点击作者名时的回调；不传则仅展示不可点。 */
function renderCards(list, append = true, target = null, opts = {}) {
  const grid = target || document.getElementById('comicGrid');
  if (!grid) return;
  if (!append) grid.innerHTML = '';
  const favTagsSet = FavTags.list();
  const favAuthorsSet = FavAuthors.list();
  const onAuthorClick = opts.onAuthorClick || null;
  list.forEach((c, i) => {
    const id = c.id || c.album_id || c.comic_id;
    const author = c.author || '';
    const isFavAuthor = author && favAuthorsSet.includes(author);
    const card = document.createElement('div');
    card.className = 'comic-card';
    card.dataset.jmId = id;
    card.dataset.title = c.title || '';
    card.dataset.author = author;
    card.dataset.tags = (c.tags || []).join(',');
    card.dataset.pages = String(c.pages || 0);
    card.style.animationDelay = (Math.min(i, 15) * 0.03) + 's';
    card.innerHTML = `
      <img class="comic-card__cover" data-cover="${id}" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='3' height='4'%3E%3C/svg%3E">
      <div class="comic-card__body">
        <div class="comic-card__title">${escapeHtml(c.title || '未命名漫画')}</div>
        ${author ? `<div class="comic-card__meta"><span class="author-name" data-author="${escapeHtml(author)}">${escapeHtml(author)}</span></div>` : ''}
        ${renderPageChapter(c.pages, 0)}
        <div class="comic-card__tags">${cardTagsHtml(c.tags, opts.favSel || new Set(), favTagsSet)}</div>
        <div class="comic-card__actions unified">
          <button class="act act--primary" data-act="dl" title="下载"><i class="fas fa-download"></i></button>
          <button class="act act--toggle${isFavAuthor ? ' on' : ''}" data-act="fav-author" title="收藏作者"><i class="fas fa-star"></i></button>
          <button class="act act--danger" data-act="block-author" title="拉黑作者"><i class="fas fa-user-slash"></i></button>
          <button class="act act--danger" data-act="block-work" title="拉黑作品"><i class="fas fa-ban"></i></button>
        </div>
      </div>`;
    card.querySelector('[data-act="dl"]').onclick = (e) => {
      e.stopPropagation();
      pickCategories([], (cats) => downloadComic(id, cats, c.title));
    };
    card.querySelector('[data-act="fav-author"]').onclick = (e) => {
      e.stopPropagation();
      if (FavAuthors.list().includes(author)) { FavAuthors.remove(author); e.currentTarget.classList.remove('on'); }
      else { FavAuthors.add(author); e.currentTarget.classList.add('on'); }
    };
    card.querySelector('[data-act="block-author"]').onclick = (e) => {
      e.stopPropagation();
      blockAuthor(author);
    };
    card.querySelector('[data-act="block-work"]').onclick = (e) => {
      e.stopPropagation();
      blockWork(id, c.title);
    };
    card.querySelectorAll('.comic-card__tag').forEach(tg => {
      tg.addEventListener('click', (e) => {
        e.stopPropagation();
        const t = tg.dataset.tag;
        if (FavTags.list().includes(t)) { FavTags.remove(t); tg.classList.remove('on'); }
        else { FavTags.add(t); tg.classList.add('on'); }
      });
      tg.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        e.stopPropagation();
        showTagContextMenu(e.clientX, e.clientY, tg.dataset.tag, tg);
      });
    });
    const authorName = card.querySelector('.author-name');
    if (authorName && onAuthorClick) {
      authorName.addEventListener('click', (e) => {
        e.stopPropagation();
        onAuthorClick(authorName.dataset.author);
      });
      authorName.classList.add('author-name--clickable');
    }
    grid.appendChild(card);
  });
}

function initSearch() {
  const grid = document.getElementById('comicGrid');
  const input = document.getElementById('searchInput');
  const btn = document.getElementById('searchBtn');
  const loading = document.getElementById('loading');
  const empty = document.getElementById('emptyState');
  const countEl = document.getElementById('resultCount');
  const sortBySel = document.getElementById('sortBy');
  const timeSel = document.getElementById('timeRange');
  const dirSel = document.getElementById('sortDir');
  let page = 1, keyword = '', total = 0, loadingMore = false;
  const main = document.getElementById('appMain');
  const clearKwBtn = document.getElementById('clearKwBtn');
  loadAppConfig(); // 预载配置，使标签显示数量限制即时生效

  // ── 搜索偏好（排序/时间/方向）持久化 ──
  (function restoreSearchPrefs() {
    const p = SearchPrefs.get();
    if (sortBySel && p.sortBy) sortBySel.value = p.sortBy;
    if (timeSel && p.time) timeSel.value = p.time;
    if (dirSel && p.dir) dirSel.value = p.dir;
  })();
  function saveSearchPrefs() {
    SearchPrefs.set({
      sortBy: sortBySel ? sortBySel.value : 'mr',
      time: timeSel ? timeSel.value : 'a',
      dir: dirSel ? dirSel.value : 'desc'
    });
  }

  // ── 统一下拉面板 ──
  const searchDrop = document.getElementById('searchDrop');
  function showSearchDrop() {
    if (!searchDrop) return;
    renderSearchHistory();
    renderFavTags();
    renderFavAuthors();
    searchDrop.hidden = false;
    
  }
  function hideSearchDrop() {
    if (searchDrop) searchDrop.hidden = true;
  }

  // ── 搜索历史（数据来自模块级 SearchHistory）──
  function renderSearchHistory() {
    const listEl = document.getElementById('searchHistoryList');
    const emptyEl = document.getElementById('searchHistoryEmpty');
    if (!listEl) return;
    const arr = SearchHistory.list();
    if (emptyEl) emptyEl.style.display = arr.length ? 'none' : 'block';
    listEl.innerHTML = arr.map(k => `<span class="sh-chip" data-k="${escapeHtml(k)}">
        ${escapeHtml(k)}
        <button class="sh-del" data-k="${escapeHtml(k)}" title="删除"><i class="fas fa-times"></i></button>
      </span>`).join('');
    listEl.querySelectorAll('.sh-chip').forEach(it => {
      it.addEventListener('click', (e) => {
        if (e.target.closest('.sh-del')) return;
        input.value = it.dataset.k;
        hideSearchDrop();
        clearState(); doSearch(true);
      });
    });
    listEl.querySelectorAll('.sh-del').forEach(b => {
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        const k = b.dataset.k;
        PrefLibrary.remove('history', k);
        renderSearchHistory();
        toastUndo(`已删除「${k}」，点此恢复`, () => { PrefLibrary.restore('history', k); renderSearchHistory(); });
      });
    });
    const clr = document.getElementById('shClear');
    if (clr) clr.onclick = () => {
      if (!confirm('确定清空全部搜索历史？可在「搜索偏好库」恢复')) return;
      SearchHistory.list().forEach(k => PrefLibrary.remove('history', k));
      renderSearchHistory();
    };
  }

  // ── 收藏标签 / 作者：统一的芯片列表渲染（消除两套重复逻辑）──
  const favSel = new Set();
  const authorSel = new Set();
  const searchCardOpts = {
    favSel,
    onAuthorClick: (author) => {
      input.value = '';
      authorSel.clear(); favSel.clear();
      authorSel.add(author);
      hideSearchDrop();
      clearState(); doSearch(true);
    }
  };
  function renderChipList(listEl, emptyEl, arr, selSet, cls, dataAttr, delCls, cat) {
    if (!listEl) return;
    if (emptyEl) emptyEl.style.display = arr.length ? 'none' : 'block';
    listEl.innerHTML = arr.map(v => `<span class="${cls}${selSet.has(v) ? ' selected' : ''}" draggable="true" data-${dataAttr}="${escapeHtml(v)}">
        ${escapeHtml(v)}
        <button class="${delCls}" data-${dataAttr}="${escapeHtml(v)}" title="删除收藏"><i class="fas fa-times"></i></button>
      </span>`).join('');
    listEl.querySelectorAll('.' + cls).forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('.' + delCls)) return;
        const v = el.dataset[dataAttr];
        if (selSet.has(v)) selSet.delete(v); else selSet.add(v);
        el.classList.toggle('selected');
        updateQuickTagHighlight();
      });
      el.addEventListener('dragstart', e => { e.dataTransfer.setData('text/plain', el.dataset[dataAttr]); });
    });
    listEl.querySelectorAll('.' + delCls).forEach(b => {
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        const v = b.dataset[dataAttr];
        PrefLibrary.remove(cat, v);          // 可恢复删除（移入回收站）
        selSet.delete(v);
        renderChipList(listEl, emptyEl, PrefLibrary.list(cat), selSet, cls, dataAttr, delCls, cat);
        updateQuickTagHighlight();
        toastUndo(`已删除「${v}」，点此恢复`, () => {
          PrefLibrary.restore(cat, v);
          renderChipList(listEl, emptyEl, PrefLibrary.list(cat), selSet, cls, dataAttr, delCls, cat);
        });
      });
    });
  }
  function renderFavTags() {
    renderChipList(document.getElementById('favTagsList'), document.getElementById('favTagsEmpty'), FavTags.list(), favSel, 'fav-tag', 't', 'ft-del', 'tags');
  }
  function renderFavAuthors() {
    renderChipList(document.getElementById('favAuthorsList'), document.getElementById('favAuthorsEmpty'), FavAuthors.list(), authorSel, 'fav-author', 'a', 'fa-del', 'authors');
  }
  const favAuthorsClear = document.getElementById('favAuthorsClear');
  if (favAuthorsClear) favAuthorsClear.onclick = () => {
    if (!confirm('确定清空所有收藏作者？可在「搜索偏好库」恢复')) return;
    FavAuthors.list().forEach(a => PrefLibrary.remove('authors', a));
    authorSel.clear(); renderFavAuthors();
  };
  // 标签「清空」：清除本次已选标签（不删收藏列表，方便重新挑选）
  const clearTagsBtn = document.getElementById('clearTagsBtn');
  if (clearTagsBtn) clearTagsBtn.onclick = () => {
    if (favSel.size === 0) { toast('当前没有已选标签', 'warn'); return; }
    favSel.clear(); renderFavTags();  updateQuickTagHighlight();
  };

  function getSearchMode() {
    const active = searchDrop && searchDrop.querySelector('.td-mode-btn.active');
    return active ? active.dataset.mode : 'or';
  }
  function updateSearchCond(kw, tagList, authorList, mode) {
    const el = document.getElementById('searchCond');
    if (!el) return;
    const parts = [];
    if (kw) parts.push(`「${kw}」`);
    if (tagList && tagList.length) parts.push(`标签:${tagList.join(' ')}（${mode === 'and' ? '且' : '或'}）`);
    if (authorList && authorList.length) parts.push(`作者:${authorList.join(' ')}`);
    if (parts.length) { el.textContent = ' · ' + parts.join(' · '); el.style.display = ''; }
    else el.style.display = 'none';
  }

  // sessionStorage 持久化
  const SK_KEY = 'jm_search_state';
  const SCROLL_KEY = 'jm_search_scroll';
  function loadState() {
    try {
      const s = JSON.parse(sessionStorage.getItem(SK_KEY) || 'null');
      if (!s) return false;
      input.value = s.keyword || '';
      if (sortBySel) sortBySel.value = s.order_by || 'mr';
      if (timeSel) timeSel.value = s.time_range || 'a';
      if (dirSel) dirSel.value = s.sort_order || 'desc';
      // 恢复本次已选标签/作者（避免切回搜索页后选择被清空）
      if (Array.isArray(s.sel_tags)) { s.sel_tags.forEach(t => favSel.add(t)); }
      if (Array.isArray(s.sel_authors)) { s.sel_authors.forEach(a => authorSel.add(a)); }
      if (s.keyword && s.cards && s.cards.length) {
        keyword = s.keyword; page = s.page || 1; total = s.total || 0;
        renderCards(s.cards, false, grid, searchCardOpts);
        if (countEl) countEl.textContent = `${s.total || 0} 个结果（已恢复）`;
        document.getElementById('loadMore').style.display = (s.hasMore || false) ? 'block' : 'none';
        renderFavTags(); renderFavAuthors(); updateQuickTagHighlight();         updateSearchCond(keyword, [...favSel], [...authorSel], getSearchMode());
        syncClearKwBtn();
        return true;
      }
    } catch (_) {}
    return false;
  }
  function saveState(hasMore = false) {
    try {
      const cards = [...grid.querySelectorAll('.comic-card')].map(card => ({
        id: card.dataset.jmId,
        title: card.dataset.title,
        author: card.dataset.author,
        tags: (card.dataset.tags || '').split(',').filter(Boolean),
        pages: card.dataset.pages ? parseInt(card.dataset.pages, 10) : 0,
      }));
      sessionStorage.setItem(SK_KEY, JSON.stringify({
        keyword, page, total,
        order_by: sortBySel ? sortBySel.value : 'mr',
        time_range: timeSel ? timeSel.value : 'a',
        sort_order: dirSel ? dirSel.value : 'desc',
        sel_tags: [...favSel],
        sel_authors: [...authorSel],
        cards,
        hasMore,
      }));
    } catch (_) {}
  }
  function clearState() {
    try {
      sessionStorage.removeItem(SK_KEY);
      sessionStorage.removeItem(SCROLL_KEY);
    } catch (_) {}
  }

  async function doSearch(reset = true) {
    keyword = input.value.trim();
    const selTags = [...favSel];
    const selAuthors = [...authorSel];
    const hasKw = !!keyword, hasTag = selTags.length > 0, hasAuthor = selAuthors.length > 0;
    if (!hasKw && !hasTag && !hasAuthor) { toast('请输入关键词、选择标签或选择作者', 'warn'); return; }
    if (reset) {
      if (keyword) SearchHistory.add(keyword);
      page = 1; grid.innerHTML = ''; total = 0;
      try { sessionStorage.removeItem(SCROLL_KEY); } catch (_) {}
    }
    loading.style.display = 'block'; empty.style.display = 'none';
    document.getElementById('loadMore').style.display = 'none';
    const mode = getSearchMode();
    const orderBy = sortBySel ? sortBySel.value : 'mr';
    const timeRange = timeSel ? timeSel.value : 'a';
    const sortOrder = dirSel ? dirSel.value : 'desc';
    let res, condKw = '', condTags = [], condAuthors = [];

    try {
      if (hasTag || hasAuthor) {
        // 有标签或作者：走联合搜索
        condTags = selTags; condAuthors = selAuthors; condKw = keyword;
        const params = new URLSearchParams();
        if (keyword) params.set('keyword', keyword);
        if (selTags.length) params.set('tags', selTags.join(','));
        if (selAuthors.length) params.set('author', selAuthors.join(','));
        params.set('mode', mode);
        res = await api('GET', `/api/search/combined?${params.toString()}`);
      } else if (/^\d+$/.test(keyword)) {
        condKw = keyword;
        res = await api('GET', `/api/search/jm/${keyword}`);
      } else {
        condKw = keyword;
        res = await api('GET', `/api/search/keyword?keyword=${encodeURIComponent(keyword)}&page=${page}&sort=${sortOrder}&order_by=${orderBy}&time=${timeRange}`);
      }
      loading.style.display = 'none';
      if (res.success && res.data && res.data.length) {
        renderCards(res.data, true, grid, searchCardOpts); total = res.data.length;
        countEl.textContent = `${total} 个结果`;
        if (!hasTag && !hasAuthor) {
          const hasMore = res.data.length >= 20;
          document.getElementById('loadMore').style.display = hasMore ? 'block' : 'none';
        }
      } else if (res.success) {
        empty.style.display = 'block'; countEl.textContent = '';
      } else {
        empty.style.display = 'block'; countEl.textContent = '';
        toast(res.message || '搜索失败', 'error');
      }
    } catch (e) {
      loading.style.display = 'none'; empty.style.display = 'block';
      toast('搜索失败：' + (e.message || e), 'error');
    }

    updateSearchCond(condKw, condTags, condAuthors, mode);
    attachRipple(grid);
    setupLazyCovers(grid);
    saveState(false);
  }

  // ── 事件绑定 ──
  btn.onclick = () => { hideSearchDrop(); clearState(); doSearch(true); };
  input.addEventListener('keydown', e => { if (e.key === 'Enter') { hideSearchDrop(); clearState(); doSearch(true); } });
  input.addEventListener('focus', showSearchDrop);
  input.addEventListener('click', showSearchDrop); // 已聚焦时点击也能展开
  input.addEventListener('input', () => {
    if (clearKwBtn) clearKwBtn.hidden = !input.value.trim();
    if (!input.value.trim()) showSearchDrop();
  });
  // 失去焦点时延迟关闭，方便点击下拉内部
  input.addEventListener('blur', () => setTimeout(hideSearchDrop, 180));
  document.getElementById('loadMore').onclick = () => { page++; doSearch(false); };
  [sortBySel, timeSel, dirSel].forEach(el => {
    if (!el) return;
    el.addEventListener('change', () => {
      saveSearchPrefs();
      clearState(); doSearch(true);
    });
  });

  // ── 批量下载入口（独立函数，搜索页与下载管理页共用）──
  initBatchBar();

  // 下拉面板开关（点击标签区空白处展开）
  const sbTagArea = document.getElementById('sbTagArea');
  if (sbTagArea) {
    sbTagArea.addEventListener('click', (e) => {
      if (e.target.closest('.qt-chip')) return;       // 点芯片只切换选中，不展开面板
      if (e.target.closest('.batch-bar')) return;      // 点批量下载区不展开下拉
      e.stopPropagation();
      if (searchDrop && !searchDrop.hidden) hideSearchDrop(); else showSearchDrop();
    });
  }
  if (searchDrop) {
    searchDrop.querySelectorAll('.td-mode-btn').forEach(b => {
      b.addEventListener('click', () => {
        searchDrop.querySelectorAll('.td-mode-btn').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
      });
    });
    searchDrop.addEventListener('mousedown', e => e.preventDefault()); // 防止内部点击触发 input blur
  }
  document.addEventListener('click', (e) => {
    if (searchDrop && !searchDrop.hidden &&
        !e.target.closest('#searchDrop') && !e.target.closest('#sbTagArea') && !e.target.closest('#searchInput')) {
      hideSearchDrop();
    }
  });

  // ── 清空按钮 ──
  function syncClearKwBtn() {
    if (!clearKwBtn) return;
    clearKwBtn.classList.toggle('sb-clear--visible', !!input.value.trim());
  }
  if (clearKwBtn) clearKwBtn.onclick = () => { input.value = ''; syncClearKwBtn(); clearState(); input.focus(); };
  input.addEventListener('input', syncClearKwBtn);
  input.addEventListener('focus', syncClearKwBtn);
  syncClearKwBtn();
  // 清空已选标签按钮已移到搜索按钮旁（clearTagsBtn）

  // ── 常用标签（钉在搜索栏，拖拽排序）──
  const qtagsEl = document.getElementById('quickTags');
  function renderQuickTags() {
    if (!qtagsEl) return;
    const arr = QuickTags.list();
    qtagsEl.innerHTML = arr.map((t, i) =>
      `<span class="qt-chip" draggable="true" data-idx="${i}" data-t="${escapeHtml(t)}">
        ${escapeHtml(t)}<button class="qt-del" data-t="${escapeHtml(t)}">&times;</button>
      </span>`).join('');
    qtagsEl.querySelectorAll('.qt-chip').forEach(chip => {
      chip.addEventListener('dragstart', e => { e.dataTransfer.setData('text/plain', chip.dataset.idx); chip.classList.add('qt-drag'); });
      chip.addEventListener('dragend', () => chip.classList.remove('qt-drag'));
      chip.addEventListener('dragover', e => e.preventDefault());
      chip.addEventListener('drop', e => {
        e.preventDefault();
        const from = parseInt(e.dataTransfer.getData('text/plain'));
        const to = parseInt(chip.dataset.idx);
        if (!isNaN(from) && !isNaN(to) && from !== to) { QuickTags.move(from, to); renderQuickTags(); updateQuickTagHighlight(); }
      });
      chip.addEventListener('click', e => {
        if (e.target.closest('.qt-del')) return;
        const t = chip.dataset.t;
        if (favSel.has(t)) favSel.delete(t); else favSel.add(t);
        chip.classList.toggle('qt-on');
        renderFavTags();        });
      chip.querySelector('.qt-del').addEventListener('click', e => { e.stopPropagation(); QuickTags.remove(chip.dataset.t); favSel.delete(chip.dataset.t); renderQuickTags();   });
      if (favSel.has(chip.dataset.t)) chip.classList.add('qt-on');
    });
  }
  function updateQuickTagHighlight() {
    if (!qtagsEl) return;
    qtagsEl.querySelectorAll('.qt-chip').forEach(chip => chip.classList.toggle('qt-on', favSel.has(chip.dataset.t)));
  }
  renderQuickTags();
  // 常用标签区接收从下拉拖入的标签
  if (qtagsEl) {
    qtagsEl.addEventListener('dragover', e => { e.preventDefault(); qtagsEl.classList.add('qt-drop'); });
    qtagsEl.addEventListener('dragleave', () => qtagsEl.classList.remove('qt-drop'));
    qtagsEl.addEventListener('drop', e => {
      e.preventDefault(); qtagsEl.classList.remove('qt-drop');
      const t = e.dataTransfer.getData('text/plain');
      if (t && !QuickTags.list().includes(t)) { QuickTags.add(t); renderQuickTags(); updateQuickTagHighlight(); }
    });
  }

  // ── 回到顶部 ──
  const backBtn = document.getElementById('backToTop');
  if (backBtn && main) {
    main.addEventListener('scroll', () => { backBtn.hidden = main.scrollTop < 300; });
    backBtn.addEventListener('click', () => { main.scrollTop = 0; main.style.scrollBehavior = 'auto'; });
  }

  // URL 参数：支持 ?author=xxx 自动搜索
  let urlTriggered = false;
  (function applyUrlParams() {
    const params = new URLSearchParams(location.search);
    const authorParam = (params.get('author') || '').trim();
    const tagParam = (params.get('tag') || '').trim();
    if (authorParam) { authorSel.add(authorParam);   }
    if (tagParam) { favSel.add(tagParam);   updateQuickTagHighlight(); }
    if (authorParam || tagParam) { urlTriggered = true; hideSearchDrop(); clearState(); doSearch(true); }
  })();

  // 恢复上次搜索结果与滚动位置
  const restored = !urlTriggered && loadState();
  if (restored) {
    attachRipple(grid);
    setupLazyCovers(grid);
    try {
      const sy = parseInt(sessionStorage.getItem(SCROLL_KEY) || '0', 10) || 0;
      if (sy > 0 && main) {
        const prev = main.style.scrollBehavior;
        main.style.scrollBehavior = 'auto';
        let tries = 0;
        const applyScroll = () => {
          main.scrollTop = sy;
          if (tries++ < 20) setTimeout(applyScroll, 50);
          else main.style.scrollBehavior = prev;
        };
        requestAnimationFrame(applyScroll);
        window.addEventListener('load', () => { main.scrollTop = sy; }, { once: true });
      }
    } catch (_) {}
  }
  // 多路保存滚动位置
  let _scrollSaveTimer = null;
  const saveScrollPos = () => {
    try { if (main) sessionStorage.setItem(SCROLL_KEY, String(main.scrollTop || 0)); } catch (_) {}
  };
  if (main) {
    main.addEventListener('scroll', () => {
      if (_scrollSaveTimer) return;
      _scrollSaveTimer = setTimeout(() => { _scrollSaveTimer = null; saveScrollPos(); }, 150);
    }, { passive: true });
  }
  window.addEventListener('beforeunload', saveScrollPos);
  window.addEventListener('pagehide', saveScrollPos);
  document.addEventListener('visibilitychange', () => { if (document.hidden) saveScrollPos(); });
  document.addEventListener('click', saveScrollPos, true);
}

/* ═══════════════════════════════════════════════════════
   主页（推荐）
   ═══════════════════════════════════════════════════════ */
function initHome() {
  const recommendGrid = document.getElementById('recommendGrid');
  const recommendLoading = document.getElementById('recommendLoading');
  const recommendEmpty = document.getElementById('recommendEmpty');
  const recommendNote = document.getElementById('recommendNote');
  if (!recommendGrid) return;
  async function loadRecommendations() {
    recommendGrid.innerHTML = '';
    if (recommendEmpty) recommendEmpty.style.display = 'none';
    if (recommendLoading) recommendLoading.style.display = 'block';
    if (recommendNote) recommendNote.textContent = '';
    try {
      const r = await api('GET', '/api/recommend');
      if (recommendLoading) recommendLoading.style.display = 'none';
      if (!r.success || !r.enabled) { if (recommendEmpty) recommendEmpty.style.display = 'block'; return; }
      const data = r.data || [];
      if (!data.length) {
        if (recommendEmpty) recommendEmpty.style.display = 'block';
        if (recommendNote && r.note) recommendNote.textContent = r.note;
        return;
      }
      renderCards(data, false, recommendGrid, {
        onAuthorClick: (author) => { location.href = `/search?author=${encodeURIComponent(author)}`; }
      });
      attachRipple(recommendGrid);
      setupLazyCovers(recommendGrid);
      if (recommendNote) {
        const src = (r.seeds && r.seeds.length) ? `基于 ${r.seeds.length} 个兴趣词` : '';
        recommendNote.textContent = src + (r.is_default ? ' · 默认' : '');
      }
    } catch (e) {
      if (recommendLoading) recommendLoading.style.display = 'none';
      if (recommendEmpty) recommendEmpty.style.display = 'block';
    }
  }
  loadRecommendations();
}

/* ═══════════════════════════════════════════════════════
   阅读页（书架 + 分类树）
   ═══════════════════════════════════════════════════════ */
let _currentCat = null; // null = 全部
const _openNodes = new Set(); // 展开的树节点 id 集合（默认全部展开）
function initLibrary() {
  const grid = document.getElementById('comicGrid');
  const treeEl = document.getElementById('catTree');
  const nameEl = document.getElementById('currentCatName');
  const countEl = document.getElementById('currentCatCount');
  const empty = document.getElementById('emptyState');
  let _dragData = null; // {type:'comic'|'category', jmId|catId}

  function clearDropTargets() {
    treeEl.querySelectorAll('.drop-target').forEach(x => x.classList.remove('drop-target'));
  }

  async function loadTree() {
    const r = await api('GET', '/api/categories?tree=1');
    const tree = r.data || [];
    let totalComics = 0;
    tree.forEach(countSub);
    function countSub(n) { totalComics += (n.count || n.comic_count || 0); (n.children || []).forEach(countSub); }
    // 收集所有节点 id 并默认展开
    function collectIds(nodes) { nodes.forEach(n => { _openNodes.add(n.id); (n.children || []).length && collectIds(n.children); }); }
    collectIds(tree);
    let html = `<div class="tree-item ${_currentCat == null ? 'active' : ''}" data-cat="all">
        <i class="fas fa-layer-group tree-item__icon"></i>
        <span class="tree-item__name">全部漫画</span>
        <span class="tree-item__count">${totalComics}</span>
      </div>`;
    html += renderTree(tree, 0);
    treeEl.innerHTML = html;
    treeEl.querySelectorAll('.tree-item').forEach(it => {
      const catId = it.dataset.cat;
      it.onclick = (e) => {
        if (e.target.closest('.cat-move-btn')) return; // 点移动按钮不触发选择
        const v = catId;
        _currentCat = v === 'all' ? null : parseInt(v);
        treeEl.querySelectorAll('.tree-item').forEach(x => x.classList.remove('active'));
        it.classList.add('active');
        loadComics();
      };
      // 分类可拖动（用于调整层级）
      if (catId !== 'all') {
        it.setAttribute('draggable', 'true');
        it.addEventListener('dragstart', (e) => {
          _dragData = { type: 'category', catId: parseInt(catId) };
          it.classList.add('dragging');
          e.dataTransfer.effectAllowed = 'move';
          try { e.dataTransfer.setData('text/plain', String(catId)); } catch (_) {}
        });
        it.addEventListener('dragend', () => { _dragData = null; it.classList.remove('dragging'); clearDropTargets(); });
      }
      // 作为放置目标：接受漫画（移动分类）或分类（调整父级）
      it.addEventListener('dragover', (e) => {
        if (!_dragData) return;
        if (_dragData.type === 'category' && _dragData.catId === parseInt(catId)) return;
        e.preventDefault();
        it.classList.add('drop-target');
      });
      it.addEventListener('dragleave', () => it.classList.remove('drop-target'));
      it.addEventListener('drop', (e) => {
        e.preventDefault();
        it.classList.remove('drop-target');
        if (!_dragData) return;
        if (_dragData.type === 'comic') {
          if (catId === 'all') moveComicToCategory(_dragData.jmId, null);
          else moveComicToCategory(_dragData.jmId, parseInt(catId));
        } else if (_dragData.type === 'category' && _dragData.catId !== parseInt(catId)) {
          moveCategoryToParent(_dragData.catId, parseInt(catId));
        }
        _dragData = null;
      });
    });
    // 分类移动按钮（移动端友好）
    treeEl.querySelectorAll('.cat-move-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        openCategoryMovePicker(parseInt(btn.dataset.cat), tree);
      });
    });
    // 展开/折叠切换
    treeEl.querySelectorAll('.tree-item__toggle[data-toggle]').forEach(toggle => {
      toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = parseInt(toggle.dataset.toggle);
        const childrenDiv = treeEl.querySelector(`.tree-children[data-parent="${id}"]`);
        if (!childrenDiv) return;
        if (_openNodes.has(id)) {
          _openNodes.delete(id);
          toggle.classList.remove('open', 'fa-chevron-down');
          toggle.classList.add('fa-chevron-right');
          childrenDiv.style.display = 'none';
        } else {
          _openNodes.add(id);
          toggle.classList.remove('fa-chevron-right');
          toggle.classList.add('open', 'fa-chevron-down');
          childrenDiv.style.display = '';
        }
      });
    });
  }
  function renderTree(nodes, depth) {
    return nodes.map(n => {
      const hasChildren = n.children && n.children.length;
      const count = n.count || n.comic_count || 0;
      const isOpen = _openNodes.has(n.id);
      const toggleHtml = hasChildren
        ? `<i class="fas ${isOpen ? 'fa-chevron-down' : 'fa-chevron-right'} tree-item__toggle ${isOpen ? 'open' : ''}" data-toggle="${n.id}" title="${isOpen ? '折叠' : '展开'}子文件夹"></i>`
        : `<span class="tree-item__toggle"></span>`;
      return `<div class="tree-item ${_currentCat === n.id ? 'active' : ''}" data-cat="${n.id}" style="padding-left:${6 + depth * 16}px">
        ${toggleHtml}
        <i class="fas fa-folder tree-item__icon"></i>
        <span class="tree-item__name">${escapeHtml(n.name)}</span>
        <span class="tree-item__count">${count}</span>
        <button class="cat-move-btn" data-cat="${n.id}" title="移动到其他分类"><i class="fas fa-arrows-up-down-left-right"></i></button>
      </div>` + (hasChildren ? `<div class="tree-children" data-parent="${n.id}" style="${isOpen ? '' : 'display:none'}">${renderTree(n.children, depth + 1)}</div>` : '');
    }).join('');
  }

  async function loadComics() {
    const url = _currentCat != null ? `/api/downloaded?category_id=${_currentCat}` : '/api/downloaded';
    const r = await api('GET', url);
    const list = r.data || [];
    nameEl.textContent = _currentCat == null ? '全部漫画' : '分类';
    countEl.textContent = `${list.length} 本`;
    if (!list.length) {
      grid.innerHTML = '';
      empty.style.display = 'block'; attachRipple(empty); return;
    }
    empty.style.display = 'none';
    grid.innerHTML = '';
    list.forEach((c, i) => {
      const id = c.id;
      const blocked = !!c.blocked;
      const card = document.createElement('div');
      card.className = 'comic-card' + (blocked ? ' comic-card--blocked' : '');
      card.style.animationDelay = (Math.min(i, 15) * 0.03) + 's';
      card.setAttribute('draggable', 'true');
      card.addEventListener('dragstart', (e) => {
        _dragData = { type: 'comic', jmId: id };
        card.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', String(id)); } catch (_) {}
      });
      card.addEventListener('dragend', () => { _dragData = null; card.classList.remove('dragging'); });
      const cats = (c.categories || []).map(x => `<span class="chip chip--accent" style="margin:2px">${escapeHtml(x.name)}</span>`).join('');
      card.innerHTML = `
        ${blocked ? `<div class="blocked-badge"><i class="fas fa-ban"></i> 已拉黑</div>
        <button class="comic-card__fab unblock-fab" data-act="unblock" title="取消拉黑"><i class="fas fa-rotate-left"></i></button>` : ''}
        <button class="comic-card__fab" title="移动到分类（也可直接拖拽）" data-act="move"><i class="fas fa-folder-plus"></i></button>
        <img class="comic-card__cover" src="${c.cover_path ? '/api/cover/downloaded/' + id : "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='3' height='4'%3E%3C/svg%3E"}" onclick="location.href='/reader/${id}'">
        <div class="comic-card__body">
          <div class="comic-card__title">${escapeHtml(c.title || '未命名漫画')}</div>
          ${renderPageChapter(c.pages, c.chapter_count)}
          <div style="margin:6px 0">${cats}</div>
          <div class="comic-card__actions">
            <button class="btn btn--primary" data-act="read">阅读</button>
            <button class="btn" data-act="del"><i class="fas fa-trash"></i></button>
          </div>
        </div>`;
      card.querySelector('[data-act="read"]').onclick = () => location.href = `/reader/${id}`;
      card.querySelector('[data-act="del"]').onclick = async (e) => {
        e.stopPropagation();
        if (!confirm('确定删除这本漫画？（仅本地文件）')) return;
        const d = await api('DELETE', `/api/delete/${id}`);
        if (d.success) { toast('已删除', 'success'); loadComics(); loadTree(); }
        else toast(d.message || '删除失败', 'error');
      };
      card.querySelector('[data-act="move"]').onclick = async (e) => {
        e.stopPropagation();
        const cur = (c.categories || []).map(x => x.id);
        pickCategories(cur, async (cats) => {
          const res = await api('PUT', `/api/comic/${id}/categories`, { category_ids: cats });
          if (res.success) { toast('已更新分类', 'success'); loadComics(); loadTree(); }
        });
      };
      if (blocked) {
        const ub = card.querySelector('[data-act="unblock"]');
        if (ub) ub.addEventListener('click', async (e) => {
          e.stopPropagation();
          await unblockComic(id, c.author);
          loadComics();
        });
      }
      grid.appendChild(card);
    });
    attachRipple(grid);
  }

  async function moveComicToCategory(jmId, catId) {
    const cids = catId == null ? [] : [catId];
    const res = await api('PUT', `/api/comic/${jmId}/categories`, { category_ids: cids });
    if (res.success) {
      toast(catId == null ? '已移到「全部」（取消分类）' : '已移动分类', 'success');
      loadComics(); loadTree();
    } else toast(res.message || '移动失败', 'error');
  }

  async function moveCategoryToParent(catId, parentId) {
    const res = await api('POST', `/api/categories/${catId}/move`, { parent_id: parentId });
    if (res.success) { toast('已移动分类', 'success'); loadTree(); loadComics(); }
    else toast(res.message || '移动失败', 'error');
  }

  function collectDescendantIds(tree, id) {
    const res = [];
    (function walk(ns) {
      for (const n of ns) {
        if (n.id === id) {
          (function collect(x) { (x.children || []).forEach(ch => { res.push(ch.id); collect(ch); }); })(n);
          return;
        }
        walk(n.children || []);
      }
    })(tree);
    return res;
  }

  function openCategoryMovePicker(catId, tree) {
    const forbidden = new Set([catId, ...collectDescendantIds(tree, catId)]);
    const flat = [];
    (function walk(ns, depth) {
      ns.forEach(n => {
        if (!forbidden.has(n.id)) flat.push({ id: n.id, name: n.name, depth });
        walk(n.children || [], depth + 1);
      });
    })(tree, 0);
    flat.unshift({ id: 0, name: '（根目录 / 顶级分类）', depth: 0 });
    const bodyHtml = `<div class="tree">${flat.map(n => `
      <div class="tree-item" data-pick="${n.id}" style="padding-left:${10 + n.depth * 14}px">
        <i class="fas ${n.id === 0 ? 'fa-layer-group' : 'fa-folder'} tree-item__icon"></i>
        <span class="tree-item__name">${escapeHtml(n.name)}</span>
      </div>`).join('')}</div>`;
    const { close } = openModal({
      title: '移动到…', sub: '选择该分类的新父级', bodyHtml,
      actions: [{ key: 'cancel', label: '取消', cls: 'btn--ghost', onClick: c => c() }],
    });
    document.querySelectorAll('[data-pick]').forEach(el => {
      el.onclick = async () => {
        const pid = parseInt(el.dataset.pick) || null;
        close();
        await moveCategoryToParent(catId, pid);
      };
    });
  }

  document.getElementById('newCatBtn').onclick = async () => {
    const name = prompt('新建分类名称：');
    if (!name) return;
    const r = await api('POST', '/api/categories', { name });
    if (r.success) { toast('已创建', 'success'); loadTree(); }
  };
  document.getElementById('newSubBtn').onclick = async () => {
    if (_currentCat == null) { toast('请先选择一个父分类', 'warn'); return; }
    const name = prompt('新建子分类名称：');
    if (!name) return;
    const r = await api('POST', '/api/categories', { name, parent_id: _currentCat });
    if (r.success) { toast('已创建', 'success'); loadTree(); }
  };

  loadTree();
  loadComics();
}

async function reDownloadFromHistory(jmId) {
  try {
    const r = await api('GET', `/api/comic/${jmId}/categories`);
    const cats = (r.success && r.data) ? r.data : [];
    downloadComic(jmId, cats);
    toast('已加入下载队列（原分类）', 'success');
  } catch (_) {
    downloadComic(jmId, []);
  }
}

/* ═══════════════════════════════════════════════════════
   设置页
   ═══════════════════════════════════════════════════════ */
function initSettings() {
  // 缓存
  api('GET', '/api/cache/status').then(r => {
    if (r.success) document.getElementById('cacheSize').textContent = `${r.data.cache_size_mb} MB`;
  });
  document.getElementById('clearCacheBtn').onclick = async () => {
    const r = await api('POST', '/api/cache/clear');
    if (r.success) { toast(`已清理 ${r.data.cleared_size_mb} MB`, 'success');
      document.getElementById('cacheSize').textContent = `${r.data.remaining_size_mb} MB`; }
  };

  // 分类钻取导航：点分类块 → 展开对应详情面板；返回回到上一层（主页或父级面板）
  const settingsHome = document.getElementById('settingsHome');
  const settingsDetail = document.getElementById('settingsDetail');
  const drillStack = []; // 记录父级面板 cat，支持屏蔽管理 → 搜索设置 这样的二级返回
  function showDrillPanel(cat) {
    if (!settingsDetail) return;
    settingsDetail.querySelectorAll('.drill-panel').forEach(p => { p.hidden = (p.dataset.cat !== cat); });
    if (settingsHome) settingsHome.hidden = true;
    settingsDetail.hidden = false;
    window.scrollTo({ top: 0 });
    // 重放进入动画，营造“跳到独立隐藏子页”的感觉（与下载页一致）
    settingsDetail.classList.remove('page-enter');
    void settingsDetail.offsetWidth;
    settingsDetail.classList.add('page-enter');
    if (cat === 'block') loadBlocklist();
  }
  function openDrill(cat, parentCat = null) {
    if (parentCat) drillStack.push(parentCat);
    else drillStack.length = 0; // 从设置主页进入新分类，清空栈
    showDrillPanel(cat);
  }
  if (settingsHome && settingsDetail) {
    settingsHome.querySelectorAll('.cat-card').forEach(card => {
      card.addEventListener('click', () => openDrill(card.dataset.cat));
    });
    const drillBack = document.getElementById('drillBack');
    if (drillBack) drillBack.addEventListener('click', () => {
      if (drillStack.length) {
        const prev = drillStack.pop();
        showDrillPanel(prev);
      } else {
        settingsDetail.hidden = true;
        settingsHome.hidden = false;
        window.scrollTo({ top: 0 });
      }
    });
    const blockPageBtn = document.getElementById('blockPageBtn');
    if (blockPageBtn) blockPageBtn.addEventListener('click', () => openDrill('block', 'search'));
  }

  // 搜索偏好库（历史 / 标签 / 作者 统一管理，误删可恢复）—— 现在内嵌在「搜索设置」分类里
  initPrefLib();

  // 收藏标签高亮模式（已从搜索下拉面板移到搜索设置）
  const favTagHighlight = document.getElementById('favTagHighlight');
  if (favTagHighlight) {
    favTagHighlight.value = Settings.get('fav_tag_highlight', 'same');
    favTagHighlight.addEventListener('change', () => {
      Settings.set('fav_tag_highlight', favTagHighlight.value);
      // 刷新当前结果卡片上的标签高亮
      const favArr = FavTags.list();
      document.querySelectorAll('.comic-card').forEach(card => {
        const tags = (card.dataset.tags || '').split(',').filter(Boolean);
        const tagsEl = card.querySelector('.comic-card__tags');
        if (!tagsEl) return;
        tagsEl.innerHTML = cardTagsHtml(tags, new Set(), favArr);
        tagsEl.querySelectorAll('.comic-card__tag').forEach(tg => {
          tg.addEventListener('click', (e) => {
            e.stopPropagation();
            const t = tg.dataset.tag;
            if (FavTags.contains(t)) { FavTags.remove(t); tg.classList.remove('on'); }
            else { FavTags.add(t); tg.classList.add('on'); }
          });
          tg.addEventListener('contextmenu', (e) => {
            e.preventDefault(); e.stopPropagation();
            showTagContextMenu(e.clientX, e.clientY, tg.dataset.tag, tg);
          });
        });
      });
    });
  }

  // 设置项加载
  api('GET', '/api/settings').then(r => {
    if (!r.success) return; const s = r.data;
    appConfigCache = s;
    const maxConcurrentEl = document.getElementById('maxConcurrent');
    const maxConcurrentValEl = document.getElementById('maxConcurrentVal');
    if (s.max_concurrent_downloads && maxConcurrentEl) maxConcurrentEl.value = s.max_concurrent_downloads;
    if (maxConcurrentEl && maxConcurrentValEl) {
      maxConcurrentValEl.textContent = maxConcurrentEl.value;
      maxConcurrentEl.addEventListener('input', () => { maxConcurrentValEl.textContent = maxConcurrentEl.value; });
    }
    if (s.image_quality) document.getElementById('imageQuality').value = s.image_quality;
    if (s.auto_cleanup_cache) document.getElementById('autoCleanup').checked = s.auto_cleanup_cache === '1' || s.auto_cleanup_cache === true || s.auto_cleanup_cache === 'true';
    if (s.proxy_enabled) document.getElementById('proxyEnabled').checked = s.proxy_enabled === '1' || s.proxy_enabled === true || s.proxy_enabled === 'true';
    if (s.proxy_url) document.getElementById('proxyUrl').value = s.proxy_url;
    const limit = parseInt(s.search_result_limit, 10);
    document.getElementById('searchResultLimit').value = (isNaN(limit) || limit < 10) ? 80 : limit;
    if (s.search_priority) document.getElementById('searchPriority').value = (s.search_priority === 'equal') ? 'equal' : 'input';
    const hidePC = s.hide_page_chapter === '1' || s.hide_page_chapter === true || s.hide_page_chapter === 'true';
    document.getElementById('hidePageChapter').checked = hidePC;
    document.body.classList.toggle('hide-page-chapter', hidePC);
    const showBH = s.show_block_hits !== '0';
    document.getElementById('showBlockHits').checked = showBH;
    if (s.theme) Theme.set(s.theme);
    const tagLimitEl = document.getElementById('searchTagLimit');
    if (tagLimitEl) {
      const tl = parseInt(s.search_tag_limit, 10);
      tagLimitEl.value = (isNaN(tl) || tl < 0) ? 0 : tl;
    }
    const readerToastEl = document.getElementById('readerChapterToast');
    if (readerToastEl) readerToastEl.checked = s.reader_chapter_toast !== '0';
  });

  // 即时保存：任一设置项变更即写入后端（不再需要「保存设置」按钮）
  async function saveSettings(showToast = true) {
    let limit = parseInt(document.getElementById('searchResultLimit').value, 10);
    if (isNaN(limit) || limit < 10) limit = 80;
    if (limit > 500) limit = 500;
    document.getElementById('searchResultLimit').value = limit;
    const hidePC = document.getElementById('hidePageChapter').checked;
    document.body.classList.toggle('hide-page-chapter', hidePC);
    const payload = {
      max_concurrent_downloads: document.getElementById('maxConcurrent').value,
      image_quality: document.getElementById('imageQuality').value,
      auto_cleanup_cache: document.getElementById('autoCleanup').checked ? '1' : '0',
      proxy_enabled: document.getElementById('proxyEnabled').checked ? '1' : '0',
      proxy_url: document.getElementById('proxyUrl').value.trim(),
      search_result_limit: String(limit),
      search_priority: document.getElementById('searchPriority').value || 'input',
      hide_page_chapter: hidePC ? '1' : '0',
      show_block_hits: document.getElementById('showBlockHits').checked ? '1' : '0',
      search_tag_limit: String(parseInt(document.getElementById('searchTagLimit').value, 10) || 0),
      reader_chapter_toast: document.getElementById('readerChapterToast').checked ? '1' : '0',
      theme: Theme.get()
    };
    const r = await api('POST', '/api/settings', payload);
    if (r.success) {
      setAppConfig('show_block_hits', payload.show_block_hits);
      setAppConfig('search_tag_limit', payload.search_tag_limit);
      if (showToast) toast('设置已保存', 'success');
    } else if (showToast) {
      toast('保存失败', 'error');
    }
  }
  // 滑块(拖动)只更新数字标签；松手(change)才保存，避免拖动过程狂写
  const mcEl = document.getElementById('maxConcurrent');
  if (mcEl) mcEl.addEventListener('input', () => { const v = mcEl.value; const vEl = document.getElementById('maxConcurrentVal'); if (vEl) vEl.textContent = v; });
  const bindSave = (id, evt) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener(evt, () => saveSettings());
  };
  bindSave('maxConcurrent', 'change');
  bindSave('imageQuality', 'change');
  bindSave('autoCleanup', 'change');
  bindSave('proxyEnabled', 'change');
  bindSave('proxyUrl', 'change');
  bindSave('searchResultLimit', 'change');
  bindSave('searchPriority', 'change');
  bindSave('hidePageChapter', 'change');
  bindSave('showBlockHits', 'change');
  bindSave('searchTagLimit', 'change');
  bindSave('readerChapterToast', 'change');

  // 关于
  api('GET', '/api/settings').then(r => {});
  const vEl = document.getElementById('appVersion');
  if (vEl && vEl.dataset.v) vEl.textContent = vEl.dataset.v;

  /* ── 屏蔽管理 ── */
  const blockListEl = document.getElementById('blockList');
  function renderBlockList(rows, titleMap) {
    if (!blockListEl) return;
    if (!rows.length) { blockListEl.innerHTML = `<div class="manage-empty">还没有屏蔽项</div>`; return; }
    const typeLabels = { work: '作品', author: '作者', tag: '标签' };
    blockListEl.innerHTML = `<div class="block-chips">` + rows.map(b => {
      const isWork = b.block_type === 'work';
      const isTag = b.block_type === 'tag';
      const workTitle = isWork && titleMap ? titleMap[String(b.value)] : null;
      const val = isWork ? (workTitle ? escapeHtml(workTitle) : `JM-${escapeHtml(b.value)}`) : escapeHtml(b.value);
      const typeLabel = typeLabels[b.block_type] || b.block_type;
      return `<span class="block-chip" data-id="${b.id}" data-type="${b.block_type}" data-value="${escapeHtml(b.value)}" title="点击查看预览">
        <span class="block-chip__type">${typeLabel}</span>
        <span class="block-chip__name">${val}</span>
        <button class="block-chip__del" data-id="${b.id}" title="取消屏蔽"><i class="fas fa-times"></i></button>
      </span>`;
    }).join('') + `</div>`;
    blockListEl.querySelectorAll('.block-chip').forEach(chip => {
      chip.addEventListener('click', async (e) => {
        if (e.target.closest('.block-chip__del')) return;
        const type = chip.dataset.type;
        const value = chip.dataset.value;
        await previewBlockItem(type, value);
      });
    });
    blockListEl.querySelectorAll('.block-chip__del').forEach(b => {
      b.onclick = async () => {
        const r = await api('DELETE', `/api/blocklist/${b.dataset.id}`);
        if (r.success) loadBlocklist(); else toast('移除失败', 'error');
      };
    });
  }
  async function previewBlockItem(type, value) {
    openModal({
      title: type === 'work' ? '作品预览' : type === 'author' ? '作者作品预览' : '标签作品预览',
      sub: type === 'work' ? `JM-${value}` : `「${value}」`,
      bodyHtml: `<div id="blockPreviewLoading" style="text-align:center;padding:22px"><i class="fas fa-spinner fa-spin"></i> 加载中…</div><div id="blockPreviewGrid" class="comic-grid stagger"></div><div id="blockPreviewEmpty" class="empty" style="display:none"><div class="empty__icon"><i class="fas fa-search"></i></div><h3>暂无结果</h3></div>`,
      actions: [{ key: 'ok', label: '关闭', cls: 'btn--primary', onClick: c => c() }],
    });
    try {
      const r = await api('GET', `/api/blocklist/preview?type=${encodeURIComponent(type)}&value=${encodeURIComponent(value)}&limit=12`);
      const loading = document.getElementById('blockPreviewLoading');
      if (loading) loading.style.display = 'none';
      const grid = document.getElementById('blockPreviewGrid');
      const empty = document.getElementById('blockPreviewEmpty');
      if (!r.success || !r.data || !r.data.length) {
        if (grid) grid.style.display = 'none';
        if (empty) empty.style.display = 'block';
        return;
      }
      if (grid) {
        renderCards(r.data, false, grid);
        attachRipple(grid);
        setupLazyCovers(grid);
      }
    } catch (e) {
      const loading = document.getElementById('blockPreviewLoading');
      if (loading) loading.innerHTML = '<p>加载失败</p>';
    }
  }
  async function loadBlocklist() {
    const [br, dr] = await Promise.all([
      api('GET', '/api/blocklist'),
      api('GET', '/api/downloaded')
    ]);
    let titleMap = {};
    if (dr.success) (dr.data || []).forEach(c => { if (c.id != null) titleMap[String(c.id)] = c.title; });
    if (br.success) renderBlockList(br.data || [], titleMap);
  }
  const blockAddBtn = document.getElementById('blockAddBtn');
  if (blockAddBtn) blockAddBtn.onclick = async () => {
    const input = document.getElementById('blockInput');
    const type = document.getElementById('blockType').value;
    const v = input.value.trim();
    if (!v) { toast('请输入要屏蔽的内容', 'warn'); return; }
    const r = await api('POST', '/api/blocklist', { type, value: v });
    if (r.success) { toast('已拉黑', 'success'); input.value = ''; loadBlocklist(); }
    else toast(r.message || '操作失败', 'error');
  };
  loadBlocklist();

  /* ── 同义词 / 别名 ── */
  const aliasListEl = document.getElementById('aliasList');
  function renderAliasList(rows) {
    if (!aliasListEl) return;
    if (!rows.length) { aliasListEl.className = 'manage-list'; aliasListEl.innerHTML = `<div class="manage-empty">还没有同义词组</div>`; return; }
    aliasListEl.className = 'alias-grid';
    aliasListEl.innerHTML = rows.map(a => {
      const icon = a.type === 'author' ? 'fa-user-pen' : 'fa-tag';
      const typeLabel = a.type === 'author' ? '作者' : '标签';
      return `<span class="alias-block">
        <i class="fas ${icon}" style="color:var(--accent);width:16px;text-align:center;flex:0 0 auto"></i>
        <span class="alias-block__type">${typeLabel}</span>
        <span class="alias-block__name">${escapeHtml(a.canonical)} <span class="alias-block__eq">=</span> ${escapeHtml(a.alias)}</span>
        <button class="alias-block__del" data-id="${a.id}" title="移除"><i class="fas fa-times"></i></button>
      </span>`;
    }).join('');
    aliasListEl.querySelectorAll('.alias-block__del').forEach(b => {
      b.onclick = async () => {
        const r = await api('DELETE', `/api/aliases/${b.dataset.id}`);
        if (r.success) loadAliases(); else toast('移除失败', 'error');
      };
    });
  }
  async function loadAliases() {
    const r = await api('GET', '/api/aliases');
    if (r.success) renderAliasList(r.data || []);
  }
  const aliasAddBtn = document.getElementById('aliasAddBtn');
  if (aliasAddBtn) aliasAddBtn.onclick = async () => {
    const type = document.getElementById('aliasType').value;
    const canon = document.getElementById('aliasCanon').value.trim();
    const variants = document.getElementById('aliasVariants').value.trim();
    if (!canon || !variants) { toast('请填写标准词与同义词', 'warn'); return; }
    let ok = true;
    for (const alias of variants.split(/[,，]/).map(s => s.trim()).filter(Boolean)) {
      const r = await api('POST', '/api/aliases', { type, canonical: canon, alias });
      if (!r.success) { ok = false; toast(r.message || '添加失败', 'error'); }
    }
    if (ok) { toast('已添加', 'success'); document.getElementById('aliasCanon').value = ''; document.getElementById('aliasVariants').value = ''; loadAliases(); }
  };
  const aliasSuggestBtn = document.getElementById('aliasSuggestBtn');
  if (aliasSuggestBtn) aliasSuggestBtn.onclick = async () => {
    const el = document.getElementById('aliasSuggest');
    el.className = 'alias-grid';
    el.style.display = '';
    el.innerHTML = `<div class="manage-empty">分析中…</div>`;
    const r = await api('GET', '/api/aliases/suggestions');
    if (!r.success) { el.innerHTML = `<div class="manage-empty">获取失败</div>`; return; }
    const rows = r.data || [];
    if (!rows.length) { el.innerHTML = `<div class="manage-empty">暂无建议（可手动添加）</div>`; return; }
    el.innerHTML = rows.map((s, i) => {
      const icon = s.type === 'author' ? 'fa-user-pen' : 'fa-tag';
      const typeLabel = s.type === 'author' ? '作者' : '标签';
      const badges = s.aliases.map(a => `<span class="alias-chip">${escapeHtml(a)}</span>`).join('');
      return `<span class="alias-block" style="flex-wrap:wrap;align-items:center">
        <i class="fas ${icon}" style="color:var(--accent);width:16px;text-align:center;flex:0 0 auto"></i>
        <span class="alias-block__type">${typeLabel}</span>
        <span class="alias-block__name">${escapeHtml(s.canonical)} <span class="alias-block__eq">≈</span> ${badges}</span>
        <span style="flex-basis:100%;font-size:11px;color:var(--text-3);margin-left:24px;margin-top:-2px">${s.source === 'seed' ? '内置建议' : '本地共现'}${s.score ? ' · 相似度 ' + s.score : ''}</span>
        <button class="alias-block__add" data-i="${i}" title="采纳" style="margin-left:auto"><i class="fas fa-check"></i></button>
      </span>`;
    }).join('');
    el.querySelectorAll('.alias-block__add').forEach(b => {
      b.onclick = async () => {
        const s = rows[parseInt(b.dataset.i)];
        let ok = true;
        for (const alias of s.aliases) {
          const r2 = await api('POST', '/api/aliases', { type: s.type, canonical: s.canonical, alias });
          if (!r2.success) ok = false;
        }
        if (ok) { toast('已采纳同义词', 'success'); loadAliases(); b.closest('.manage-item').remove(); }
        else toast('部分采纳失败', 'error');
      };
    });
  };
  loadAliases();

  /* ── 自动推荐设置 ── */
  let recommendBasis = ['keyword', 'author', 'tag']; // 内存中的推荐依据集合
  let recommendCustom = []; // 内存中的自定义内容 [{type, value}]
  const basisChipEls = document.querySelectorAll('#recommendBasis .basis-chip');
  const recommendCountEl = document.getElementById('recommendCount');
  const recommendEnabledEl = document.getElementById('recommendEnabled');
  const recommendCustomListEl = document.getElementById('recommendCustomList');
  const recommendParamInfoEl = document.getElementById('recommendParamInfo');

  function parseJsonList(str, fallback) {
    if (!str) return fallback;
    try { const v = JSON.parse(str); return Array.isArray(v) ? v : fallback; } catch (_) { return fallback; }
  }
  function renderRecommendParamInfo() {
    if (!recommendParamInfoEl) return;
    const basisLabels = { keyword: '常搜索', author: '作者', tag: '标签' };
    const basisTxt = recommendBasis.map(b => basisLabels[b] || b).join('、') || '无';
    const customTxt = recommendCustom.length
      ? recommendCustom.map(c => `${c.type === 'name' ? '名字' : c.type === 'tag' ? '标签' : '作者'}:${c.value}`).join('，')
      : '无';
    const enabled = recommendEnabledEl ? recommendEnabledEl.checked : true;
    recommendParamInfoEl.textContent = `启用：${enabled ? '是' : '否'} · 数量：${recommendCountEl ? recommendCountEl.value : '?'} · 依据：${basisTxt} · 自定义：${customTxt}`;
  }
  function renderRecommendCustom() {
    if (!recommendCustomListEl) return;
    if (!recommendCustom.length) { recommendCustomListEl.innerHTML = `<div class="manage-empty">还没有自定义推荐内容</div>`; return; }
    const typeLabels = { name: '名字', tag: '标签', author: '作者' };
    recommendCustomListEl.className = 'manage-list';
    recommendCustomListEl.innerHTML = recommendCustom.map((c, i) =>
      `<span class="rc-chip">
        <span class="rc-chip__type">${typeLabels[c.type] || c.type}</span>
        <span class="rc-chip__name">${escapeHtml(c.value)}</span>
        <button class="rc-chip__del" data-i="${i}" title="删除"><i class="fas fa-times"></i></button>
      </span>`).join('');
    recommendCustomListEl.querySelectorAll('.rc-chip__del').forEach(b => {
      b.onclick = () => { recommendCustom.splice(parseInt(b.dataset.i, 10), 1); renderRecommendCustom(); renderRecommendParamInfo(); saveRecommendSettings(false); };
    });
  }
  function renderRecommendBasis() {
    basisChipEls.forEach(el => { el.classList.toggle('on', recommendBasis.includes(el.dataset.basis)); });
  }
  async function saveRecommendSettings(showToast = true) {
    const payload = {
      recommend_enabled: (recommendEnabledEl && recommendEnabledEl.checked) ? '1' : '0',
      recommend_count: String(parseInt(recommendCountEl ? recommendCountEl.value : '20', 10) || 20),
      recommend_basis: recommendBasis,
      recommend_custom: recommendCustom,
    };
    const r = await api('POST', '/api/settings', payload);
    if (r.success) { if (showToast) toast('推荐设置已保存', 'success'); }
    else if (showToast) toast('保存失败', 'error');
    renderRecommendParamInfo();
  }
  // 初始默认值渲染
  renderRecommendBasis(); renderRecommendCustom(); renderRecommendParamInfo();
  // 从后端读取已保存配置覆盖默认
  api('GET', '/api/settings').then(r => {
    if (!r.success) return; const s = r.data || {};
    if (recommendEnabledEl) recommendEnabledEl.checked = (s.recommend_enabled !== '0' && s.recommend_enabled !== false && s.recommend_enabled !== 'false');
    if (recommendCountEl) { const c = parseInt(s.recommend_count, 10); recommendCountEl.value = (isNaN(c) || c < 4) ? 20 : c; }
    const basis = parseJsonList(s.recommend_basis, null);
    recommendBasis = (basis && basis.length) ? basis.filter(b => ['keyword', 'author', 'tag'].includes(b)) : ['keyword', 'author', 'tag'];
    recommendCustom = parseJsonList(s.recommend_custom, []);
    renderRecommendBasis(); renderRecommendCustom(); renderRecommendParamInfo();
  });
  // 依据 chip 点击高亮/变暗
  basisChipEls.forEach(el => {
    el.addEventListener('click', () => {
      const b = el.dataset.basis;
      if (recommendBasis.includes(b)) recommendBasis = recommendBasis.filter(x => x !== b);
      else recommendBasis.push(b);
      el.classList.toggle('on');
      renderRecommendParamInfo();
      saveRecommendSettings(false);
    });
  });
  if (recommendEnabledEl) recommendEnabledEl.addEventListener('change', () => { renderRecommendParamInfo(); saveRecommendSettings(false); });
  if (recommendCountEl) recommendCountEl.addEventListener('change', () => { renderRecommendParamInfo(); saveRecommendSettings(false); });
  const recommendCustomAddBtn = document.getElementById('recommendCustomAdd');
  if (recommendCustomAddBtn) recommendCustomAddBtn.onclick = () => {
    const typeEl = document.getElementById('recommendCustomType');
    const valEl = document.getElementById('recommendCustomValue');
    const type = typeEl ? typeEl.value : 'name';
    const val = valEl ? valEl.value.trim() : '';
    if (!val) { toast('请输入内容', 'warn'); return; }
    if (recommendCustom.some(c => c.type === type && c.value === val)) { toast('已添加过', 'warn'); return; }
    recommendCustom.push({ type, value: val });
    if (valEl) valEl.value = '';
    renderRecommendCustom(); renderRecommendParamInfo(); saveRecommendSettings(false);
  };
}

/* ═══════════════════════════════════════════════════════
   阅读器（独立全屏页，由 reader.html 调用）
   ═══════════════════════════════════════════════════════ */
function initReader() {
  const jmId = parseInt(location.pathname.split('/').pop());
  const stage = document.getElementById('readerStage');
  const titleEl = document.getElementById('readerTitle');
  const zoomVal = document.getElementById('zoomVal');
  const chapSide = document.getElementById('chapSide');
  const chapTrigger = document.getElementById('chapTrigger');
  const chapList = document.getElementById('chapList');
  const chapInfo = document.getElementById('chapInfo');
  const prevChapBtn = document.getElementById('prevChapBtn');
  const nextChapBtn = document.getElementById('nextChapBtn');
  let pages = [], current = 0, zoom = 1, currentChapter = '1';
  let chapters = [], currentChapterIndex = 0, chapterToastEnabled = true;
  // 章节预加载：key=chapterId, value={pages:[], promise, images:[]}
  let preloadCache = {};
  const preloadSettings = {
    enabled: Settings.get('reader_preload_enabled', true),
    thresholdPages: Math.max(1, parseInt(Settings.get('reader_preload_threshold_pages', 5), 10) || 5),
    thresholdRatio: Math.max(1, Math.min(100, parseInt(Settings.get('reader_preload_threshold_ratio', 25), 10) || 25)),
    preloadCount: Math.max(1, parseInt(Settings.get('reader_preload_count', 10), 10) || 10)
  };

  (async () => {
    const [r, cfg] = await Promise.all([api('GET', `/api/read/${jmId}`), loadAppConfig()]);
    chapterToastEnabled = cfg.reader_chapter_toast !== '0';
    if (!r.success) {
      stage.innerHTML = `<div class="empty"><div class="empty__icon"><i class="fas fa-face-sad-tear"></i></div><h3>无法打开</h3><p>${escapeHtml(r.message || '')}</p></div>`;
      return;
    }
    const d = r.data;
    titleEl.textContent = d.title;
    chapters = d.chapters || [];
    currentChapter = (d.current_chapter || '1');
    currentChapterIndex = Math.max(0, chapters.findIndex(c => c.id === currentChapter));
    pages = Array.isArray(d.current_chapter_pages) ? d.current_chapter_pages : [];
    updateChapterUI();
    if (!pages.length) {
      stage.innerHTML = `<div class="empty"><div class="empty__icon"><i class="fas fa-circle-exclamation"></i></div><h3>该章节没有图片</h3><p>可能是下载未完成或目录异常</p></div>`;
      return;
    }
    renderPage(0);
  })();

  function updateChapterUI() {
    const hasMulti = chapters.length > 1;
    if (chapSide) chapSide.style.display = hasMulti ? 'flex' : 'none';
    if (chapInfo && chapters[currentChapterIndex]) {
      chapInfo.textContent = `${currentChapterIndex + 1} / ${chapters.length}`;
    }
    if (chapList) {
      chapList.innerHTML = chapters.map((c, idx) =>
        `<button class="reader__chap-item${idx === currentChapterIndex ? ' on' : ''}" data-idx="${idx}">${escapeHtml(c.name || ('第' + (idx + 1) + '章'))}</button>`
      ).join('');
      chapList.querySelectorAll('.reader__chap-item').forEach(b => {
        const idx = parseInt(b.dataset.idx, 10);
        // 悬停即预载该章页码 + 前 N 张图，直接跳章也不再卡
        b.addEventListener('mouseenter', () => preloadChapter(idx).catch(() => {}));
        b.addEventListener('click', async () => {
          if (idx !== currentChapterIndex && await loadChapter(idx)) renderPage(0);
          if (chapSide) chapSide.classList.remove('open');
        });
      });
    }
    if (prevChapBtn) prevChapBtn.disabled = currentChapterIndex <= 0;
    if (nextChapBtn) nextChapBtn.disabled = currentChapterIndex >= chapters.length - 1;
  }

  // ── 并发受限的图片预加载：一次最多 4 张并行，既快速备齐又不阻塞网络 ──
  const PRELOAD_CONCUR = 4;
  async function preloadImages(cid, pageList, count) {
    const slice = pageList.slice(0, Math.max(0, count));
    const cache = preloadCache[cid] || (preloadCache[cid] = { pages: [], images: new Set() });
    let cursor = 0;
    async function worker() {
      while (cursor < slice.length) {
        const p = slice[cursor++];
        if (cache.images.has(p)) continue;
        await new Promise(resolve => {
          const img = new Image();
          img.onload = img.onerror = () => resolve();
          img.src = `/api/comic/${jmId}/page/${p}?chapter=${encodeURIComponent(cid)}`;
          cache.images.add(p);
        });
      }
    }
    const workers = [];
    for (let w = 0; w < Math.min(PRELOAD_CONCUR, slice.length); w++) workers.push(worker());
    await Promise.all(workers);
  }

  // 拉取某章页码列表（带缓存），切换章节时跳过 API 往返
  async function fetchChapterPages(index) {
    if (index < 0 || index >= chapters.length) return null;
    const cid = chapters[index].id;
    const c = preloadCache[cid];
    if (c && c.pages.length) return c.pages;
    const r = await api('GET', `/api/read/${jmId}/chapter/${encodeURIComponent(cid)}`);
    if (!r.success) return null;
    const cp = Array.isArray(r.data.current_chapter_pages) ? r.data.current_chapter_pages : [];
    (preloadCache[cid] || (preloadCache[cid] = { pages: [], images: new Set() })).pages = cp;
    return cp;
  }

  async function loadChapter(index) {
    if (index < 0 || index >= chapters.length) return false;
    const targetCid = chapters[index].id;
    const cached = preloadCache[targetCid];
    if (cached && cached.pages.length) {
      currentChapterIndex = index;
      currentChapter = targetCid;
      pages = cached.pages;
    } else {
      const r = await api('GET', `/api/read/${jmId}/chapter/${encodeURIComponent(targetCid)}`);
      if (!r.success) { toast('章节加载失败', 'error'); return false; }
      pages = Array.isArray(r.data.current_chapter_pages) ? r.data.current_chapter_pages : [];
      const slot = preloadCache[targetCid] || (preloadCache[targetCid] = { pages: [], images: new Set() });
      slot.pages = pages;
      currentChapterIndex = index;
      currentChapter = targetCid;
    }
    updateChapterUI();
    // 进入新章节后，立刻备好「下一章」页码列表（极轻量），让下次切换零 API 等待
    const nextIdx = index + 1;
    if (nextIdx < chapters.length) fetchChapterPages(nextIdx).catch(() => {});
    return true;
  }

  async function preloadChapter(index) {
    if (index < 0 || index >= chapters.length) return;
    const pagesList = await fetchChapterPages(index);
    if (!pagesList || !pagesList.length) return;
    await preloadImages(chapters[index].id, pagesList, preloadSettings.preloadCount);
  }
  function shouldPreload() {
    if (!preloadSettings.enabled || !pages.length) return false;
    const nextIdx = currentChapterIndex + 1;
    if (nextIdx >= chapters.length) return false;
    const c = preloadCache[chapters[nextIdx].id];
    if (c && c.images.size >= Math.min(preloadSettings.preloadCount, c.pages.length || preloadSettings.preloadCount)) return false;
    const remaining = pages.length - current - 1;
    const ratio = ((current + 1) / pages.length) * 100;
    return remaining <= preloadSettings.thresholdPages || ratio >= preloadSettings.thresholdRatio;
  }
  function maybePreload() {
    if (!shouldPreload()) return;
    preloadChapter(currentChapterIndex + 1).catch(() => {});
  }

  function showBoundaryToast(msg) {
    if (!chapterToastEnabled) return;
    toastClosable(msg, 'info', 3000);
  }

  async function renderPage(i) {
    // 翻到上一章最后一页
    if (i < 0) {
      if (currentChapterIndex > 0) {
        if (await loadChapter(currentChapterIndex - 1)) renderPage(pages.length - 1);
      } else {
        showBoundaryToast('已经是第一章了');
      }
      return;
    }
    // 翻到下一章第一页
    if (i >= pages.length) {
      if (currentChapterIndex < chapters.length - 1) {
        if (await loadChapter(currentChapterIndex + 1)) renderPage(0);
      } else {
        showBoundaryToast('已经是最后一章了');
      }
      return;
    }
    current = i;
    maybePreload(); // 翻页时检查是否预加载下一章
    stage.scrollTop = 0;
    stage.innerHTML = '';
    const spinner = document.createElement('div');
    spinner.className = 'reader__loading';
    spinner.innerHTML = '<i class="fas fa-spinner"></i>';
    stage.appendChild(spinner);
    const img = document.createElement('img');
    img.className = 'reader__img';
    img.style.width = (100 * zoom) + '%';
    img.alt = `第 ${current + 1} 页`;
    img.style.maxHeight = (100 * zoom) + '%';
    img.onload = () => { if (spinner.parentNode) spinner.remove(); };
    img.src = `/api/comic/${jmId}/page/${pages[i]}?chapter=${encodeURIComponent(currentChapter)}`;
    img.onerror = () => {
      stage.innerHTML = `<div class="empty"><div class="empty__icon"><i class="fas fa-image"></i></div><h3>加载失败</h3><p>章节 ${escapeHtml(currentChapter)} · 第 ${current + 1} 页</p></div>`;
    };
    stage.appendChild(img);
    document.getElementById('pageInfo').textContent = `${current + 1} / ${pages.length}`;
  }
  function zoomBy(d) { zoom = Math.min(3, Math.max(0.5, zoom + d)); zoomVal.textContent = Math.round(zoom * 100) + '%'; renderPage(current); }

  document.getElementById('prevBtn').onclick = () => renderPage(current - 1);
  document.getElementById('nextBtn').onclick = () => renderPage(current + 1);
  const pageInfoEl = document.getElementById('pageInfo');
  if (pageInfoEl) pageInfoEl.onclick = () => {
    const p = prompt(`跳转到第几页？（1-${pages.length}）`, String(current + 1));
    if (p === null) return;
    const n = parseInt(p, 10);
    if (isNaN(n) || n < 1 || n > pages.length) { toast(`请输入 1-${pages.length} 之间的页码`, 'warn'); return; }
    renderPage(n - 1);
  };
  if (prevChapBtn) prevChapBtn.onclick = () => { if (currentChapterIndex > 0) { loadChapter(currentChapterIndex - 1).then(() => renderPage(pages.length - 1)); } };
  if (nextChapBtn) nextChapBtn.onclick = () => { if (currentChapterIndex < chapters.length - 1) { loadChapter(currentChapterIndex + 1).then(() => renderPage(0)); } };
  document.getElementById('zoomIn').onclick = () => zoomBy(0.2);
  document.getElementById('zoomOut').onclick = () => zoomBy(-0.2);
  document.getElementById('closeReader').onclick = () => { history.length > 1 ? history.back() : location.href = '/library'; };

  // 阅读设置（预加载参数）
  const readerSettingsBtn = document.getElementById('readerSettings');
  if (readerSettingsBtn) readerSettingsBtn.onclick = () => {
    openModal({
      title: '阅读设置', sub: '预加载下一章可大幅减少换章卡顿',
      bodyHtml: `
        <div class="setting-row" style="justify-content:space-between">
          <span>启用章节预加载</span>
          <label class="switch"><input type="checkbox" id="rsPreloadEnabled" ${preloadSettings.enabled ? 'checked' : ''}><span class="track"></span></label>
        </div>
        <div class="setting-row" style="flex-direction:column;align-items:stretch;gap:8px;margin-top:12px">
          <span>剩余页数阈值（页）</span>
          <input type="number" id="rsPreloadPages" value="${preloadSettings.thresholdPages}" min="1" max="50" style="padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--surface-2)">
        </div>
        <div class="setting-row" style="flex-direction:column;align-items:stretch;gap:8px;margin-top:12px">
          <span>已读比例阈值（%）</span>
          <input type="number" id="rsPreloadRatio" value="${preloadSettings.thresholdRatio}" min="1" max="100" style="padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--surface-2)">
        </div>
        <div class="setting-row" style="flex-direction:column;align-items:stretch;gap:8px;margin-top:12px">
          <span>预加载页数（张）</span>
          <input type="number" id="rsPreloadCount" value="${preloadSettings.preloadCount}" min="1" max="50" style="padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--surface-2)">
        </div>
      `,
      actions: [
        { key: 'cancel', label: '取消', cls: 'btn--ghost', onClick: c => c() },
        { key: 'ok', label: '保存', cls: 'btn--primary', onClick: c => {
          preloadSettings.enabled = document.getElementById('rsPreloadEnabled').checked;
          preloadSettings.thresholdPages = Math.max(1, parseInt(document.getElementById('rsPreloadPages').value, 10) || 5);
          preloadSettings.thresholdRatio = Math.max(1, Math.min(100, parseInt(document.getElementById('rsPreloadRatio').value, 10) || 25));
          preloadSettings.preloadCount = Math.max(1, parseInt(document.getElementById('rsPreloadCount').value, 10) || 10);
          Settings.set('reader_preload_enabled', preloadSettings.enabled);
          Settings.set('reader_preload_threshold_pages', preloadSettings.thresholdPages);
          Settings.set('reader_preload_threshold_ratio', preloadSettings.thresholdRatio);
          Settings.set('reader_preload_count', preloadSettings.preloadCount);
          toast('阅读设置已保存', 'success');
          c();
        }}
      ]
    });
  };

  // 半隐藏章节侧栏：鼠标靠近左侧触发条（靠中，避开左上角返回键）才滑出
  if (chapTrigger || chapSide) {
    const openChap = () => chapSide && chapSide.classList.add('open');
    const closeChap = () => { if (chapSide && !chapSide.matches(':hover')) chapSide.classList.remove('open'); };
    function inTrigger(e) {
      if (!chapTrigger) return false;
      const r = chapTrigger.getBoundingClientRect();
      return e.clientX >= r.left - 8 && e.clientX <= r.right + 8 && e.clientY >= r.top - 8 && e.clientY <= r.bottom + 8;
    }
    document.addEventListener('mousemove', (e) => {
      if (chapSide && chapSide.matches(':hover')) return;
      if (inTrigger(e)) openChap(); else closeChap();
    });
    if (chapSide) chapSide.addEventListener('mouseleave', closeChap);
    if (chapTrigger) { chapTrigger.addEventListener('mouseenter', openChap); chapTrigger.addEventListener('click', () => chapSide && chapSide.classList.toggle('open')); }
  }

  document.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft') renderPage(current - 1);
    if (e.key === 'ArrowRight') renderPage(current + 1);
    if (e.key === 'Escape') document.getElementById('closeReader').click();
  });
}

/* ═══════════════════════════════════════════════════════
   下载进度页
   ═══════════════════════════════════════════════════════ */
function initDownloads() {
  const activeWrap = document.getElementById('activeList');
  const histWrap = document.getElementById('historyList');

  function statusBadge(s) {
    const map = {
      starting: ['等待中', 'wait'], downloading: ['下载中', 'run'],
      processing: ['处理中', 'spin'], completed: ['已完成', 'ok'],
      downloaded: ['已完成', 'ok'], error: ['失败', 'err'], canceled: ['已取消', 'err'],
    };
    const [label, kind] = map[s] || ['未知', 'wait'];
    const icon = kind === 'run' ? 'fa-spinner' : kind === 'spin' ? 'fa-arrows-rotate'
      : kind === 'ok' ? 'fa-circle-check' : kind === 'err' ? 'fa-circle-xmark' : 'fa-clock';
    return `<span class="dl-badge dl-badge--${kind}"><i class="fas ${icon}"></i> ${label}</span>`;
  }

  function renderActive(map) {
    const items = Object.entries(map || {}).map(([id, d]) => ({ id, ...d }))
      // 已完成/已下载的任务已写入历史，不应停留在"进行中"区域
      .filter(d => d.status !== 'completed' && d.status !== 'downloaded');
    if (!items.length) {
      activeWrap.innerHTML = `<div class="empty"><div class="empty__icon"><i class="fas fa-cloud-arrow-down"></i></div>
        <h3>暂无进行中的下载</h3><p>去「搜索」下载漫画，进度会实时显示在这里</p></div>`;
      return;
    }
    activeWrap.innerHTML = items.map(d => {
      const pct = Math.max(0, Math.min(100, d.progress || 0));
      const title = d.title || ('JM-' + (d.jm_id || ''));
      const isError = d.status === 'error' || d.status === 'canceled';
      return `<div class="dl-card ${isError ? 'dl-card--error' : ''}" data-dlid="${d.id}">
        <div class="dl-card__head">
          <div class="dl-card__title">${escapeHtml(title)}</div>
          <div class="dl-card__badges">${statusBadge(d.status)}${isError ? `<button class="dl-card__del" data-dlid="${d.id}" title="从列表移除"><i class="fas fa-trash"></i></button>` : ''}</div>
        </div>
        <div class="dl-card__msg">${escapeHtml(d.message || '')}</div>
        <div class="dl-progress"><div class="dl-progress__fill" style="width:${pct}%"></div></div>
        <div class="dl-card__meta"><span>${pct}%</span><span>${isError ? '出错/已取消' : '处理中…'}</span></div>
      </div>`;
    }).join('');
    activeWrap.querySelectorAll('.dl-card__del').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const dlid = btn.dataset.dlid;
        if (!confirm('确认从进行中列表移除该任务？')) return;
        const r = await api('DELETE', `/api/download/progress/${dlid}`);
        if (r.success) { toast('已移除', 'success'); tick(); }
        else toast(r.message || '移除失败', 'error');
      });
    });
  }

  function renderHistory(list) {
    const countEl = document.getElementById('historyCount');
    if (countEl) countEl.textContent = list && list.length ? `(${list.length})` : '';
    if (!list || !list.length) {
      histWrap.innerHTML = `<div class="empty"><div class="empty__icon"><i class="fas fa-clock-rotate-left"></i></div>
        <h3>还没有下载记录</h3></div>`;
      return;
    }
    histWrap.innerHTML = list.map(d => {
      const ok = d.download_status === 'completed';
      const missing = ok && d.files_exist === false;
      return `<div class="dl-hist ${ok ? (missing ? 'dl-hist--missing' : 'dl-hist--ok') : 'dl-hist--err'}" data-rid="${d.id}">
        <div class="dl-hist__icon"><i class="fas ${ok ? (missing ? 'fa-triangle-exclamation' : 'fa-circle-check') : 'fa-circle-xmark'}"></i></div>
        <div class="dl-hist__main">
          <div class="dl-hist__title">${escapeHtml(d.title || ('JM-' + d.jm_id))}${missing ? ' <span class="dl-hist__missing-tag">已丢失</span>' : ''}</div>
          <div class="dl-hist__sub">${escapeHtml(d.download_time || '')} · ${ok ? (missing ? '文件已被删除' : '完成') : '失败'}
            ${d.error_message ? (' · ' + escapeHtml(d.error_message)) : ''}</div>
        </div>
        ${missing ? `<button class="dl-hist__redl" data-jmid="${d.jm_id}" title="重新下载"><i class="fas fa-download"></i></button>` : ''}
        <button class="dl-hist__del" data-rid="${d.id}" title="删除该记录"><i class="fas fa-trash"></i></button>
      </div>`;
    }).join('');
    histWrap.querySelectorAll('.dl-hist__redl').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const jmId = parseInt(btn.dataset.jmid);
        if (!confirm('确认重新下载该漫画？将下载到原来的分类。')) return;
        reDownloadFromHistory(jmId);
      });
    });
    histWrap.querySelectorAll('.dl-hist__del').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const rid = parseInt(btn.dataset.rid);
        if (!confirm('确认删除该条下载记录？')) return;
        const r = await api('DELETE', `/api/download/history/${rid}`);
        if (r.success) { toast('已删除', 'success'); loadHistory(); }
        else toast(r.message || '删除失败', 'error');
      });
    });
  }

  async function tick() {
    try {
      const r = await api('GET', '/api/download/progress');
      if (r.success) renderActive(r.data);
    } catch (e) {}
  }
  async function loadHistory() {
    try {
      const r = await api('GET', '/api/download/history');
      if (r.success) renderHistory(r.data);
    } catch (e) {}
  }

  loadHistory();
  tick();
  const timer = setInterval(tick, 1500);
  window.addEventListener('beforeunload', () => clearInterval(timer));

  // 清空全部历史
  const clearBtn = document.getElementById('clearHistoryBtn');
  if (clearBtn) clearBtn.addEventListener('click', async () => {
    if (!confirm('确认清空所有下载记录？此操作不可撤销。')) return;
    const r = await api('DELETE', '/api/download/history');
    if (r.success) { toast(r.message || '已清空', 'success'); loadHistory(); }
    else toast(r.message || '清空失败', 'error');
  });
  // 批量下载入口放在下载管理页
  initBatchBar();
}

function applyGlobalSettings() {
  api('GET', '/api/settings').then(r => {
    if (!r.success) return;
    const s = r.data;
    const hidePC = s.hide_page_chapter === '1' || s.hide_page_chapter === true || s.hide_page_chapter === 'true';
    document.body.classList.toggle('hide-page-chapter', hidePC);
  });
}

/* ═══════════════════════════════════════════════════════
   启动路由
   ═══════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  applyGlobalSettings();
  document.querySelectorAll('.theme-toggle').forEach(tg => {
    Theme.sync(tg);
    tg.onclick = () => { Theme.toggle(); Theme.save(); };
  });
  setupNav();
  attachRipple();

  const p = location.pathname;
  if (p.startsWith('/downloads')) initDownloads();
  else if (p.startsWith('/reader/')) initReader();
  else if (p.startsWith('/library')) initLibrary();
  else if (p.startsWith('/settings')) initSettings();
  else if (p.startsWith('/home')) initHome();
  else initSearch();

  // 全局可拖动返回键
  const globalBack = document.getElementById('globalBack');
  if (globalBack) {
    // 阅读器页面已有独立返回键，全局返回键隐藏避免重复
    if (p.startsWith('/reader/')) { globalBack.style.display = 'none'; }
    else {
      try {
        const pos = JSON.parse(localStorage.getItem('jm_global_back_pos') || '{}');
        if (pos.x != null) { globalBack.style.left = pos.x + 'px'; globalBack.style.top = pos.y + 'px'; }
      } catch (_) {}
      let dragging = false, ox = 0, oy = 0;
      globalBack.addEventListener('pointerdown', e => {
        if (e.button !== 0) return;
        dragging = true;
        globalBack.classList.add('dragging');
        const r = globalBack.getBoundingClientRect();
        ox = e.clientX - r.left; oy = e.clientY - r.top;
        globalBack.setPointerCapture(e.pointerId);
      });
      globalBack.addEventListener('pointermove', e => {
        if (!dragging) return;
        e.preventDefault();
        const x = Math.max(0, Math.min(window.innerWidth - 38, e.clientX - ox));
        const y = Math.max(0, Math.min(window.innerHeight - 38, e.clientY - oy));
        globalBack.style.left = x + 'px'; globalBack.style.top = y + 'px';
      });
      const endDrag = (e) => {
        if (!dragging) return;
        dragging = false;
        globalBack.classList.remove('dragging');
        try {
          const r = globalBack.getBoundingClientRect();
          localStorage.setItem('jm_global_back_pos', JSON.stringify({ x: r.left, y: r.top }));
        } catch (_) {}
        if (e.type === 'pointerup') {
          // 真正点击（没拖动或拖动很小）→ 返回
          const r = globalBack.getBoundingClientRect();
          const dx = e.clientX - r.left - ox, dy = e.clientY - r.top - oy;
          if (Math.abs(dx) < 4 && Math.abs(dy) < 4) {
            if (history.length > 1) history.back(); else location.href = '/search';
          }
        }
      };
      globalBack.addEventListener('pointerup', endDrag);
      globalBack.addEventListener('pointercancel', endDrag);
    }
  }
});
