"""In-page geometry/visual probes. These give the VLM hard evidence (and give the
self-healing loop an objective 'is it really fixed?' check) without any hardcoded app selectors."""

PROBE_JS = r"""
() => {
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  const sx = window.scrollX, sy = window.scrollY;
  const signals = [];

  const unique = (s) => { try { return document.querySelectorAll(s).length === 1; } catch (e) { return false; } };
  const cssPath = (el) => {
    const parts = []; let n = el;
    while (n && n.nodeType === 1 && n !== document.documentElement) {
      let p = n.tagName.toLowerCase();
      if (n.id) { parts.unshift('#' + CSS.escape(n.id)); break; }
      const cls = n === document.body ? '' : [...n.classList].slice(0, 2).map(c => '.' + CSS.escape(c)).join('');
      p += cls;
      const sibs = n.parentElement ? [...n.parentElement.children].filter(s => s.tagName === n.tagName) : [];
      if (sibs.length > 1) p += ':nth-of-type(' + (sibs.indexOf(n) + 1) + ')';
      parts.unshift(p); n = n.parentElement;
    }
    return parts.join(' > ');
  };
  const sel = (el) => {
    if (el.id && unique('#' + CSS.escape(el.id))) return '#' + CSS.escape(el.id);
    const t = el.getAttribute('data-testid');
    if (t && unique('[data-testid="' + t + '"]')) return '[data-testid="' + t + '"]';
    return cssPath(el);
  };
  const pageBox = (r) => ({x: Math.max(0, r.left + sx), y: Math.max(0, r.top + sy), w: r.width, h: r.height});
  const label = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().slice(0, 60);

  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', {willReadFrequently: true}); const ccache = {};
  const rgba = (c) => {
    if (ccache[c]) return ccache[c];
    cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = c; cx.fillRect(0, 0, 1, 1);
    const d = cx.getImageData(0, 0, 1, 1).data;
    return (ccache[c] = [d[0], d[1], d[2], d[3] / 255]);
  };
  const lum = (c) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const blend = (fg, bg) => [0, 1, 2].map(i => fg[i] * fg[3] + bg[i] * (1 - fg[3]));
  const bgOf = (el) => {
    let n = el;
    while (n && n.nodeType === 1) {
      const cs = getComputedStyle(n);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;
      const c = rgba(cs.backgroundColor); if (c[3] > 0.95) return c.slice(0, 3);
      n = n.parentElement;
    }
    return [255, 255, 255];
  };
  const hex = (c) => '#' + c.map(v => Math.round(v).toString(16).padStart(2, '0')).join('');

  // ---- page-level horizontal overflow ----
  const sw = document.documentElement.scrollWidth;
  let widest = null, widestR = 0;
  document.querySelectorAll('body *').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > widestR && getComputedStyle(el).position !== 'fixed') { widestR = r.right; widest = el; }
  });
  if (sw > vw + 1 && widest) {
    signals.push({type: 'overflow', selector: sel(widest), bbox: pageBox(widest.getBoundingClientRect()),
      detail: {scrollWidth: sw, viewportWidth: vw, overflowPx: sw - vw}});
  }

  // ---- interactive elements: clipped / hidden / obscured ----
  const inter = [...document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, [role=button], summary')].slice(0, 80);
  const startY = window.scrollY;
  inter.forEach(el => {
    const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
    const name = label(el);
    const isSubmit = el.matches('button[type=submit], input[type=submit], form button:not([type])');
    if (cs.display === 'none') {
      const form = el.closest('form');
      if (isSubmit && form && form.getBoundingClientRect().width > 0)
        signals.push({type: 'hidden_element', selector: sel(el), bbox: pageBox(form.getBoundingClientRect()), text: name, detail: {reason: 'display:none on submit control of a visible form'}});
      return;
    }
    if (el.closest('[hidden], [aria-hidden=true]')) return;
    const invisible = cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.05 || r.width < 4 || r.height < 4;
    if (invisible && (isSubmit || name)) {
      const ref = el.parentElement ? el.parentElement.getBoundingClientRect() : r;
      signals.push({type: 'hidden_element', selector: sel(el), bbox: pageBox(ref), text: name,
        detail: {reason: 'control is in the DOM but not visible', opacity: cs.opacity, visibility: cs.visibility, width: Math.round(r.width), height: Math.round(r.height)}});
      return;
    }
    // horizontal clipping against the viewport and clipping ancestors
    let L = r.left, R = r.right;
    let a = el.parentElement;
    while (a && a !== document.documentElement) {
      const acs = getComputedStyle(a);
      if (acs.overflowX !== 'visible') { const ar = a.getBoundingClientRect(); L = Math.max(L, ar.left); R = Math.min(R, ar.right); }
      a = a.parentElement;
    }
    const visW = Math.max(0, Math.min(R, vw) - Math.max(L, 0));
    if (r.width > 0 && visW < r.width * 0.98 && cs.position !== 'fixed') {
      signals.push({type: 'clipping', selector: sel(el), bbox: pageBox(r), text: name,
        detail: {clippedPx: Math.round(r.width - visW), right: Math.round(r.right), left: Math.round(r.left), viewportWidth: vw, visibleFraction: +(visW / r.width).toFixed(2)}});
      return;
    }
    // obscured (something else covers its centre)
    el.scrollIntoView({block: 'center', inline: 'nearest'});
    const r2 = el.getBoundingClientRect();
    const px = Math.min(Math.max(r2.left + r2.width / 2, 0), vw - 1), py = Math.min(Math.max(r2.top + r2.height / 2, 0), vh - 1);
    const top = document.elementFromPoint(px, py);
    if (top && top !== el && !el.contains(top) && !top.contains(el)) {
      signals.push({type: 'hidden_element', selector: sel(el), other_selector: sel(top), bbox: pageBox(r2), text: name,
        detail: {reason: 'obscured by another element', coveredBy: sel(top)}});
    }
  });
  window.scrollTo(sx, startY);

  // ---- text elements: overlap + contrast ----
  const texts = [];
  document.querySelectorAll('body *').forEach(el => {
    if (texts.length >= 400) return;
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'PATH'].includes(el.tagName.toUpperCase())) return;
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 0);
    if (!own) return;
    const cs = getComputedStyle(el), r = el.getBoundingClientRect();
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.05 || r.width < 2 || r.height < 2) return;
    texts.push({el, r, cs});
  });
  for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++) {
    const A = texts[i], B = texts[j];
    if (A.el.contains(B.el) || B.el.contains(A.el)) continue;
    const ix = Math.min(A.r.right, B.r.right) - Math.max(A.r.left, B.r.left);
    const iy = Math.min(A.r.bottom, B.r.bottom) - Math.max(A.r.top, B.r.top);
    if (ix <= 2 || iy <= 2) continue;
    const frac = (ix * iy) / Math.min(A.r.width * A.r.height, B.r.width * B.r.height);
    if (frac > 0.25) signals.push({type: 'overlap', selector: sel(A.el), other_selector: sel(B.el),
      bbox: pageBox({left: Math.min(A.r.left, B.r.left), top: Math.min(A.r.top, B.r.top), width: Math.max(A.r.right, B.r.right) - Math.min(A.r.left, B.r.left), height: Math.max(A.r.bottom, B.r.bottom) - Math.min(A.r.top, B.r.top)}),
      text: label(A.el) + ' / ' + label(B.el), detail: {overlapFraction: +frac.toFixed(2)}});
  }
  texts.forEach(({el, r, cs}) => {
    const bg = bgOf(el); if (!bg) return;
    const fg = rgba(cs.color); const fgc = blend(fg, bg);
    const L1 = lum(fgc), L2 = lum(bg); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const px = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
    const need = (px >= 24 || (px >= 18.66 && bold)) ? 3 : 4.5;
    if (ratio < need) signals.push({type: 'contrast', selector: sel(el), bbox: pageBox(r), text: label(el),
      detail: {ratio: +ratio.toFixed(2), required: need, color: hex(fgc), background: hex(bg)}});
  });

  return {meta: {url: location.href, title: document.title, vw, vh, scrollW: sw, scrollH: document.documentElement.scrollHeight}, signals};
}
"""

DOM_EXCERPT_JS = r"""
(args) => {
  const {selectors, limit, each} = args;
  const strip = (el) => { const c = el.cloneNode(true); c.querySelectorAll('script,style,svg,noscript,link,meta').forEach(n => n.remove());
    c.querySelectorAll('*').forEach(n => { [...n.attributes].forEach(a => { if (a.name.startsWith('data-') && a.name !== 'data-testid') n.removeAttribute(a.name); if (a.name === 'style' && a.value.length > 200) n.setAttribute('style', a.value.slice(0, 200)); }); });
    return c; };
  const focus = [];
  for (const s of selectors) { try { const el = document.querySelector(s); if (el) focus.push({selector: s, html: strip(el).outerHTML.slice(0, each)}); } catch (e) {} }
  const page = strip(document.body).outerHTML.replace(/\s+/g, ' ').slice(0, limit);
  return {focus, page};
}
"""
