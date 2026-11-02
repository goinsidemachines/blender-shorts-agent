/* The review page: the stage bar, live updates from the page server, notes and answers, and the offline view.
   Every piece of text from the snapshot is set with textContent (via el), never parsed as markup. */
(function () {
  "use strict";
  const BSA = (window.BSA = window.BSA || {});
  BSA.stages = BSA.stages || {};

  const OFFLINE = window.BSA_MODE === "static";
  const MAX_NOTE = 4000;
  const FETCH_TIMEOUT_MS = 5000;
  const RETRY_MS = 1000; // after a failed poll, try again sooner: two failures in a row pause the buttons
  const THEME_KEY = "bsa-theme";
  const TEXT = {
    offline: "Offline view: buttons copy your answer for the chat",
    unreachable: "Can't reach the page server, so this may be out of date. Your buttons are paused. " +
      "Ask Claude to open the page again.",
    waiting: "Sent · waiting for Claude",
    approved: "Approved",
  };
  const STATUS = { open: "Not sent", sent: "Sent", done: "Done", declined: "Declined" };

  const $ = (id) => document.getElementById(id);
  const nodes = {
    title: $("bsa-title"), sub: $("bsa-sub"), live: $("bsa-live"), theme: $("bsa-theme"),
    nav: $("bsa-stages"), banners: $("bsa-banners"), stage: $("bsa-stage"), side: $("bsa-side"),
    announce: $("bsa-announce"),
  };

  const params = new URLSearchParams(location.search);
  const TOKEN = params.get("t") || "";
  let snap = OFFLINE ? window.BSA_SNAPSHOT || null : null;
  const KEY = OFFLINE
    ? String(((snap || {}).short || {}).key || "")
    : decodeURIComponent(location.pathname.split("/")[2] || "");

  let etag = null;
  let failures = 0;
  let paused = false;
  let timer = null;
  let polling = false;
  let pollAgain = false;
  let editor = null; // {stage, kind, id, version, label, text}
  let actionError = null;
  let notice = null;
  let busy = false;
  let bannerKey = "";
  const pendingApprove = {}; // stage -> the version approved here, until the snapshot shows it
  const offlineNotes = []; // notes written in the offline view: they live only in this tab

  // ------------------------------------------------------------------ DOM building (text only, never markup)

  function append(node, kids) {
    for (const c of kids) {
      if (c === null || c === undefined || c === false) continue;
      if (Array.isArray(c)) append(node, c);
      else if (c instanceof Node) node.append(c);
      else node.append(document.createTextNode(String(c)));
    }
  }

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (/^on/i.test(k)) { // handlers only as functions; a string would be code
        if (typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
        continue;
      }
      if ((k === "href" || k === "src") && /^\s*(javascript|data|vbscript):/i.test(String(v))) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "value") node.value = v;
      else if (k === "disabled") node.disabled = !!v;
      else node.setAttribute(k, v === true ? "" : String(v));
    }
    append(node, children);
    return node;
  }

  // ------------------------------------------------------------------ answers for the chat

  function targetWords(t) {
    return [t.kind, t.id].filter((x) => x !== null && x !== undefined && x !== "").join(" ");
  }

  function answerText(kind, a) {
    if (kind === "approve") {
      const engine = a.choice && a.choice.engine ? ` with ${String(a.choice.engine).toUpperCase()}` : "";
      return `Approve ${a.label} (version ${a.version})${engine}`;
    }
    if (kind === "changes") {
      const lines = (a.notes || []).map((n) => `- ${targetWords(n.target || {})}: ${n.text}`);
      return [`Changes to ${a.label} (version ${a.version}):`, ...lines].join("\n");
    }
    if (kind === "note") {
      return `Note on ${a.label} ${targetWords(a.note.target || {})} (version ${a.version}): ${a.note.text}`;
    }
    throw new Error(`unknown answer ${kind}`);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) { /* fall back to a hidden selection */ }
    const ta = el("textarea", { class: "sr-only", "aria-hidden": "true", tabindex: "-1" });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    return ok;
  }

  async function copyAnswer(text) {
    const ok = await copyText(text);
    notice = ok ? "Copied. Paste it in the chat with Claude." : "Couldn't copy. Select the text and copy it by hand.";
    say(notice);
    render();
    return ok;
  }

  // ------------------------------------------------------------------ the page server

  function apiUrl(path) {
    return `/api/${encodeURIComponent(KEY)}/${path}`;
  }

  async function api(method, path, body, headers) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    const h = Object.assign({ "X-BSA-Token": TOKEN }, headers || {});
    if (body !== undefined) h["Content-Type"] = "application/json";
    try {
      return await fetch(apiUrl(path), {
        method, headers: h, body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store", credentials: "same-origin", signal: ctrl.signal,
      });
    } finally {
      clearTimeout(t);
    }
  }

  async function post(path, body) {
    let res;
    try {
      res = await api("POST", path, body);
    } catch (e) {
      throw new Error("Can't reach the page server. Try again in a moment.");
    }
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) throw new Error((data && data.error) || `The page server answered ${res.status}.`);
    return data || {};
  }

  function pollDelay() {
    return document.visibilityState === "hidden" ? 15000 : 2000;
  }

  function schedule(ms) {
    clearTimeout(timer);
    timer = setTimeout(poll, ms);
  }

  async function poll() {
    if (OFFLINE) return;
    if (polling) { pollAgain = true; return; }
    polling = true;
    clearTimeout(timer);
    let ok = false;
    let data = null;
    try {
      const res = await api("GET", "state", undefined, etag ? { "If-None-Match": etag } : {});
      if (res.status === 304) ok = true;
      else if (res.ok) {
        data = await res.json();
        etag = res.headers.get("ETag");
        ok = true;
      }
    } catch (e) { ok = false; }
    polling = false;
    if (data) {
      try {
        receive(data);
      } catch (e) { // a page bug, not a network failure: report it without pausing the buttons
        setTimeout(() => { throw e; });
      }
    }
    if (ok) {
      failures = 0;
      if (paused) { paused = false; render(); }
    } else {
      failures += 1;
      if (failures >= 2 && !paused) { paused = true; render(); }
    }
    if (pollAgain) { pollAgain = false; schedule(0); }
    else schedule(ok || document.visibilityState === "hidden" ? pollDelay() : RETRY_MS);
  }

  function receive(data) {
    const prev = snap;
    snap = data;
    if (prev && Array.isArray(prev.stages) && Array.isArray(data.stages)) {
      const said = [];
      for (const s of data.stages) {
        const p = prev.stages.find((x) => x.id === s.id);
        if (p && s.version !== null && s.version !== undefined && p.version !== s.version) {
          said.push(`Updated: ${s.label} version ${s.version}`);
        }
      }
      if (said.length) say(said.join(". "));
    }
    for (const id of Object.keys(pendingApprove)) {
      const s = stageById(id);
      if (!s || s.waiting_for_claude || !s.needs_yes || s.version !== pendingApprove[id]) delete pendingApprove[id];
    }
    if (data.show) showStage(data.show, false);
    render();
  }

  function say(text) {
    nodes.announce.textContent = text;
  }

  // ------------------------------------------------------------------ stages and what is shown

  function stageById(id) {
    return snap && Array.isArray(snap.stages) ? snap.stages.find((s) => s.id === id) || null : null;
  }

  function hashStage() {
    try {
      return decodeURIComponent(location.hash.slice(1));
    } catch (e) { // a malformed #%zz: show the current stage
      return "";
    }
  }

  function shownStage() {
    const wanted = stageById(hashStage());
    if (wanted && wanted.status !== "locked") return wanted;
    return stageById(snap.current) || snap.stages[0];
  }

  function showStage(id, byUser) {
    const s = stageById(id);
    if (!s || s.status === "locked") return;
    const base = location.href.split("#")[0];
    if (id === snap.current) history.replaceState(null, "", base); // follow the work as it moves on
    else if (byUser) location.hash = id;
    else history.replaceState(null, "", `${base}#${encodeURIComponent(id)}`);
    render();
  }

  function decisionsProblem() {
    return ((snap && snap.problems) || []).find((p) => /decisions\.json/.test(String(p))) || null;
  }

  function isWaiting(s) {
    return !!s.waiting_for_claude || (pendingApprove[s.id] !== undefined && pendingApprove[s.id] === s.version);
  }

  // A hook a stage renderer may carry (see stages.js), or null.
  function hook(s, name) {
    const draw = BSA.stages[s.id];
    return draw && typeof draw[name] === "function" ? draw[name] : null;
  }

  // The stage needs a yes and asks the user for it: a stage whose answer comes from elsewhere (the engine from the
  // user's settings) needs Claude to record it, not a question on the page.
  function asksYes(s) {
    if (!s.needs_yes) return false;
    const asks = hook(s, "asks");
    if (!asks || s.content === null || s.content === undefined) return true;
    try {
      return asks(s.content, s) !== false;
    } catch (e) {
      return true;
    }
  }

  // The Approve / Send changes bar is on: a stage that needs a yes, answerable, with no answer on its way.
  function answerable(s) {
    return asksYes(s) && !decisionsProblem() && !isWaiting(s);
  }

  function stageNotes(s) {
    const mine = ((snap && snap.notes) || []).filter((n) => n.stage === s.id);
    return mine.concat(offlineNotes.filter((n) => n.stage === s.id));
  }

  // ------------------------------------------------------------------ actions

  function activeKey() {
    const a = document.activeElement;
    return a && a.dataset ? a.dataset.k || null : null;
  }

  // Focus the first of these data-k targets that is on the page and usable; false when none is.
  function focusFirst(keys) {
    for (const k of keys) {
      if (!k) continue;
      const node = document.querySelector(`[data-k="${CSS.escape(k)}"]`);
      if (node && !node.disabled) {
        node.focus();
        return true;
      }
    }
    return false;
  }

  function overallKey(stage) {
    return `note-overall:${stage}:overall:null`;
  }

  // Run one action. The buttons are disabled while it runs, which drops keyboard focus, so the focused control is
  // remembered and focused again afterwards; when the action removed it, focus goes to the first of `fallback`.
  // `after(ok)` runs before the final render.
  async function act(fn, opts) {
    if (busy || paused) return false;
    const o = opts || {};
    const key = activeKey();
    busy = true;
    actionError = null;
    notice = null;
    render();
    let ok = false;
    try {
      await fn();
      ok = true;
    } catch (e) {
      actionError = e.message || String(e);
    }
    busy = false;
    if (o.after) o.after(ok);
    render();
    if (key) focusFirst([key, ...(o.fallback || [])]);
    if (!OFFLINE) schedule(0);
    return ok;
  }

  function decide(stage, decision, choice) {
    const s = stageById(stage);
    if (!s) return Promise.resolve(false);
    if (OFFLINE) {
      const text = decision === "approve"
        ? answerText("approve", { label: s.label, version: s.version, choice })
        : answerText("changes", { label: s.label, version: s.version,
          notes: stageNotes(s).filter((n) => n.status === "open") });
      return copyAnswer(text);
    }
    return act(async () => {
      const body = { stage, version: s.version, decision };
      if (choice) body.choice = choice;
      await post("decision", body);
      if (decision === "approve") pendingApprove[stage] = s.version;
    }, { fallback: [`answer-h:${stage}`, overallKey(stage)] });
  }

  // Approve, with the choice the stage asks for (the engine) when it has one; nothing is sent until it is picked.
  function approve(s) {
    const pick = hook(s, "choice");
    if (!pick) return decide(s.id, "approve");
    const got = pick(s.content, s) || {};
    if (got.error) {
      actionError = got.error;
      notice = null;
      render();
      return Promise.resolve(false);
    }
    return decide(s.id, "approve", got.choice);
  }

  function openEditor(s, kind, id, label) {
    editor = { stage: s.id, kind, id: id === undefined ? null : id, version: s.version, label: label || null, text: "" };
    actionError = null;
    notice = null;
    render();
    const ta = $("bsa-note-text");
    if (ta) ta.focus();
  }

  function closeEditor() {
    const back = editor ? `${editor.kind === "overall" ? "note-overall" : "note"}:${editor.stage}:${editor.kind}:${editor.id}` : null;
    editor = null;
    render();
    const btn = back && document.querySelector(`[data-k="${CSS.escape(back)}"]`);
    if (btn) btn.focus();
  }

  function noteError(text) {
    if (!text.trim()) return "Write a note first.";
    const n = [...text].length;
    if (n > MAX_NOTE) return `A note can be at most ${MAX_NOTE} characters; this one is ${n}.`;
    return null;
  }

  async function saveNote() {
    if (!editor) return;
    const s = stageById(editor.stage);
    const bad = noteError(editor.text);
    if (bad) {
      actionError = bad;
      render();
      return;
    }
    const target = { kind: editor.kind, id: editor.id, version: editor.version };
    if (OFFLINE) {
      const note = { id: `local${offlineNotes.length + 1}`, stage: editor.stage, target, text: editor.text,
        by: "page", status: "open", reply: null };
      offlineNotes.push(note);
      editor = null;
      await copyAnswer(answerText("note", { label: s ? s.label : note.stage, version: target.version, note }));
      return;
    }
    const ed = editor;
    const origin = ed.kind === "overall" ? overallKey(ed.stage) : `note:${ed.stage}:${ed.kind}:${ed.id}`;
    await act(() => post("notes", { op: "add", stage: ed.stage, target, text: ed.text }), {
      after: (ok) => { if (ok && editor === ed) editor = null; },
      fallback: [origin, overallKey(ed.stage)],
    });
  }

  // ------------------------------------------------------------------ helpers handed to stage renderers

  function fileUrl(ref) {
    const rel = typeof ref === "string" ? ref : ref && ref.file;
    if (!rel) return "";
    const path = String(rel).split("/").map(encodeURIComponent).join("/");
    if (OFFLINE) return `../${path}`;
    return `/files/${encodeURIComponent(KEY)}/${path}?t=${encodeURIComponent(TOKEN)}`;
  }

  function copyButton(text, label) {
    return el("button", { type: "button", class: "btn small", onclick: () => copyAnswer(text) }, label || "Copy");
  }

  function noteButton(s, kind, id, label) {
    return el("button", {
      type: "button", class: "note-btn", "data-act": "1", "data-k": `note:${s.id}:${kind}:${id}`,
      onclick: () => openEditor(s, kind, id),
    }, label || `Note on ${kind} ${id}`);
  }

  function makeCtx(s) {
    return {
      el, stage: s, version: s.version, offline: OFFLINE, fileUrl, copyButton,
      noteButton: (kind, id, label) => noteButton(s, kind, id, label),
      decide: (stage, decision, choice) => decide(stage, decision, choice),
    };
  }

  // ------------------------------------------------------------------ rendering

  function renderHeader() {
    const short = snap.short || {};
    const title = String(short.title || KEY || "Review");
    nodes.title.textContent = title;
    nodes.sub.textContent = short.key ? `Short ${short.key}` : "";
    const yes = snap.stages.filter(asksYes).length;
    const current = stageById(snap.current);
    document.title = yes ? `(${yes}) Needs your yes · ${title}` : `${current ? current.label : "Review"} · ${title}`;
    renderLive();
  }

  function renderLive() {
    nodes.live.classList.toggle("off", OFFLINE || paused);
    nodes.live.lastElementChild.textContent = OFFLINE ? "Offline view" : paused ? "Not connected" : "Live";
  }

  function renderNav(shown) {
    const ol = el("ol");
    snap.stages.forEach((s, i) => {
      const locked = s.status === "locked";
      const btn = el("button", {
        type: "button", class: `stage-btn${s.id === shown.id ? " shown" : ""}`, "data-k": `stage:${s.id}`,
        disabled: locked, "aria-current": s.status === "current" ? "step" : null,
        title: asksYes(s) ? "Needs your yes" : null,
        onclick: () => showStage(s.id, true),
      },
      el("span", { class: "n", "aria-hidden": "true" }, s.status === "done" ? "✓" : String(i + 1)),
      s.label,
      asksYes(s) ? el("span", { class: "dot", "aria-hidden": "true" }) : null);
      ol.append(el("li", null, btn));
    });
    nodes.nav.replaceChildren(ol);
  }

  function renderBanners() {
    const rows = [];
    if (OFFLINE) rows.push(["banner", null, TEXT.offline]);
    if (paused) rows.push(["banner alert", "alert", TEXT.unreachable]);
    if (OFFLINE && !hasStages()) rows.push(["banner", null, "This saved page has nothing to show."]);
    for (const p of (snap && snap.problems) || []) rows.push(["banner", null, String(p)]);
    const key = JSON.stringify(rows);
    if (key === bannerKey) return; // unchanged: an alert is not announced again on every update
    bannerKey = key;
    nodes.banners.replaceChildren(...rows.map(([cls, role, text]) => el("p", { class: cls, role }, text)));
  }

  function stageMeta(s) {
    const bits = [];
    if (s.version !== null && s.version !== undefined) bits.push(`version ${s.version}`);
    bits.push(asksYes(s) ? "needs your yes" : s.status === "done" ? "done" : s.status === "current" ? "in progress" : "not started");
    return bits.join(" · ");
  }

  function renderStage(s) {
    const kids = [el("div", { class: "head" },
      el("h2", { id: "bsa-stage-h" }, s.label), el("span", { class: "meta" }, stageMeta(s)))];
    if (s.problem) kids.push(el("p", { class: "problem" }, String(s.problem)));
    if (s.content !== null && s.content !== undefined) {
      const draw = BSA.stages[s.id];
      if (typeof draw === "function") {
        try {
          kids.push(draw(s.content, makeCtx(s)));
        } catch (e) {
          kids.push(el("p", { class: "problem" }, `This stage couldn't be shown: ${e.message || e}`));
        }
      } else {
        kids.push(el("p", { class: "muted" }, "The details of this stage show here soon."));
      }
    } else if (!s.problem) {
      kids.push(el("p", { class: "muted" }, s.status === "current" ? "Claude is working on this stage." : "Nothing to show yet."));
    }
    nodes.stage.replaceChildren(...kids);
  }

  function answerCard(s) {
    const d = s.decision;
    const v = s.version;
    const kids = [];
    if (isWaiting(s)) {
      kids.push(el("p", { class: "state" }, TEXT.waiting));
    } else if (asksYes(s)) {
      const problem = decisionsProblem();
      if (problem) {
        kids.push(el("p", { class: "problem" }, `Answer in the chat for now: ${problem}`));
      } else {
        if (d && d.decision === "changes" && d.version === v) {
          kids.push(el("p", { class: "muted" }, "Your changes went to Claude."));
        }
        kids.push(el("p", { class: "muted" }, "Approve it, or add notes and send the changes."));
        kids.push(el("div", { class: "btns" },
          el("button", { type: "button", class: "btn primary", "data-act": "1", "data-k": `approve:${s.id}`,
            onclick: () => approve(s) }, OFFLINE ? "Copy: Approve" : "Approve"),
          el("button", { type: "button", class: "btn", "data-act": "1", "data-k": `changes:${s.id}`,
            onclick: () => decide(s.id, "changes") }, OFFLINE ? "Copy: Send changes" : "Send changes")));
      }
    } else if (d && d.decision === "approve" && d.version === v && d.applied) {
      kids.push(el("p", { class: "state ok" }, el("span", { class: "tick", "aria-hidden": "true" }, "✓"),
        el("span", null, TEXT.approved)));
    }
    if (!kids.length) return null;
    return el("div", { class: "card" },
      el("div", { class: "head" }, el("h3", { tabindex: "-1", "data-k": `answer-h:${s.id}` },
        asksYes(s) ? "Needs your yes" : "Your answer")), kids);
  }

  function editorBlock(s) {
    if (!editor || editor.stage !== s.id) return null;
    const what = editor.kind === "overall" ? `Note on ${s.label} (overall)` : `Note on ${s.label}: ${targetWords(editor)}`;
    const ta = el("textarea", { id: "bsa-note-text", rows: "4", "data-k": "editor" });
    ta.value = editor.text;
    ta.addEventListener("input", () => { if (editor) editor.text = ta.value; });
    ta.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); closeEditor(); } });
    return el("div", { class: "editor" },
      el("label", { class: "lbl", for: "bsa-note-text" }, what), ta,
      el("div", { class: "btns" },
        el("button", { type: "button", class: "btn primary", "data-act": "1", "data-k": "editor-save",
          onclick: saveNote }, OFFLINE ? "Copy: Note" : "Save note"),
        el("button", { type: "button", class: "btn ghost", "data-k": "editor-cancel", onclick: closeEditor }, "Cancel")));
  }

  function noteItem(s, n) {
    const t = n.target || {};
    const kids = [
      el("div", { class: "note-top" },
        el("span", { class: "note-target" }, t.kind === "overall" ? "Overall" : targetWords(t)),
        el("span", { class: `chip ${n.status}` }, STATUS[n.status] || String(n.status))),
      el("p", { class: "note-text" }, String(n.text)),
    ];
    if (n.reply) kids.push(el("p", { class: "note-reply" }, `Claude: ${n.reply}`));
    if (n.status === "open" && !OFFLINE) {
      const btns = [];
      if (!answerable(s)) {
        btns.push(el("button", { type: "button", class: "btn small", "data-act": "1", "data-k": `send:${n.id}`,
          onclick: () => act(() => post("notes", { op: "send", ids: [n.id] }), { fallback: [overallKey(s.id)] }) },
          "Send"));
      }
      btns.push(el("button", { type: "button", class: "btn ghost small", "data-act": "1", "data-k": `withdraw:${n.id}`,
        onclick: () => act(() => post("notes", { op: "withdraw", id: n.id }), { fallback: [overallKey(s.id)] }) },
        "Remove"));
      kids.push(el("div", { class: "btns" }, btns));
    }
    return el("li", null, kids);
  }

  function notesCard(s) {
    const list = stageNotes(s);
    return el("div", { class: "card" },
      el("div", { class: "head" }, el("h3", null, "Notes"), el("span", { class: "meta" }, String(list.length))),
      el("button", { type: "button", class: "note-btn", "data-act": "1", "data-k": overallKey(s.id),
        onclick: () => openEditor(s, "overall", null) }, `Add a note on ${s.label}`),
      editorBlock(s),
      list.length ? el("ul", { class: "notes" }, list.map((n) => noteItem(s, n)))
        : el("p", { class: "muted" }, "No notes yet."));
  }

  function renderSide(s) {
    const kids = [];
    if (actionError) kids.push(el("p", { class: "err", role: "alert" }, actionError));
    if (notice) kids.push(el("p", { class: "muted" }, notice));
    kids.push(answerCard(s), notesCard(s));
    nodes.side.replaceChildren(...kids.filter(Boolean));
  }

  function hasStages() {
    return !!snap && Array.isArray(snap.stages) && snap.stages.length > 0;
  }

  function render() {
    if (!hasStages()) { // nothing to draw yet (or a bad saved page): the offline and unreachable banners still show
      renderBanners();
      renderLive();
      return;
    }
    const active = document.activeElement;
    const focusKey = active && active.dataset ? active.dataset.k : null;
    const sel = active && active.id === "bsa-note-text" ? [active.selectionStart, active.selectionEnd] : null;
    const shown = shownStage();
    renderHeader();
    renderNav(shown);
    renderBanners();
    renderStage(shown);
    renderSide(shown);
    for (const b of document.querySelectorAll("[data-act]")) b.disabled = paused || busy;
    if (focusKey) {
      const again = document.querySelector(`[data-k="${CSS.escape(focusKey)}"]`);
      if (again && again !== document.activeElement && !again.disabled) {
        again.focus({ preventScroll: true });
        if (sel && again.setSelectionRange) again.setSelectionRange(sel[0], sel[1]);
      }
    }
    document.documentElement.dataset.ready = "1";
  }

  // ------------------------------------------------------------------ theme

  function storedTheme() {
    try {
      const v = localStorage.getItem(THEME_KEY);
      return v === "dark" || v === "light" ? v : null;
    } catch (e) {
      return null;
    }
  }

  function effectiveTheme() {
    const set = document.documentElement.dataset.theme;
    if (set === "dark" || set === "light") return set;
    return window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function showTheme() {
    nodes.theme.setAttribute("aria-pressed", String(effectiveTheme() === "dark"));
  }

  const saved = storedTheme();
  if (saved) document.documentElement.dataset.theme = saved;
  showTheme();
  nodes.theme.addEventListener("click", () => {
    const next = effectiveTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* remembered for this visit only */ }
    showTheme();
  });
  if (window.matchMedia) matchMedia("(prefers-color-scheme: dark)").addEventListener("change", showTheme);

  // ------------------------------------------------------------------ start

  Object.assign(BSA, { answerText, pollDelay, el, mode: OFFLINE ? "static" : "server" });
  window.addEventListener("hashchange", render);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && !OFFLINE) schedule(0);
  });
  if (OFFLINE) {
    render();
  } else {
    post("hello", {}).catch(() => { /* the state poll says whether the server is there */ });
    poll();
  }
})();
