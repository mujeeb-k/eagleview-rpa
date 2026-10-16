// Eagleview's in-page library. One expression that evaluates to an object of functions; it is
// inlined into every evaluate call, so nothing is installed on the page and the page cannot
// tamper with it between calls. Read-only except for scrolling an element into view.
//
// Coordinates: "local" is the element's own frame viewport; "top" is the top-level viewport,
// which is screenshot pixel space (1280x800 at scale 1).
(() => {
  const norm = (s) =>
    (s || "").normalize("NFKC").replace(/ /g, " ").replace(/\s+/g, " ").trim();
  const labelKey = (s) => norm(s).replace(/:+$/, "").trim().toLowerCase();

  // What a click "means": the nearest ancestor-or-self a person would say they clicked.
  const ACTIONABLE = [
    "a[href]", "button", "input", "select", "textarea", "option", "summary", "[onclick]",
    "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]", "[role=tab]",
    "[role=menuitem]", "[role=option]", "[role=combobox]", "[role=textbox]",
  ].join(",");

  const CONTROLS = {
    textbox:
      "input:not([type]),input[type=text],input[type=password],input[type=search]," +
      "input[type=email],input[type=tel],input[type=number],input[type=url],textarea",
    button: "button,input[type=button],input[type=submit],input[type=reset],input[type=image]",
    link: "a[href]",
    select: "select",
    checkbox: "input[type=checkbox]",
    radio: "input[type=radio]",
    image: "img,input[type=image]",
  };
  CONTROLS.any = Object.values(CONTROLS).join(",");

  function framePath(win) {
    const out = [];
    let w = win;
    while (w.frameElement) {
      const fe = w.frameElement;
      let name = fe.name || fe.getAttribute("name");
      if (!name) {
        const siblings = Array.from(w.parent.frames);
        name = "#" + siblings.indexOf(w);
      }
      out.unshift(name);
      w = w.parent;
    }
    return out;
  }

  // Top-level viewport position of a frame's viewport origin.
  function offset(win) {
    let x = 0, y = 0, w = win;
    while (w.frameElement) {
      const fe = w.frameElement;
      const r = fe.getBoundingClientRect();
      x += r.left + fe.clientLeft;
      y += r.top + fe.clientTop;
      w = w.parent;
    }
    return { x, y };
  }

  function rendered(el) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const st = el.ownerDocument.defaultView.getComputedStyle(el);
    return st.visibility !== "hidden" && st.display !== "none";
  }

  function firstRect(el) {
    for (const r of el.getClientRects()) if (r.width > 0 && r.height > 0) return r;
    return null;
  }

  function inViewport(el) {
    const r = el.getBoundingClientRect();
    const w = el.ownerDocument.defaultView;
    return r.top >= 0 && r.left >= 0 && r.bottom <= w.innerHeight && r.right <= w.innerWidth;
  }

  // The hit test behind every visibility claim. The point (local coordinates of el's frame) must
  // land on el, or inside it, and every enclosing frame element must itself be topmost at that
  // point in its parent document. A fake modal in any frame on the path hides it. A point outside
  // a frame's viewport is not visible: callers scroll first when they need an off-screen element.
  // mode "text": el is a text node's parent, and an ancestor of el under the point also counts
  // (nothing foreign is on top; the text just doesn't take pointer events itself).
  function hitsAt(el, lx, ly, mode) {
    const doc = el.ownerDocument;
    let w = doc.defaultView;
    if (lx < 0 || ly < 0 || lx >= w.innerWidth || ly >= w.innerHeight) return false;
    const hit = doc.elementFromPoint(lx, ly);
    if (!hit) return false;
    if (!(hit === el || el.contains(hit) || (mode === "text" && hit.contains(el)))) return false;
    let x = lx, y = ly;
    while (w.frameElement) {
      const fe = w.frameElement;
      const r = fe.getBoundingClientRect();
      x += r.left + fe.clientLeft;
      y += r.top + fe.clientTop;
      const pw = w.parent;
      if (x < 0 || y < 0 || x >= pw.innerWidth || y >= pw.innerHeight) return false;
      if (pw.document.elementFromPoint(x, y) !== fe) return false;
      w = pw;
    }
    return true;
  }

  function topBox(el, r) {
    r = r || el.getBoundingClientRect();
    const o = offset(el.ownerDocument.defaultView);
    return {
      x: Math.round(r.left + o.x), y: Math.round(r.top + o.y),
      width: Math.round(r.width), height: Math.round(r.height),
    };
  }

  // Where to put the pointer for el: the centre of its first box, as an integer top-level point,
  // and whether el is topmost at exactly that point.
  function pointFor(el, scroll) {
    if (!el.isConnected) return { connected: false };
    if (scroll && !inViewport(el)) el.scrollIntoView({ block: "center", inline: "nearest" });
    const r = firstRect(el);
    if (!r) return { connected: true, rendered: false };
    const o = offset(el.ownerDocument.defaultView);
    const x = Math.floor(r.left + r.width / 2 + o.x);
    const y = Math.floor(r.top + r.height / 2 + o.y);
    return {
      connected: true, rendered: true, x, y,
      topmost: hitsAt(el, x - o.x, y - o.y, "element"),
    };
  }

  // Is el topmost at a top-level point right now? The last check before the mouse moves.
  function topmostAt(el, x, y) {
    if (!el.isConnected) return false;
    const o = offset(el.ownerDocument.defaultView);
    return hitsAt(el, x - o.x, y - o.y, "element");
  }

  function climb(el) {
    if (el && el.nodeType !== 1) el = el.parentElement;
    let e = el;
    while (e && e.nodeType === 1 && e.tagName !== "BODY" && e.tagName !== "HTML") {
      if (e.matches(ACTIONABLE)) return e;
      e = e.parentElement;
    }
    return el;
  }

  function ownText(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === "input") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (type === "button" || type === "submit" || type === "reset") {
        return norm(el.value || (type === "submit" ? "Submit" : type === "reset" ? "Reset" : ""));
      }
      return "";
    }
    if (tag === "select" || tag === "textarea" || tag === "img") return "";
    return norm(el.innerText);
  }

  // Do two resolved elements denote the same target? The same node; or both inside the same
  // actionable element (a <font> inside the <a> it labels); or one is a pure wrapper of the other
  // (a <td> whose only text is the <font> inside it).
  function sameTarget(a, b) {
    if (!a || !b) return false;
    if (a === b) return true;
    if (a.ownerDocument !== b.ownerDocument) return false;
    const ca = climb(a), cb = climb(b);
    if (ca === cb && ca.matches(ACTIONABLE)) return true;
    const [outer, inner] = a.contains(b) ? [a, b] : b.contains(a) ? [b, a] : [null, null];
    if (!outer || outer.matches(ACTIONABLE) !== inner.matches(ACTIONABLE)) return false;
    const t = ownText(inner);
    return t !== "" && ownText(outer) === t;
  }

  function domKey(el) {
    const parts = [];
    for (let e = el; e && e.parentElement; e = e.parentElement) {
      parts.unshift(Array.prototype.indexOf.call(e.parentElement.children, e));
    }
    return parts.join("/");
  }

  function cssString(s) {
    return '"' + s.replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
  }

  function xpathLiteral(s) {
    if (!s.includes("'")) return "'" + s + "'";
    if (!s.includes('"')) return '"' + s + '"';
    return "concat('" + s.split("'").join("', \"'\", '") + "')";
  }

  function structuralCss(el) {
    const parts = [];
    for (let e = el; e && e.tagName !== "HTML"; e = e.parentElement) {
      const tag = e.tagName.toLowerCase();
      if (tag === "body") { parts.unshift("body"); break; }
      const same = Array.from(e.parentElement.children).filter((c) => c.tagName === e.tagName);
      parts.unshift(same.length > 1 ? `${tag}:nth-of-type(${same.indexOf(e) + 1})` : tag);
    }
    return parts.join(" > ");
  }

  // Candidate selectors, best first. Grounding keeps the first that resolves uniquely back here.
  function cssCandidates(el) {
    const tag = el.tagName.toLowerCase();
    const out = [];
    if (el.id) out.push("#" + CSS.escape(el.id));
    const name = el.getAttribute("name");
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (name && (type === "radio" || type === "checkbox") && el.hasAttribute("value")) {
      out.push(`${tag}[name=${cssString(name)}][value=${cssString(el.getAttribute("value"))}]`);
    }
    if (name) out.push(`${tag}[name=${cssString(name)}]`);
    if (tag === "input" && ["button", "submit", "reset"].includes(type) && el.hasAttribute("value")) {
      out.push(`input[type=${cssString(type)}][value=${cssString(el.getAttribute("value"))}]`);
    }
    const href = el.getAttribute("href");
    if (tag === "a" && href && !/^\s*javascript:/i.test(href)) out.push(`a[href=${cssString(href)}]`);
    const src = el.getAttribute("src");
    if (src) out.push(`${tag}[src=${cssString(src)}]`);
    out.push(structuralCss(el));
    return out;
  }

  function rowHeader(el) {
    const cell = el.closest("td,th");
    const row = el.closest("tr");
    if (!cell || !row || !row.cells.length) return null;
    const first = row.cells[0];
    if (first === cell || first.contains(el)) return null;
    return norm(first.innerText).slice(0, 120) || null;
  }

  function xpathCandidates(el) {
    const tag = el.tagName.toLowerCase();
    const out = [];
    const text = ownText(el);
    if (text && (tag === "a" || tag === "button")) {
      out.push(`//${tag}[normalize-space(.)=${xpathLiteral(text)}]`);
    }
    if (tag === "input" && text) out.push(`//input[@value=${xpathLiteral(el.getAttribute("value") || "")}]`);
    const header = rowHeader(el);
    if (header) out.push(`//tr[normalize-space(*[1])=${xpathLiteral(header)}]//${tag}`);
    return out;
  }

  function programmaticLabel(el) {
    const doc = el.ownerDocument;
    if (el.labels && el.labels.length) {
      const t = norm(Array.from(el.labels).map((l) => l.innerText).join(" "));
      if (t) return t;
    }
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    if (ids.length) {
      const t = norm(ids.map((i) => (doc.getElementById(i) || {}).innerText || "").join(" "));
      if (t) return t;
    }
    return norm(el.getAttribute("aria-label")) || null;
  }

  function controlKind(el) {
    const tag = el.tagName.toLowerCase();
    for (const kind of ["textbox", "button", "link", "select", "checkbox", "radio", "image"]) {
      if (el.matches(CONTROLS[kind])) return kind;
    }
    return tag === "img" ? "image" : "text";
  }

  function describe(el) {
    const tag = el.tagName.toLowerCase();
    return {
      tag,
      input_type: tag === "input" ? (el.getAttribute("type") || "text").toLowerCase() : null,
      label: programmaticLabel(el),
      text: ownText(el).slice(0, 200) || null,
      row_header: rowHeader(el),
      frame: framePath(el.ownerDocument.defaultView),
      bbox: topBox(el),
      control: controlKind(el),
      css: cssCandidates(el),
      xpath: xpathCandidates(el),
      key: domKey(el),
      editable: tag === "textarea" || (tag === "input" && el.matches(CONTROLS.textbox)),
    };
  }

  function fieldValue(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === "select") {
      const o = el.options[el.selectedIndex];
      return o ? norm(o.text) : "";
    }
    if (tag === "input") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (type === "checkbox" || type === "radio") return el.checked ? "true" : "false";
      return el.value;
    }
    if (tag === "textarea") return el.value;
    return norm(el.innerText);
  }

  // Text a form control draws itself, as a person sees it. Password values are shown masked,
  // exactly as on screen; the secret never leaves the page.
  function controlText(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === "select") return fieldValue(el);
    if (tag === "textarea") return norm(el.value);
    if (tag !== "input") return "";
    const type = (el.getAttribute("type") || "text").toLowerCase();
    if (type === "password") return "•".repeat(el.value.length);
    if (["checkbox", "radio", "hidden", "file", "image", "range", "color"].includes(type)) return "";
    return ownText(el) || norm(el.value);
  }

  const SKIP_TEXT = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "TITLE", "OPTION",
    "SELECT", "TEXTAREA", "BUTTON"]);

  function isHeading(el) {
    if (el.closest("h1,h2,h3,h4,h5,h6,[role=heading]")) return true;
    const st = el.ownerDocument.defaultView.getComputedStyle(el);
    return parseInt(st.fontWeight, 10) >= 600 && parseFloat(st.fontSize) >= 16;
  }

  // Every piece of text visible in this frame's viewport, with the element that carries it.
  // Visible means topmost at the centre of its first on-screen line, through every frame.
  // Visible text and its geometry. With `every`, also text that is covered (under a fake modal's
  // translucent overlay) or scrolled out of view, unclipped: redaction must reach text a person
  // can still read through an overlay, and values a DOM snapshot will contain.
  function textBoxes(doc, every) {
    const win = doc.defaultView;
    const W = win.innerWidth, H = win.innerHeight;
    const o = offset(win);
    const frame = framePath(win).join("/") || null;
    const boxes = [], els = [];
    const root = doc.body || doc.documentElement;
    if (!root) return { boxes, els };
    const inView = (r) => every || (r.right > 0 && r.bottom > 0 && r.left < W && r.top < H);
    const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const p = n.parentElement;
      const text = norm(n.nodeValue);
      if (!p || !text || SKIP_TEXT.has(p.tagName) || p.closest("button,select,option")) continue;
      const range = doc.createRange();
      range.selectNodeContents(n);
      const rects = Array.from(range.getClientRects()).filter(
        (r) => r.width > 0 && r.height > 0 && inView(r)
      );
      if (!rects.length) continue;
      const r0 = rects[0];
      const cx = Math.min(Math.max(r0.left + r0.width / 2, 0), W - 1);
      const cy = Math.min(Math.max(r0.top + r0.height / 2, 0), H - 1);
      const onTop = hitsAt(p, cx, cy, "text");
      if (!every && !onTop) continue;
      const lo = every ? -Infinity : 0;
      const left = Math.max(lo, Math.min(...rects.map((r) => r.left)));
      const top = Math.max(lo, Math.min(...rects.map((r) => r.top)));
      const right = every ? Math.max(...rects.map((r) => r.right))
        : Math.min(W, Math.max(...rects.map((r) => r.right)));
      const bottom = every ? Math.max(...rects.map((r) => r.bottom))
        : Math.min(H, Math.max(...rects.map((r) => r.bottom)));
      boxes.push({
        text, frame, source: "text", heading: isHeading(p), covered: !onTop,
        x: Math.round(left + o.x), y: Math.round(top + o.y),
        width: Math.round(right - left), height: Math.round(bottom - top),
      });
      els.push(p);
    }
    for (const el of doc.querySelectorAll("input,textarea,select,button")) {
      const text = el.tagName === "BUTTON" ? norm(el.innerText) : controlText(el);
      if (!text || !rendered(el)) continue;
      const r = firstRect(el);
      if (!r || !inView(r)) continue;
      const cx = Math.min(Math.max(r.left + r.width / 2, 0), W - 1);
      const cy = Math.min(Math.max(r.top + r.height / 2, 0), H - 1);
      const onTop = hitsAt(el, cx, cy, "element");
      if (!every && !onTop) continue;
      boxes.push({ text, frame, source: "control", heading: false, covered: !onTop, ...topBox(el, r) });
      els.push(el);
    }
    return { boxes, els };
  }

  // Controls of a kind that are visible in this viewport, with their top-level boxes.
  function controls(doc, kind) {
    const boxes = [], els = [];
    const selector = CONTROLS[kind];
    if (!selector) return { boxes, els };
    for (const el of doc.querySelectorAll(selector)) {
      if (!rendered(el)) continue;
      const p = pointFor(el, false);
      if (!p.rendered || !p.topmost) continue;
      boxes.push(topBox(el));
      els.push(el);
    }
    return { boxes, els };
  }

  function deepActive(doc) {
    let el = doc.activeElement;
    while (el && (el.tagName === "FRAME" || el.tagName === "IFRAME") && el.contentDocument) {
      el = el.contentDocument.activeElement;
    }
    return el;
  }

  // The table under a caption. The caption must be unique in this document and visible (scrolled
  // into view if needed); the table is the nearest one below it that overlaps it horizontally.
  // Every row is hit-tested at its first cell, scrolling as a person would, and the frame's
  // scroll position is restored afterwards.
  function readTable(doc, keys) {
    const win = doc.defaultView;
    const sx = win.scrollX, sy = win.scrollY;
    try {
      const wanted = new Set(keys);
      const anchors = [];
      const walker = doc.createTreeWalker(doc.body || doc.documentElement, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const p = n.parentElement;
        if (p && !SKIP_TEXT.has(p.tagName) && wanted.has(labelKey(n.nodeValue)) && rendered(p)) {
          anchors.push(p);
        }
      }
      if (!anchors.length) return { error: "not_found", detail: "caption not found" };
      if (anchors.length > 1) return { error: "ambiguous", detail: `${anchors.length} captions` };
      const a = anchors[0];
      if (!inViewport(a)) a.scrollIntoView({ block: "start" });
      const ar = firstRect(a);
      if (!ar || !hitsAt(a, ar.left + ar.width / 2, ar.top + ar.height / 2, "text")) {
        return { error: "occluded", detail: "the caption is covered" };
      }
      let best = null, bestGap = Infinity;
      for (const t of doc.querySelectorAll("table")) {
        if (t.contains(a) || !rendered(t)) continue;
        const r = t.getBoundingClientRect();
        if (r.top < ar.bottom - 4 || r.right <= ar.left || r.left >= ar.right) continue;
        const gap = r.top - ar.bottom;
        if (gap < bestGap) { best = t; bestGap = gap; }
      }
      if (!best) return { error: "not_found", detail: "no table below the caption" };
      const rows = Array.from(best.rows).filter((r) => r.closest("table") === best);
      const grid = [];
      for (let i = 0; i < rows.length; i++) {
        const row = rows[i];
        if (!row.cells.length || !rendered(row)) continue;
        const cell = row.cells[0];
        if (!inViewport(cell)) cell.scrollIntoView({ block: "nearest" });
        const r = firstRect(cell);
        if (!r || !hitsAt(cell, r.left + r.width / 2, r.top + r.height / 2, "text")) {
          return { error: "occluded", detail: `row ${i} is covered` };
        }
        grid.push(Array.from(row.cells).map((c) => norm(c.innerText)));
      }
      return { grid };
    } finally {
      win.scrollTo(sx, sy);
    }
  }

  return {
    norm, labelKey, framePath, offset, rendered, inViewport, hitsAt, pointFor, topmostAt, climb,
    ownText,
    sameTarget, domKey, describe, fieldValue, textBoxes, controls, deepActive, readTable,
  };
})()
