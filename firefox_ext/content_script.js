"use strict";

(() => {
  if (window.__kkContentScriptLoaded) return;
  window.__kkContentScriptLoaded = true;

  // Canonical synonym groups so "click submit" matches "Send message" / paper-plane icons
  const SYNONYM_GROUPS = [
    ["submit", "send", "send message", "run", "post", "go", "arrow_upward", "paper-plane"],
    [
      "prompt", "input", "textbox", "enter a prompt", "ask gemini", "ask", "question",
      "question box", "ask question", "prompt box", "chat box", "text box", "message",
      "chat", "write", "type here", "search or ask"
    ],
    ["search", "find", "query", "lookup", "magnifying glass", "search box"],
    ["microphone", "mic", "voice", "dictate", "speak", "use microphone"],
    ["attach", "upload", "add file", "plus", "image", "upload file"],
    ["new chat", "new conversation", "reset", "start over"],
    ["copy", "copy code", "copy response", "clipboard"],
    ["stop", "cancel", "abort", "pause", "stop generating"],
    ["settings", "gear", "preferences", "options", "configure"],
    ["menu", "sidebar", "navigation", "hamburger", "expand", "collapse"],
    ["close", "dismiss", "cancel", "x"]
  ];

  const INTERACTIVE_SELECTOR = [
    "button",
    "a[href]",
    "input",
    "textarea",
    "select",
    "summary",
    "[role='button']",
    "[role='link']",
    "[role='textbox']",
    "[role='searchbox']",
    "[role='combobox']",
    "[role='tab']",
    "[role='menuitem']",
    "[role='checkbox']",
    "[role='switch']",
    "[role='option']",
    "[contenteditable]:not([contenteditable='false'])",
    "[tabindex]:not([tabindex='-1'])"
  ].join(",");

  let activeElementsMap = new Map();
  let lastGazeScreen = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
  let lastMonitorOffset = { x: 0, y: 0, w: 0, h: 0, usedMonLocal: false };
  let measuredViewportOrigin = { x: 0, y: 0, valid: false };
  let lastClientMouse = { x: 0, y: 0, ts: 0 };
  let pendingPointerAnchor = null;
  let hudContainer = null;
  let hudBox = null;
  let hudBadge = null;

  window.addEventListener(
    "mousemove",
    (ev) => {
      const now = performance.now();
      lastClientMouse = { x: ev.clientX, y: ev.clientY, ts: now };
      if (pendingPointerAnchor && now - pendingPointerAnchor.ts <= 250) {
        measuredViewportOrigin = {
          x: pendingPointerAnchor.gx - ev.clientX,
          y: pendingPointerAnchor.gy - ev.clientY,
          valid: true
        };
        pendingPointerAnchor = null;
      }
    },
    { capture: true, passive: true }
  );

  window.addEventListener("resize", () => {
    measuredViewportOrigin.valid = false;
  });

  function ensureHUD() {
    if (hudContainer && document.documentElement.contains(hudContainer)) return;
    hudContainer = document.createElement("div");
    hudContainer.id = "__kk_gaze_hud";
    Object.assign(hudContainer.style, {
      position: "fixed",
      inset: "0",
      pointerEvents: "none",
      zIndex: "2147483647",
      overflow: "hidden"
    });

    hudBox = document.createElement("div");
    Object.assign(hudBox.style, {
      position: "fixed",
      border: "2px solid rgba(50, 240, 130, 0.85)",
      backgroundColor: "rgba(50, 240, 130, 0.08)",
      borderRadius: "8px",
      boxShadow: "0 0 12px rgba(50, 240, 130, 0.35)",
      transition: "all 65ms ease-out",
      display: "none",
      pointerEvents: "none"
    });

    hudBadge = document.createElement("div");
    Object.assign(hudBadge.style, {
      position: "fixed",
      backgroundColor: "rgba(15, 18, 24, 0.92)",
      color: "#50fa7b",
      border: "1px solid rgba(80, 250, 123, 0.6)",
      borderRadius: "5px",
      padding: "2px 7px",
      fontSize: "11px",
      fontFamily: "system-ui, sans-serif",
      fontWeight: "600",
      whiteSpace: "nowrap",
      display: "none",
      pointerEvents: "none",
      boxShadow: "0 2px 6px rgba(0,0,0,0.4)"
    });

    hudContainer.appendChild(hudBox);
    hudContainer.appendChild(hudBadge);
    document.documentElement.appendChild(hudContainer);
  }

  function screenToViewport(screenX, screenY, monX = 0, monY = 0, monW = 0, monH = 0) {
    // Convert global Wayland physical/logical screen coordinates to Firefox CSS viewport coordinates
    const dpr = window.devicePixelRatio || 1;
    const mozX = typeof window.mozInnerScreenX === "number" ? window.mozInnerScreenX : window.screenX;
    const mozY = typeof window.mozInnerScreenY === "number" ? window.mozInnerScreenY : window.screenY;

    // 1. Exact measured Wayland window origin (locked via pointer_anchor + mousemove)
    if (measuredViewportOrigin.valid) {
      return {
        vx: screenX - measuredViewportOrigin.x,
        vy: screenY - measuredViewportOrigin.y,
        mozX,
        mozY,
        dpr
      };
    }

    let vx = screenX - mozX;
    let vy = screenY - mozY;
    let usedMonLocal = false;

    const inMonitorBounds =
      monW > 0 &&
      monH > 0 &&
      screenX >= monX &&
      screenX < monX + monW &&
      screenY >= monY &&
      screenY < monY + monH;

    // On Wayland, Firefox's mozInnerScreenX/Y is window-local (0, toolbarHeight) because
    // Wayland hides top-level window (x, y) from clients. For floating windows that don't
    // fill the full monitor, approximate centered window origin until pointer_anchor locks exact origin.
    const isFloatingWindow =
      monW > 0 &&
      monH > 0 &&
      (window.outerWidth < monW - 60 || window.outerHeight < monH - 90);
    const estWinOffX = isFloatingWindow ? Math.max(0, Math.round((monW - window.outerWidth) * 0.5)) : 0;
    const estWinOffY = isFloatingWindow ? Math.max(0, Math.round((monH - window.outerHeight) * 0.5)) : 0;

    if (
      (inMonitorBounds && (mozX < monX || mozY < monY || isFloatingWindow)) ||
      ((vx < -200 || vx > window.innerWidth + 200 || vy < -200 || vy > window.innerHeight + 200) &&
        (monX !== 0 || monY !== 0 || isFloatingWindow))
    ) {
      const localVx = (screenX - monX - estWinOffX) - mozX;
      const localVy = (screenY - monY - estWinOffY) - mozY;
      if (localVx >= -350 && localVx <= window.innerWidth + 350 && localVy >= -350 && localVy <= window.innerHeight + 350) {
        vx = localVx;
        vy = localVy;
        usedMonLocal = true;
      }
    }

    if ((vx < -200 || vx > window.innerWidth + 200) && dpr > 1.05) {
      vx = (usedMonLocal ? (screenX - monX - estWinOffX) : screenX) / dpr - mozX;
      vy = (usedMonLocal ? (screenY - monY - estWinOffY) : screenY) / dpr - mozY;
    }

    lastMonitorOffset = {
      x: monX + estWinOffX,
      y: monY + estWinOffY,
      w: monW,
      h: monH,
      usedMonLocal
    };
    return { vx, vy, mozX, mozY, dpr };
  }

  function viewportToScreen(vx, vy) {
    if (measuredViewportOrigin.valid) {
      return {
        sx: Math.round(measuredViewportOrigin.x + vx),
        sy: Math.round(measuredViewportOrigin.y + vy)
      };
    }
    const mozX = typeof window.mozInnerScreenX === "number" ? window.mozInnerScreenX : window.screenX;
    const mozY = typeof window.mozInnerScreenY === "number" ? window.mozInnerScreenY : window.screenY;
    const offX = lastMonitorOffset.usedMonLocal ? lastMonitorOffset.x : 0;
    const offY = lastMonitorOffset.usedMonLocal ? lastMonitorOffset.y : 0;
    return {
      sx: Math.round(offX + mozX + vx),
      sy: Math.round(offY + mozY + vy)
    };
  }

  function isVisible(el, rect) {
    if (rect.width < 8 || rect.height < 8) return false;
    if (rect.bottom < 0 || rect.top > window.innerHeight || rect.right < 0 || rect.left > window.innerWidth) {
      return false;
    }
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || parseFloat(style.opacity) < 0.05) {
      return false;
    }
    return true;
  }

  function resolveAriaLabelledBy(el) {
    const ids = (el.getAttribute("aria-labelledby") || "").trim().split(/\s+/);
    const parts = [];
    for (const id of ids) {
      if (!id) continue;
      const ref = document.getElementById(id);
      if (ref && ref.innerText) parts.push(ref.innerText.trim());
    }
    return parts.join(" ");
  }

  function extractIconHints(el) {
    const hints = [];
    const icons = el.querySelectorAll("mat-icon, i, span[class*='icon'], span[class*='symbol'], svg");
    for (const ic of icons) {
      const fontIcon = ic.getAttribute("fonticon") || ic.getAttribute("data-icon") || ic.getAttribute("aria-label") || "";
      if (fontIcon) hints.push(fontIcon);
      if (ic.tagName.toLowerCase() !== "svg" && ic.textContent && ic.textContent.trim().length <= 24) {
        hints.push(ic.textContent.trim());
      }
      const svgTitle = ic.querySelector && ic.querySelector("title");
      if (svgTitle && svgTitle.textContent) hints.push(svgTitle.textContent.trim());
    }
    const cls = (typeof el.className === "string" ? el.className : "") + " " + (el.id || "");
    for (const kw of ["send", "submit", "mic", "voice", "search", "attach", "upload", "copy", "close", "menu", "stop"]) {
      if (new RegExp(`\\b${kw}[-_a-z]*`, "i").test(cls)) {
        hints.push(kw);
      }
    }
    return hints.join(" ");
  }

  function expandAliases(rawText, tag, role, typeAttr, isNearPrompt) {
    const combined = `${rawText} ${tag} ${role} ${typeAttr}`.toLowerCase();
    const aliases = new Set();

    if (typeAttr === "submit") {
      aliases.add("submit");
      aliases.add("send");
    }
    if (
      role === "textbox" ||
      role === "searchbox" ||
      role === "combobox" ||
      tag === "textarea" ||
      tag === "input" ||
      combined.includes("prompt") ||
      combined.includes("type a message")
    ) {
      for (const w of [
        "prompt", "input", "textbox", "box", "field", "ask", "question", "chat", "message", "write", "type"
      ]) {
        aliases.add(w);
      }
      // Include clean words from document.title (e.g. "gemini", "teams")
      const titleWords = (document.title || "").toLowerCase().match(/[a-z0-9]{3,}/g) || [];
      for (const tw of titleWords.slice(0, 4)) aliases.add(tw);
    }
    if (isNearPrompt && (tag === "button" || role === "button")) {
      if (/\b(send|arrow|upward|submit|paper|plane|run)\b/.test(combined)) {
        aliases.add("submit");
        aliases.add("send");
        aliases.add("submit button");
        aliases.add("send button");
      }
    }

    for (const group of SYNONYM_GROUPS) {
      if (group.some((term) => combined.includes(term))) {
        for (const term of group) aliases.add(term);
      }
    }
    return Array.from(aliases);
  }

  function collectAllInteractiveRoots(root = document, out = []) {
    const nodes = root.querySelectorAll(INTERACTIVE_SELECTOR);
    for (const n of nodes) out.push(n);
    const all = root.querySelectorAll("*");
    for (const el of all) {
      if (el.shadowRoot) {
        collectAllInteractiveRoots(el.shadowRoot, out);
      }
      if (el.tagName === "IFRAME") {
        try {
          if (el.contentDocument) collectAllInteractiveRoots(el.contentDocument, out);
        } catch (_e) {}
      }
    }
    return out;
  }

  const ORDINAL_WORDS = [
    "first", "second", "third", "fourth", "fifth",
    "sixth", "seventh", "eighth", "ninth", "tenth",
    "eleventh", "twelfth"
  ];

  let resultBadgesContainer = null;

  function ensureResultBadgesContainer() {
    if (resultBadgesContainer && document.documentElement.contains(resultBadgesContainer)) {
      resultBadgesContainer.innerHTML = "";
      return resultBadgesContainer;
    }
    resultBadgesContainer = document.createElement("div");
    resultBadgesContainer.id = "__kk_result_badges";
    Object.assign(resultBadgesContainer.style, {
      position: "fixed",
      inset: "0",
      pointerEvents: "none",
      zIndex: "2147483646",
      overflow: "hidden"
    });
    document.documentElement.appendChild(resultBadgesContainer);
    return resultBadgesContainer;
  }

  function detectSearchResultsAndSuggestions() {
    // 1. Check if an autocomplete / typeahead dropdown is currently visible
    const suggestionSelectors = [
      "[role='listbox'] [role='option']",
      "ul[role='listbox'] li",
      ".sbct",
      "ytd-searchbox [role='option']",
      ".gstl_50 .sbsb_c",
      "[id*='autocomplete'] [role='option']",
      "[id*='search'] [role='option']",
      "[class*='autocomplete'] li",
      "[class*='suggestion'] li",
      "[class*='typeahead'] li"
    ].join(",");

    const visibleSuggestions = [];
    for (const el of document.querySelectorAll(suggestionSelectors)) {
      const r = el.getBoundingClientRect();
      if (isVisible(el, r) && r.width > 60 && r.height >= 16 && r.height < 120) {
        visibleSuggestions.push({ el, rect: r, kind: "suggestion" });
      }
    }

    if (visibleSuggestions.length > 0) {
      visibleSuggestions.sort((a, b) => a.rect.top - b.rect.top);
      return visibleSuggestions.slice(0, 10);
    }

    // 2. Otherwise, check for Search Engine Result Page (SERP) links
    const serpSelectors = [
      "#search a:has(h3)",
      "#rso a:has(h3)",
      "a h3",
      "a#video-title",
      "ytd-video-renderer a#video-title-link",
      "[data-testid='result-title-a']",
      ".result__a",
      ".search-title a",
      "main [role='main'] h2 a",
      "main h3 a"
    ].join(",");

    const seenLinks = new Set();
    const serpResults = [];
    let matchedNodes = [];
    try {
      matchedNodes = document.querySelectorAll(serpSelectors);
    } catch (_e) {
      matchedNodes = document.querySelectorAll("a h3, a#video-title, [data-testid='result-title-a'], .result__a");
    }
    for (const node of matchedNodes) {
      const linkEl = node.tagName.toLowerCase() === "a" ? node : node.closest("a");
      if (!linkEl || seenLinks.has(linkEl)) continue;
      const r = linkEl.getBoundingClientRect();
      if (isVisible(linkEl, r) && r.width > 80 && r.top >= 40) {
        seenLinks.add(linkEl);
        serpResults.push({ el: linkEl, rect: r, kind: "result" });
      }
    }

    serpResults.sort((a, b) => a.rect.top - b.rect.top || a.rect.left - b.rect.left);
    return serpResults.slice(0, 12);
  }

  function renderNumberBadges(rankedItems) {
    const container = ensureResultBadgesContainer();
    rankedItems.forEach((item, idx) => {
      const num = idx + 1;
      const r = item.rect;
      const badge = document.createElement("div");
      badge.textContent = `#${num}`;
      const isSugg = item.kind === "suggestion";
      Object.assign(badge.style, {
        position: "fixed",
        left: `${Math.max(4, r.left - 34)}px`,
        top: `${Math.max(4, r.top + (r.height - 18) * 0.5)}px`,
        backgroundColor: isSugg ? "rgba(255, 170, 0, 0.94)" : "rgba(0, 210, 255, 0.92)",
        color: "#0b0e14",
        borderRadius: "4px",
        padding: "1px 5px",
        fontSize: "11px",
        fontFamily: "monospace",
        fontWeight: "800",
        boxShadow: "0 1px 4px rgba(0,0,0,0.45)",
        pointerEvents: "none"
      });
      container.appendChild(badge);
    });
  }

  function scanInteractiveDOM(gazeScreenX, gazeScreenY, monX = 0, monY = 0, monW = 0, monH = 0) {
    const { vx: gazeVX, vy: gazeVY } = screenToViewport(gazeScreenX, gazeScreenY, monX, monY, monW, monH);
    const rawNodes = collectAllInteractiveRoots(document);

    // Detect ordered search suggestions or search results (#1 .. #12)
    const rankedResults = detectSearchResultsAndSuggestions();
    const elementToRank = new Map();
    for (let i = 0; i < rankedResults.length; i++) {
      elementToRank.set(rankedResults[i].el, { rank: i + 1, kind: rankedResults[i].kind });
      if (!rawNodes.includes(rankedResults[i].el)) {
        rawNodes.unshift(rankedResults[i].el);
      }
    }
    renderNumberBadges(rankedResults);

    let promptRect = null;
    for (const n of rawNodes) {
      const role = (n.getAttribute("role") || "").toLowerCase();
      const isEditable = Boolean(n.isContentEditable) || n.tagName === "TEXTAREA";
      if (isEditable || role === "textbox" || role === "searchbox") {
        const r = n.getBoundingClientRect();
        if (r.width > 120 && r.height > 20) {
          promptRect = r;
          break;
        }
      }
    }

    activeElementsMap.clear();
    const candidates = [];
    let nextId = 1;

    for (const el of rawNodes) {
      if (el.disabled || el.getAttribute("aria-disabled") === "true") continue;
      const rect = el.getBoundingClientRect();
      if (!isVisible(el, rect)) continue;

      const tag = el.tagName.toLowerCase();
      const role = (el.getAttribute("role") || (tag === "a" ? "link" : tag)).toLowerCase();
      const typeAttr = (el.getAttribute("type") || "").toLowerCase();
      const isEditable = Boolean(el.isContentEditable) || tag === "textarea" || tag === "input";

      const visibleText = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 110);
      const dataPlaceholder = (
        el.getAttribute("data-placeholder") ||
        el.getAttribute("aria-placeholder") ||
        (el.querySelector && el.querySelector("[data-placeholder]")?.getAttribute("data-placeholder")) ||
        ""
      ).replace(/\s+/g, " ").trim();
      const ariaLabel = (
        el.getAttribute("aria-label") ||
        resolveAriaLabelledBy(el) ||
        el.getAttribute("mattooltip") ||
        el.getAttribute("data-tooltip") ||
        el.getAttribute("title") ||
        el.getAttribute("placeholder") ||
        dataPlaceholder ||
        el.getAttribute("data-label") ||
        el.getAttribute("aria-description") ||
        el.getAttribute("alt") ||
        el.getAttribute("name") ||
        ""
      ).replace(/\s+/g, " ").trim();

      const iconHints = extractIconHints(el);
      const combinedLabel = [ariaLabel, dataPlaceholder].filter(Boolean).join(" ").trim();
      let primaryLabel = dataPlaceholder || ariaLabel || visibleText || iconHints || `${role}`;

      const cx = rect.left + rect.width * 0.5;
      const cy = rect.top + rect.height * 0.5;

      let isNearPrompt = false;
      if (promptRect) {
        const dxP = Math.max(0, Math.max(promptRect.left - cx, cx - promptRect.right));
        const dyP = Math.max(0, Math.max(promptRect.top - cy, cy - promptRect.bottom));
        isNearPrompt = Math.hypot(dxP, dyP) < 160;
      }

      const aliases = expandAliases(
        `${visibleText} ${combinedLabel} ${iconHints}`,
        tag,
        role,
        typeAttr,
        isNearPrompt
      );

      // Add ordinal aliases ("result 1", "first result", "option 1") for search suggestions/results
      const rankInfo = elementToRank.get(el);
      if (rankInfo) {
        const n = rankInfo.rank;
        const ordWord = ORDINAL_WORDS[n - 1] || `${n}th`;
        primaryLabel = `#${n} ${visibleText || primaryLabel}`;
        aliases.push(
          `result ${n}`,
          `${ordWord} result`,
          `option ${n}`,
          `${ordWord} option`,
          `suggestion ${n}`,
          `${ordWord} suggestion`,
          `number ${n}`,
          `item ${n}`,
          `${ordWord} item`,
          `link ${n}`,
          `${ordWord} link`
        );
      }

      const gazeDist = Math.hypot(cx - gazeVX, cy - gazeVY);
      const screenCenter = viewportToScreen(cx, cy);

      const domId = nextId++;
      activeElementsMap.set(domId, el);

      candidates.push({
        dom_id: domId,
        tag,
        role: rankInfo ? rankInfo.kind : role,
        primary_label: primaryLabel,
        visible_text: visibleText,
        aria_label: ariaLabel,
        icon_hints: iconHints,
        aliases,
        is_editable: isEditable,
        gaze_dist_px: Math.round(gazeDist),
        screen_x: screenCenter.sx,
        screen_y: screenCenter.sy,
        rect: {
          left: Math.round(rect.left),
          top: Math.round(rect.top),
          width: Math.round(rect.width),
          height: Math.round(rect.height)
        }
      });
    }

    candidates.sort((a, b) => {
      const aRanked = a.primary_label.startsWith("#") ? 0 : 1;
      const bRanked = b.primary_label.startsWith("#") ? 0 : 1;
      if (aRanked !== bRanked) return aRanked - bRanked;
      return a.gaze_dist_px - b.gaze_dist_px;
    });
    return candidates.slice(0, 65);
  }

  function hideHUD() {
    if (hudBox) hudBox.style.display = "none";
    if (hudBadge) hudBadge.style.display = "none";
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) hideHUD();
  });
  window.addEventListener("blur", hideHUD);

  function updateMagneticHUD(gazeScreenX, gazeScreenY, monX = 0, monY = 0, monW = 0, monH = 0) {
    if (document.hidden || !document.hasFocus()) {
      hideHUD();
      return;
    }
    const { vx, vy } = screenToViewport(gazeScreenX, gazeScreenY, monX, monY, monW, monH);
    if (vx < -40 || vx > window.innerWidth + 40 || vy < -40 || vy > window.innerHeight + 40) {
      hideHUD();
      return;
    }
    ensureHUD();
    const candidates = scanInteractiveDOM(gazeScreenX, gazeScreenY, monX, monY, monW, monH);
    if (candidates.length === 0 || candidates[0].gaze_dist_px > 420) {
      hideHUD();
      return;
    }

    const best = candidates[0];
    const r = best.rect;
    hudBox.style.display = "block";
    hudBox.style.left = `${r.left - 3}px`;
    hudBox.style.top = `${r.top - 3}px`;
    hudBox.style.width = `${r.width + 6}px`;
    hudBox.style.height = `${r.height + 6}px`;

    let badgeText = best.primary_label.slice(0, 34);
    if (best.aliases.includes("submit") && !badgeText.toLowerCase().includes("submit")) {
      badgeText += " [submit]";
    }
    hudBadge.textContent = badgeText;
    hudBadge.style.display = "block";
    hudBadge.style.left = `${Math.max(6, Math.min(window.innerWidth - 180, r.left))}px`;
    hudBadge.style.top = `${r.top > 28 ? r.top - 24 : r.top + r.height + 6}px`;
  }

  function flashClickRipple(el) {
    const r = el.getBoundingClientRect();
    const ripple = document.createElement("div");
    Object.assign(ripple.style, {
      position: "fixed",
      left: `${r.left - 4}px`,
      top: `${r.top - 4}px`,
      width: `${r.width + 8}px`,
      height: `${r.height + 8}px`,
      border: "3px solid #00f5d4",
      backgroundColor: "rgba(0, 245, 212, 0.25)",
      borderRadius: "10px",
      zIndex: "2147483647",
      pointerEvents: "none",
      transition: "opacity 320ms ease-out"
    });
    document.documentElement.appendChild(ripple);
    setTimeout(() => {
      ripple.style.opacity = "0";
      setTimeout(() => ripple.remove(), 340);
    }, 60);
  }

  function simulateRealClick(el) {
    // CRITICAL FOR SEARCH BAR AUTOCOMPLETE DROPDOWNS:
    // Do NOT blur the active search input or call el.focus() before mousedown/mouseup/click,
    // otherwise Google/YouTube/React search dropdowns close before the click event registers!
    const r = el.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight) {
      el.scrollIntoView({ block: "center", inline: "nearest" });
    }
    const rectNow = el.getBoundingClientRect();
    const cx = rectNow.left + rectNow.width * 0.5;
    const cy = rectNow.top + rectNow.height * 0.5;
    const opts = {
      bubbles: true,
      cancelable: true,
      view: window,
      clientX: cx,
      clientY: cy,
      button: 0,
      buttons: 1
    };

    el.dispatchEvent(new PointerEvent("pointerover", opts));
    el.dispatchEvent(new MouseEvent("mouseover", opts));
    el.dispatchEvent(new PointerEvent("pointerdown", opts));
    el.dispatchEvent(new MouseEvent("mousedown", opts));
    el.dispatchEvent(new PointerEvent("pointerup", opts));
    el.dispatchEvent(new MouseEvent("mouseup", opts));
    el.dispatchEvent(new MouseEvent("click", opts));

    const tag = el.tagName.toLowerCase();
    if (tag === "input" || tag === "textarea" || el.isContentEditable) {
      if (typeof el.focus === "function") el.focus();
      if (el.isContentEditable) {
        try {
          const doc = el.ownerDocument || document;
          const win = doc.defaultView || window;
          const sel = win.getSelection();
          const range = doc.createRange();
          const targetNode = el.querySelector("p") || el;
          range.selectNodeContents(targetNode);
          range.collapse(false);
          sel.removeAllRanges();
          sel.addRange(range);
        } catch (_e) {}
      }
    }
    flashClickRipple(el);
  }

  function simulateTypeInto(el, text, pressEnter = false) {
    if (typeof el.focus === "function") el.focus();
    simulateRealClick(el);

    const isContentEditable =
      el.getAttribute("contenteditable") === "true" || el.isContentEditable;

    if (isContentEditable) {
      const doc = el.ownerDocument || document;
      const win = doc.defaultView || window;
      const sel = win.getSelection();
      const range = doc.createRange();
      range.selectNodeContents(el);
      range.collapse(false);
      sel.removeAllRanges();
      sel.addRange(range);

      if (!doc.execCommand("insertText", false, text)) {
        el.textContent += text;
        el.dispatchEvent(new InputEvent("input", { bubbles: true, data: text, inputType: "insertText" }));
      }
    } else if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
      const proto = el.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const nativeSetter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      if (nativeSetter) {
        nativeSetter.call(el, text);
      } else {
        el.value = text;
      }
      el.dispatchEvent(new InputEvent("input", { bubbles: true, data: text, inputType: "insertText" }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keyup", { key: text.slice(-1) || "a", bubbles: true }));
    }

    if (pressEnter) {
      const form = el.closest("form");
      const keyOpts = { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true };
      el.dispatchEvent(new KeyboardEvent("keydown", keyOpts));
      el.dispatchEvent(new KeyboardEvent("keypress", keyOpts));
      el.dispatchEvent(new KeyboardEvent("keyup", keyOpts));
      if (form && typeof form.requestSubmit === "function") {
        try { form.requestSubmit(); } catch (_e) {}
      }
    }
  }

  function collectMediaElements(root = document, out = []) {
    for (const m of root.querySelectorAll("video, audio")) out.push(m);
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) collectMediaElements(el.shadowRoot, out);
    }
    return out;
  }

  function handleMediaAction(mediaAction) {
    const media = collectMediaElements(document);
    let handled = false;

    if (mediaAction === "pause") {
      for (const m of media) {
        if (!m.paused) {
          m.pause();
          handled = true;
        }
      }
      if (!handled) {
        const btn = document.querySelector("[aria-label*='Pause' i], .ytp-play-button");
        if (btn) {
          simulateRealClick(btn);
          handled = true;
        }
      }
    } else if (mediaAction === "play" || mediaAction === "resume") {
      for (const m of media) {
        if (m.paused) {
          const p = m.play();
          if (p && typeof p.catch === "function") p.catch(() => {});
          handled = true;
        }
      }
      if (!handled) {
        const btn = document.querySelector("[aria-label*='Play' i], .ytp-play-button");
        if (btn) {
          simulateRealClick(btn);
          handled = true;
        }
      }
    } else if (mediaAction === "mute" || mediaAction === "unmute") {
      const wantMuted = mediaAction === "mute";
      for (const m of media) {
        m.muted = wantMuted;
        handled = true;
      }
    } else if (mediaAction === "fullscreen") {
      const btn = document.querySelector(".ytp-fullscreen-button, [aria-label*='Full screen' i]");
      if (btn) {
        simulateRealClick(btn);
        handled = true;
      } else if (media.length > 0 && typeof media[0].requestFullscreen === "function") {
        media[0].requestFullscreen().catch(() => {});
        handled = true;
      }
    } else if (mediaAction === "exit_fullscreen") {
      if (document.fullscreenElement && typeof document.exitFullscreen === "function") {
        document.exitFullscreen().catch(() => {});
        handled = true;
      }
    }
    return handled;
  }

  let autoScrollRAF = null;
  let autoScrollSpeedPxPerSec = 140;
  let autoScrollDir = 1;
  let autoScrollTarget = null;

  function canElementScroll(el, axis = "y", dirSign = 0) {
    if (!el || el === document.body || el === document.documentElement) return false;
    const tag = el.tagName ? el.tagName.toLowerCase() : "";
    if (
      (tag === "textarea" || tag === "input" || el.getAttribute("contenteditable") === "true") &&
      el.clientHeight < 220
    ) {
      return false;
    }

    const style = window.getComputedStyle(el);
    if (axis === "y") {
      const ovY = style.overflowY;
      const isScrollStyle = ovY === "auto" || ovY === "scroll" || ovY === "overlay";
      if (!isScrollStyle || el.scrollHeight <= el.clientHeight + 12) return false;
      if (dirSign < 0 && el.scrollTop <= 1) return false;
      if (dirSign > 0 && el.scrollTop + el.clientHeight >= el.scrollHeight - 2) return false;
      return true;
    } else {
      const ovX = style.overflowX;
      const isScrollStyle = ovX === "auto" || ovX === "scroll" || ovX === "overlay";
      if (!isScrollStyle || el.scrollWidth <= el.clientWidth + 12) return false;
      if (dirSign < 0 && el.scrollLeft <= 1) return false;
      if (dirSign > 0 && el.scrollLeft + el.clientWidth >= el.scrollWidth - 2) return false;
      return true;
    }
  }

  function findBestScrollContainer(gazeScreenX, gazeScreenY, axis = "y", dirSign = 0) {
    const { vx, vy } = screenToViewport(gazeScreenX, gazeScreenY);
    const clampedX = Math.max(10, Math.min(window.innerWidth - 10, vx));
    const clampedY = Math.max(10, Math.min(window.innerHeight - 10, vy));

    const hitNodes = document.elementsFromPoint(clampedX, clampedY);
    for (const startNode of hitNodes) {
      let cur = startNode;
      while (cur && cur !== document.body && cur !== document.documentElement) {
        if (canElementScroll(cur, axis, dirSign)) {
          return cur;
        }
        cur = cur.parentElement || (cur.getRootNode && cur.getRootNode().host) || null;
      }
    }

    for (const startNode of hitNodes) {
      let cur = startNode;
      while (cur && cur !== document.body && cur !== document.documentElement) {
        if (canElementScroll(cur, axis, 0)) {
          return cur;
        }
        cur = cur.parentElement || (cur.getRootNode && cur.getRootNode().host) || null;
      }
    }

    const rootScroller = document.scrollingElement || document.documentElement;
    if (
      axis === "y" &&
      rootScroller &&
      rootScroller.scrollHeight > window.innerHeight + 20
    ) {
      return rootScroller;
    }

    let bestPane = null;
    let bestArea = 0;
    const allElements = collectAllInteractiveRoots
      ? document.querySelectorAll("div, main, section, ul, article, pre, aside")
      : [];
    for (const el of allElements) {
      if (!canElementScroll(el, axis, dirSign) && !canElementScroll(el, axis, 0)) continue;
      const r = el.getBoundingClientRect();
      if (!isVisible(el, r)) continue;
      const area = r.width * r.height;
      if (area > bestArea) {
        bestArea = area;
        bestPane = el;
      }
    }

    return bestPane || rootScroller;
  }

  function stopAutoScroll() {
    if (autoScrollRAF !== null) {
      cancelAnimationFrame(autoScrollRAF);
      autoScrollRAF = null;
    }
  }

  function startAutoScroll(targetEl, dirSign, speedPxPerSec = 140) {
    stopAutoScroll();
    autoScrollTarget = targetEl;
    autoScrollDir = dirSign;
    autoScrollSpeedPxPerSec = speedPxPerSec;
    let lastTs = performance.now();

    function step(nowTs) {
      const dt = Math.min(0.1, (nowTs - lastTs) / 1000);
      lastTs = nowTs;
      const dy = autoScrollDir * autoScrollSpeedPxPerSec * dt;
      if (
        autoScrollTarget === document.documentElement ||
        autoScrollTarget === document.body ||
        autoScrollTarget === document.scrollingElement
      ) {
        window.scrollBy(0, dy);
      } else if (autoScrollTarget) {
        autoScrollTarget.scrollTop += dy;
      }
      autoScrollRAF = requestAnimationFrame(step);
    }

    autoScrollRAF = requestAnimationFrame(step);
  }

  function executeDOMScroll(msg) {
    const gx = typeof msg.gaze_x === "number" ? msg.gaze_x : lastGazeScreen.x;
    const gy = typeof msg.gaze_y === "number" ? msg.gaze_y : lastGazeScreen.y;
    const direction = (msg.direction || "down").toLowerCase();
    const amount = (msg.amount || "normal").toLowerCase();
    const continuous = (msg.continuous || "").toLowerCase();

    if (continuous === "stop") {
      stopAutoScroll();
      return { ok: true, mode: "auto_scroll_stopped" };
    }
    if (continuous === "faster" && autoScrollRAF !== null) {
      autoScrollSpeedPxPerSec = Math.min(600, autoScrollSpeedPxPerSec * 1.45);
      return { ok: true, mode: "auto_scroll_faster", speed: autoScrollSpeedPxPerSec };
    }
    if (continuous === "slower" && autoScrollRAF !== null) {
      autoScrollSpeedPxPerSec = Math.max(40, autoScrollSpeedPxPerSec * 0.68);
      return { ok: true, mode: "auto_scroll_slower", speed: autoScrollSpeedPxPerSec };
    }

    stopAutoScroll();

    const isHorizontal = direction === "left" || direction === "right";
    const axis = isHorizontal ? "x" : "y";
    const dirSign = direction === "up" || direction === "left" || direction === "top" ? -1 : 1;

    const target = findBestScrollContainer(gx, gy, axis, dirSign);
    const isRoot =
      target === document.documentElement ||
      target === document.body ||
      target === document.scrollingElement;

    if (continuous === "start") {
      startAutoScroll(target, dirSign, 140);
      return { ok: true, mode: "auto_scroll_started" };
    }

    const viewH = isRoot ? window.innerHeight : target.clientHeight || window.innerHeight;
    const viewW = isRoot ? window.innerWidth : target.clientWidth || window.innerWidth;

    if (direction === "top") {
      target.scrollTo({ top: 0, behavior: "smooth" });
      return { ok: true, target_tag: target.tagName };
    }
    if (direction === "bottom") {
      const maxTop = target.scrollHeight || document.documentElement.scrollHeight;
      target.scrollTo({ top: maxTop, behavior: "smooth" });
      return { ok: true, target_tag: target.tagName };
    }

    const ratio = amount === "small" ? 0.25 : amount === "large" ? 0.88 : 0.58;
    const deltaPx = Math.round((isHorizontal ? viewW : viewH) * ratio) * dirSign;

    if (isHorizontal) {
      target.scrollBy({ left: deltaPx, behavior: "smooth" });
    } else {
      target.scrollBy({ top: deltaPx, behavior: "smooth" });
    }

    return { ok: true, target_tag: target.tagName, delta_px: deltaPx };
  }

  browser.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === "pointer_anchor") {
      const gx = Number(msg.global_x);
      const gy = Number(msg.global_y);
      const now = performance.now();
      if (Number.isFinite(gx) && Number.isFinite(gy)) {
        if (now - lastClientMouse.ts <= 150) {
          measuredViewportOrigin = {
            x: gx - lastClientMouse.x,
            y: gy - lastClientMouse.y,
            valid: true
          };
          pendingPointerAnchor = null;
        } else {
          pendingPointerAnchor = { gx, gy, ts: now };
        }
      }
      sendResponse({ ok: true, valid: measuredViewportOrigin.valid });
      return false;
    }

    if (msg.type === "hide_hud") {
      hideHUD();
      sendResponse({ ok: true });
      return false;
    }

    if (msg.type === "scroll_dom") {
      const res = executeDOMScroll(msg);
      sendResponse(res);
      return false;
    }

    if (msg.type === "execute_media_action") {
      const handled = handleMediaAction(msg.media_action || "");
      sendResponse({ ok: true, handled });
      return false;
    }

    if (msg.type === "gaze_update") {
      const monX = typeof msg.mon_x === "number" ? msg.mon_x : 0;
      const monY = typeof msg.mon_y === "number" ? msg.mon_y : 0;
      const monW = typeof msg.mon_w === "number" ? msg.mon_w : 0;
      const monH = typeof msg.mon_h === "number" ? msg.mon_h : 0;
      lastGazeScreen = { x: msg.gaze_x, y: msg.gaze_y };
      updateMagneticHUD(msg.gaze_x, msg.gaze_y, monX, monY, monW, monH);
      sendResponse({ ok: true });
      return false;
    }

    if (msg.type === "get_dom_candidates") {
      const gx = typeof msg.gaze_x === "number" ? msg.gaze_x : lastGazeScreen.x;
      const gy = typeof msg.gaze_y === "number" ? msg.gaze_y : lastGazeScreen.y;
      const monX = typeof msg.mon_x === "number" ? msg.mon_x : 0;
      const monY = typeof msg.mon_y === "number" ? msg.mon_y : 0;
      const monW = typeof msg.mon_w === "number" ? msg.mon_w : 0;
      const monH = typeof msg.mon_h === "number" ? msg.mon_h : 0;
      const list = scanInteractiveDOM(gx, gy, monX, monY, monW, monH);
      sendResponse({
        url: window.location.href,
        title: document.title,
        candidates: list
      });
      return false;
    }

    if (msg.type === "execute_dom_action") {
      stopAutoScroll();
      const el = activeElementsMap.get(msg.dom_id);
      if (!el || !el.isConnected) {
        sendResponse({ ok: false, error: "Element no longer in DOM" });
        return false;
      }

      if (msg.action === "click" || msg.action === "double_click") {
        simulateRealClick(el);
        if (msg.action === "double_click") {
          setTimeout(() => simulateRealClick(el), 60);
        }
      } else if (msg.action === "type_into") {
        simulateTypeInto(el, msg.text || "", Boolean(msg.press_enter));
      } else if (msg.action === "focus" || msg.action === "hover") {
        el.scrollIntoView({ block: "nearest", inline: "nearest" });
        if (typeof el.focus === "function") el.focus();
        flashClickRipple(el);
      }

      const r = el.getBoundingClientRect();
      const sc = viewportToScreen(r.left + r.width * 0.5, r.top + r.height * 0.5);
      sendResponse({ ok: true, screen_x: sc.sx, screen_y: sc.sy });
      return false;
    }
  });

  // Passively harvest physical user mouse clicks in Firefox for continuous gaze calibration!
  window.addEventListener(
    "mousedown",
    (ev) => {
      if (!ev.isTrusted || ev.button !== 0) return;
      const sc = viewportToScreen(ev.clientX, ev.clientY);
      browser.runtime.sendMessage({
        type: "passive_mouse_click",
        screen_x: sc.sx,
        screen_y: sc.sy
      }).catch(() => {});
    },
    { capture: true, passive: true }
  );
})();
