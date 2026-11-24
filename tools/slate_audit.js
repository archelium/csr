/* Layout audit for the Kestrel slate, dev only (served by tools/slate_dev.py at /slate_audit.js).
   Same checks as the design mock's audit: frame overflow, sideways scroll, unwanted vertical scroll,
   panel clipping/spill, cut or spilling text, overlapping text, small tap targets, tiny fonts.
   The page sizes itself from the real window: set the viewport, load /live, then run
   auditLive({touch: true}) and read the grouped report it returns. */
(function () {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const vis = e => { const r = e.getBoundingClientRect(); if (!r.width || !r.height) return false; const cs = getComputedStyle(e); return cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity !== 0 };
  const label = e => { if (!e || !e.tagName) return '?'; let s = e.id ? '#' + e.id : ''; const c = typeof e.className === 'string' ? e.className : (e.className && e.className.baseVal) || ''; if (c.trim()) s += '.' + c.trim().split(/\s+/).slice(0, 2).join('.'); return s || e.tagName.toLowerCase() };
  const path = e => { const out = []; let p = e; for (let i = 0; i < 3 && p && p.id !== 'app'; i++, p = p.parentElement) out.unshift(label(p)); return out.join(' > ') };
  const clipsX = cs => cs.overflowX !== 'visible';
  const clipsY = cs => cs.overflowY !== 'visible';
  const isTransparent = c => !c || c === 'transparent' || /rgba\(.*,\s*0\)$/.test(c);
  const visibleBox = (e, cs) => (parseFloat(cs.borderTopWidth) > 0 && !isTransparent(cs.borderTopColor)) || (parseFloat(cs.borderLeftWidth) > 0 && !isTransparent(cs.borderLeftColor)) || !isTransparent(cs.backgroundColor) || (cs.backgroundImage && cs.backgroundImage !== 'none');
  const inter = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));

  function textItems(app, k) {
    // every visible text run (HTML text nodes + SVG text), with its rect clipped by scroll containers
    const items = [];
    const w = document.createTreeWalker(app, NodeFilter.SHOW_TEXT);
    const rng = document.createRange();
    let n;
    while ((n = w.nextNode())) {
      if (!n.nodeValue.trim()) continue;
      const el = n.parentElement; if (!el) continue;
      if (el.closest('#sdBanner,#toasts,#bellPanel,#ticker,.mapwrap.narrow .dist,.dhandle')) continue;
      const svgText = el.closest('svg');
      const host = svgText ? (el.closest('text') || el) : el;
      if (!vis(host)) continue;
      let r;
      if (svgText) r = host.getBoundingClientRect(); else { rng.selectNodeContents(n); r = rng.getBoundingClientRect() }
      if (!r.width || !r.height) continue;
      items.push({ n, el: host, svg: !!svgText, r: { left: r.left, right: r.right, top: r.top, bottom: r.bottom }, t: n.nodeValue.trim().slice(0, 40) });
    }
    return items;
  }

  function checkPage(key, ctx) {
    const issues = [];
    const add = (type, where, txt) => issues.push({ type, where, txt: txt || '', key });
    const app = document.querySelector('#app'), A = app.getBoundingClientRect(), k = A.width / app.offsetWidth;
    const pg = document.querySelector('.page.on');
    // 1. outside the frame sideways
    for (const e of app.querySelectorAll('*')) {
      if (e.closest('svg') && e.tagName !== 'svg') continue;
      if (e.closest('#toasts,#bellPanel,#ticker')) continue;
      if (!vis(e)) continue;
      const r = e.getBoundingClientRect();
      if (r.right > A.right + 2 || r.left < A.left - 2) {
        let p = e.parentElement, clipped = false;
        while (p && p !== app) { if (clipsX(getComputedStyle(p))) { const pr = p.getBoundingClientRect(); if (pr.right <= A.right + 2 && pr.left >= A.left - 2) { clipped = true; break } } p = p.parentElement }
        if (!clipped) { add('outside frame', path(e)); break }
      }
    }
    if (pg) {
      // 2. sideways scroll
      if (pg.scrollWidth > pg.clientWidth + 2) add('page scrolls sideways', label(pg), pg.scrollWidth + '/' + pg.clientWidth);
      // 3. pages that must fit without scrolling (tablet + desktop MAIN and NAV)
      if (!ctx.phone && (ctx.tab === 'ship' || ctx.tab === 'nav') && pg.scrollHeight > pg.clientHeight + 2) add('page scrolls vertically', label(pg), pg.scrollHeight + '/' + pg.clientHeight);
      // 4/5. panels
      for (const p of pg.querySelectorAll('.panel, .sidecol, .dmgp, .navside')) {
        if (!vis(p)) continue;
        const cs = getComputedStyle(p);
        if (cs.overflowY === 'hidden' && p.scrollHeight > p.clientHeight + 3) add('panel clips', path(p), p.scrollHeight + '/' + p.clientHeight);
        if (cs.overflowY === 'auto' && p.scrollHeight > p.clientHeight + 3 && !ctx.phone && !p.classList.contains('scrolls')) add('panel scrollbar', path(p), p.scrollHeight + '/' + p.clientHeight);
        if (cs.overflowY === 'visible') { const pr = p.getBoundingClientRect(); for (const c of p.children) { if (!vis(c)) continue; const r = c.getBoundingClientRect(); if (r.bottom > pr.bottom + 3 || r.right > pr.right + 3) { add('spills out of panel', path(c)); break } } }
      }
    }
    // text checks
    const items = textItems(app, k);
    const csCache = new Map(); const CS = e => { let c = csCache.get(e); if (!c) { c = getComputedStyle(e); csCache.set(e, c) } return c };
    const live = [];
    for (const it of items) {
      let r = { ...it.r }, hidden = false, cut = null, ell = false;
      for (let p = it.el; p && p !== app; p = p.parentElement) {
        if (it.svg && p.tagName !== 'svg' && p.closest('svg')) continue;
        const cs = CS(p);
        if (!clipsX(cs) && !clipsY(cs)) continue;
        const pr = p.getBoundingClientRect();
        // client area (inside borders)
        const box = { left: pr.left + p.clientLeft * k, top: pr.top + p.clientTop * k, right: pr.left + (p.clientLeft + p.clientWidth) * k, bottom: pr.top + (p.clientTop + p.clientHeight) * k };
        const scrolls = /auto|scroll/.test(cs.overflowX + cs.overflowY);
        const outX = clipsX(cs) && (r.left < box.left - 1 || r.right > box.right + 1);
        const outY = clipsY(cs) && (r.top < box.top - 1 || r.bottom > box.bottom + 1);
        const fullyOut = r.right <= box.left || r.left >= box.right || r.bottom <= box.top || r.top >= box.bottom;
        if (scrolls) { if (fullyOut) { hidden = true; break } r = { left: Math.max(r.left, box.left), right: Math.min(r.right, box.right), top: Math.max(r.top, box.top), bottom: Math.min(r.bottom, box.bottom) }; continue }
        if (fullyOut) { hidden = true; break }
        if ((outX || outY) && !cut) { cut = p; ell = CS(it.el).textOverflow === 'ellipsis' || cs.textOverflow === 'ellipsis' }
        r = { left: Math.max(r.left, box.left), right: Math.min(r.right, box.right), top: Math.max(r.top, box.top), bottom: Math.min(r.bottom, box.bottom) };
      }
      if (hidden) continue;
      if (cut) add(ell ? 'text ellipsised' : 'text cut', path(it.el) + ' in ' + label(cut), it.t);
      // spill: beyond the nearest visible box
      if (!it.svg && !ell) {
        for (let p = it.el; p && p !== app; p = p.parentElement) {
          const cs = CS(p);
          if (cs.display === 'inline') continue;
          if (!visibleBox(p, cs)) continue;
          const pr = p.getBoundingClientRect();
          if (r.right > pr.right + 1.5 * k || r.left < pr.left - 1.5 * k || r.bottom > pr.bottom + 1.5 * k) add('text spills out of box', path(it.el) + ' out of ' + label(p), it.t);
          break;
        }
        // tiny fonts (design px)
        const fs = parseFloat(CS(it.el).fontSize) * (+app.dataset.ds || 1);
        if (fs < 8.5) add('font under 8.5px', path(it.el), fs.toFixed(1) + 'px ' + it.t);
      }
      live.push({ ...it, r });
    }
    // overlapping text from different elements
    live.sort((a, b) => a.r.left - b.r.left);
    for (let i = 0; i < live.length; i++) {
      const a = live[i];
      for (let j = i + 1; j < live.length; j++) {
        const b = live[j];
        if (b.r.left >= a.r.right) break;
        if (a.el === b.el || a.el.contains(b.el) || b.el.contains(a.el)) continue;
        const ov = inter(a.r, b.r);
        const minA = Math.min((a.r.right - a.r.left) * (a.r.bottom - a.r.top), (b.r.right - b.r.left) * (b.r.bottom - b.r.top));
        if (ov > 4 * k * k && ov > 0.08 * minA) add(a.svg && b.svg ? 'map labels overlap' : 'text overlaps', path(a.el) + ' | ' + path(b.el), a.t + ' | ' + b.t);
      }
    }
    // small tap targets (design px), phone and tablet only
    if (ctx.touch) for (const b of app.querySelectorAll('button, select, [data-id], [data-go], [data-p], .seg')) {
      if (!vis(b) || b.closest('#sdBanner') === null && b.disabled) continue;
      const r = b.getBoundingClientRect(), ds = +app.dataset.ds || 1; let h = r.height / k, w = r.width / k;
      const af = getComputedStyle(b, '::after'); if (af.position === 'absolute' && af.content !== 'none') { const n = v => Math.max(0, -parseFloat(v) || 0); h += n(af.top) + n(af.bottom); w += n(af.left) + n(af.right) }
      h *= ds; w *= ds;
      if (h < 32 || w < 32) add('tap target under 32px', path(b), Math.round(w) + 'x' + Math.round(h) + ' ' + (b.textContent || '').trim().slice(0, 20));
    }
    return issues;
  }

  window.auditLive = function (o = {}) {
    const noAnim = document.createElement('style'); noAnim.textContent = '*{transition:none!important;animation:none!important}'; document.head.appendChild(noAnim);
    const raw = [], tabs = o.tabs || ['ship', 'sys', 'msn', 'feed', 'nav', 'ppl', 'you'];
    const views = [false].concat(o.cview && !foot ? [true] : []);
    try {
      for (const cv of views) {
        if (typeof CV !== 'undefined') { CV.on = cv; CV.manual = cv; CV.last = cv ? Date.now() : 0 }
        render();
        for (const tab of tabs) {
          if (cv && tab !== 'ship') continue;
          const b = document.querySelector(`#bezel .osb[data-p="${tab}"]`); if (b) b.click();
          const key = `${innerWidth}x${innerHeight} | ${seat}${cv ? ' cview' : ''} | ${tab}`;
          raw.push(...checkPage(key, { phone, tab, touch: !!o.touch }));
          // a value the stream didn't give must read —, never NaN / undefined / null
          for (const t of (document.querySelector('#app').innerText.match(/[^\n]*\b(NaN|undefined|null)\b[^\n]*/g) || []))
            raw.push({ type: 'bad value', where: tab, txt: t.trim().slice(0, 60), key });
        }
      }
    } finally { noAnim.remove(); if (typeof CV !== 'undefined') { CV.on = false; CV.manual = false } document.querySelector('#bezel .osb').click() }
    const g = {};
    for (const i of raw) { if (o.skip && o.skip.includes(i.type)) continue; const t = g[i.type] = g[i.type] || {}; const kk = i.where + ' :: ' + i.txt; const e = t[kk] = t[kk] || { n: 0, ex: [] }; e.n++; if (e.ex.length < 2) e.ex.push(i.key) }
    const rep = {};
    for (const [type, m] of Object.entries(g)) { const arr = Object.entries(m).sort((x, y) => y[1].n - x[1].n); rep[type] = arr.slice(0, o.top || 10).map(([w, v]) => `${v.n}x ${w} [${v.ex.join(' / ')}]`) }
    return rep;
  };
})();
