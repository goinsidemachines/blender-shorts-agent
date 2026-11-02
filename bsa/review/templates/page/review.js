/* Review tools for the stage renderers. BSA.review.timeline(ctx, tl, opts) draws the clickable timeline under a
   video: a row of markers per kind (shots, captions, sounds, findings, holds, the closing card), a frame scrubber, a
   playhead that follows the video, and a note on the moment on screen. Markup with ctx.el only (text, never HTML).
   Frames are 1-based and inclusive; frame f is on screen for t in [(f-1)/fps, f/fps), and a seek goes to its middle. */
(function () {
  "use strict";
  var BSA = (window.BSA = window.BSA || {});
  var review = (BSA.review = BSA.review || {});

  var ROWS = ["shots", "captions", "sounds", "findings", "holds", "closing"];
  var NAMES = { shots: "Shots", captions: "Captions", sounds: "Sounds", findings: "Findings", holds: "Holds",
    closing: "Closing card" };
  var STALE = "This video was cut from an older timing; run the edit again to line up the markers.";

  var rowsOn = null; // row -> shown: the user's choice, kept across live updates and shared by every timeline
  var cache = {}; // timeline key -> {json, video, version, node, stop, applyRows}: the track is reused while unchanged

  function startRows() {
    if (rowsOn) return rowsOn;
    var phone = window.matchMedia ? window.matchMedia("(max-width: 767.98px)").matches : window.innerWidth < 768;
    rowsOn = {};
    ROWS.forEach(function (r) { rowsOn[r] = !phone || r === "shots" || r === "findings"; });
    return rowsOn;
  }

  function num(v, fallback) {
    var n = Number(v);
    return isFinite(n) ? n : fallback;
  }

  // Every row's markers: {f0, f1, name, cls}, in frames of the video, clamped to it.
  function markerRows(tl, frames) {
    var t = tl.tracks || {};
    function clamp(f) { return Math.min(Math.max(Math.round(num(f, 1)), 1), frames); }
    function m(f0, f1, name, cls) {
      var a = clamp(f0);
      return { f0: a, f1: Math.max(a, clamp(f1 === undefined || f1 === null ? f0 : f1)), name: name, cls: cls || "" };
    }
    var span = function (a, b) { return "frames " + a + " to " + b; };
    var closing = t.closing ? [t.closing] : [];
    return {
      shots: (t.shots || []).map(function (s) {
        return m(s.f0, s.f1, "Shot " + s.id + (s.title ? ": " + s.title : "") + ", " + span(s.f0, s.f1));
      }),
      captions: (t.captions || []).map(function (c) { return m(c.f0, c.f1, "Caption: " + c.text + ", frame " + c.f0); }),
      sounds: (t.sounds || []).map(function (x) { return m(x.f, x.f, "Sound: " + x.label + ", frame " + x.f); }),
      findings: (t.findings || []).map(function (x) {
        return m(x.f, x.f1, "Finding: " + x.text + ", frame " + x.f, x.severity === "error" ? "error" : "warning");
      }),
      holds: (t.holds || []).map(function (h) { return m(h.f0, h.f1, "Hold in shot " + h.shot + ", " + span(h.f0, h.f1)); }),
      closing: closing.map(function (c) { return m(c.f0, c.f1, "Closing card, " + span(c.f0, c.f1)); }),
    };
  }

  function build(ctx, tl, o, key) {
    var el = ctx.el;
    var frames = Math.max(1, Math.round(num(tl.frames, 1)));
    var fps = num(tl.fps, 0) > 0 ? num(tl.fps, 30) : 30;
    var video = o.videoNode || null;
    var noteId = typeof o.noteId === "function" ? o.noteId : String;
    var shots = (tl.tracks && tl.tracks.shots) || [];
    var state = { frame: 0, alive: true, rvfc: null, dragging: false };
    var uid = "bsa-tl-" + String(key).replace(/[^A-Za-z0-9_-]/g, "_");

    function clamp(f) { return Math.min(Math.max(f, 1), frames); }
    function frameAt(t) { return clamp(Math.floor(num(t, 0) * fps + 1e-6) + 1); }
    function left(f) { return (100 * (f - 1) / frames).toFixed(4) + "%"; }
    function shotAt(f) {
      return shots.find(function (s) { return s.f0 <= f && f <= s.f1; }) || null;
    }

    var fill = el("span", { class: "tl-fill" });
    var knob = el("span", { class: "tl-knob" });
    var slider = el("div", { class: "tl-scrub", role: "slider", tabindex: "0", "aria-label": "Frame",
      "aria-valuemin": "1", "aria-valuemax": String(frames), "aria-valuenow": "1", "data-k": "tl:" + key + ":slider" },
    fill, knob);
    var head = el("span", { class: "tl-head", "aria-hidden": "true" });
    var readout = el("p", { class: "tl-now" });

    function show(f) {
      if (f === state.frame) return;
      state.frame = f;
      var s = shotAt(f);
      var at = left(f);
      slider.setAttribute("aria-valuenow", String(f));
      slider.setAttribute("aria-valuetext", "Frame " + f + " of " + frames + (s ? ", shot " + s.id : ""));
      fill.style.width = at;
      knob.style.left = at;
      head.style.left = at;
      readout.textContent = "Frame " + f + " of " + frames + " · " + ((f - 1) / fps).toFixed(2) + " s" +
        (s ? " · shot " + s.id : "");
    }

    function seek(f) {
      f = clamp(Math.round(f));
      if (video) {
        try { video.currentTime = (f - 0.5) / fps; } catch (e) { /* not seekable yet: the playhead still moves */ }
      }
      show(f);
    }

    function fromVideo() {
      if (state.alive && !state.dragging) show(frameAt(video.currentTime));
    }

    if (video) {
      if (typeof video.requestVideoFrameCallback === "function") {
        var tick = function (now, meta) {
          if (!state.alive) return;
          if (!state.dragging) show(frameAt(meta && typeof meta.mediaTime === "number" ? meta.mediaTime : video.currentTime));
          state.rvfc = video.requestVideoFrameCallback(tick);
        };
        state.rvfc = video.requestVideoFrameCallback(tick);
      } else {
        video.addEventListener("timeupdate", fromVideo);
      }
      video.addEventListener("seeked", fromVideo);
      video.addEventListener("loadedmetadata", fromVideo);
    }

    // The scrubber: a click or drag picks a frame, the arrow keys step one frame.
    function frameFromX(e) {
      var r = slider.getBoundingClientRect();
      var x = r.width ? (e.clientX - r.left) / r.width : 0;
      return clamp(Math.floor(x * frames) + 1);
    }
    slider.addEventListener("pointerdown", function (e) {
      state.dragging = true;
      if (slider.setPointerCapture) {
        try { slider.setPointerCapture(e.pointerId); } catch (err) { /* capture is a nicety */ }
      }
      seek(frameFromX(e));
    });
    slider.addEventListener("pointermove", function (e) { if (state.dragging) seek(frameFromX(e)); });
    ["pointerup", "pointercancel", "lostpointercapture"].forEach(function (type) {
      slider.addEventListener(type, function () { state.dragging = false; });
    });
    slider.addEventListener("keydown", function (e) {
      var f = state.frame || 1;
      var to = { ArrowRight: f + 1, ArrowUp: f + 1, ArrowLeft: f - 1, ArrowDown: f - 1, PageUp: f + 10,
        PageDown: f - 10, Home: 1, End: frames }[e.key];
      if (to === undefined) return;
      e.preventDefault();
      seek(to);
    });

    var kids = [];
    var applyRows = function () {};
    if (tl.stale) {
      kids.push(el("p", { class: "problem" }, STALE));
    } else {
      var on = startRows();
      var items = markerRows(tl, frames);
      var toggles = {};
      var rows = {};
      ROWS.forEach(function (row) {
        var list = items[row];
        var nameId = uid + "-" + row;
        var marks = [];
        function rove(i, focus) {
          marks.forEach(function (b, j) { b.tabIndex = i === j ? 0 : -1; });
          if (focus) marks[i].focus();
        }
        list.forEach(function (m, i) {
          var width = (100 * (m.f1 - m.f0 + 1) / frames).toFixed(4) + "%";
          marks.push(el("button", { type: "button", class: "tl-mark tl-" + row + (m.cls ? " " + m.cls : ""),
            "aria-label": m.name, title: m.name, tabindex: i === 0 ? "0" : "-1", "data-k": "tl:" + key + ":" + row + ":" + i,
            style: "left:" + left(m.f0) + ";width:max(2px, " + width + ")",
            onclick: function () { rove(i, false); seek(m.f0); } }));
        });
        var lane = el("div", { class: "tl-lane", role: "group", "aria-labelledby": nameId,
          onkeydown: function (e) {
            var at = marks.indexOf(document.activeElement);
            if (at < 0) return;
            var to = { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: marks.length - 1 }[e.key];
            if (to === undefined) return;
            e.preventDefault();
            rove(Math.min(Math.max(to, 0), marks.length - 1), true);
          } }, marks.length ? marks : el("span", { class: "tl-none" }, "None"));
        rows[row] = el("div", { class: "tl-row" }, el("p", { class: "tl-name", id: nameId }, NAMES[row]), lane);
        toggles[row] = el("button", { type: "button", class: "tl-toggle", "aria-pressed": "true",
          "data-k": "tl:" + key + ":toggle:" + row,
          onclick: function () {
            on[row] = !on[row];
            Object.keys(cache).forEach(function (k) { cache[k].applyRows(); });
            applyRows();
          } }, NAMES[row]);
      });
      applyRows = function () {
        ROWS.forEach(function (row) {
          toggles[row].setAttribute("aria-pressed", String(!!on[row]));
          rows[row].hidden = !on[row];
        });
      };
      applyRows();
      kids.push(el("div", { class: "tl-toggles", role: "group", "aria-label": "Rows to show" },
        ROWS.map(function (row) { return toggles[row]; })));
      kids.push(el("div", { class: "tl-body" }, slider, ROWS.map(function (row) { return rows[row]; }), head));
    }
    if (tl.stale) kids.push(el("div", { class: "tl-body" }, slider, head));
    if (tl.problem) kids.push(el("p", { class: "muted tl-problem" }, String(tl.problem)));
    kids.push(el("div", { class: "tl-foot" }, readout,
      el("button", { type: "button", class: "note-btn", "data-act": "1", "data-k": "tl:" + key + ":note",
        onclick: function () {
          var f = state.frame || 1;
          var s = shotAt(f);
          ctx.noteAt("frame", noteId(f), "Note at frame " + f + (s ? " (shot " + s.id + ")" : ""), "", s ? s.id : null);
        } }, "Note at this moment")));
    var unplaced = tl.unplaced || [];
    if (unplaced.length) {
      kids.push(el("section", { class: "tl-unplaced", "aria-labelledby": uid + "-unplaced" },
        el("h4", { id: uid + "-unplaced" }, "Findings without a place on the timeline"),
        el("ul", null, unplaced.map(function (u) {
          var check = String(u.check || "").replace(/_/g, " ");
          return el("li", null,
            el("p", null, String(u.text || "")),
            el("p", { class: "muted" }, String(u.why || "")),
            ctx.noteButton("finding", u.id, "+ Note on finding: " + check + (u.shot ? ", shot " + u.shot : "")));
        }))));
    }
    var node = el("div", { class: "tl-track", "data-frames": String(frames) }, kids);
    show(video ? frameAt(video.currentTime) : 1); // never seeks: a rebuilt track leaves playback where it was
    return {
      node: node,
      applyRows: function () { applyRows(); },
      stop: function () {
        state.alive = false;
        if (video) {
          if (state.rvfc !== null && typeof video.cancelVideoFrameCallback === "function") video.cancelVideoFrameCallback(state.rvfc);
          video.removeEventListener("timeupdate", fromVideo);
          video.removeEventListener("seeked", fromVideo);
          video.removeEventListener("loadedmetadata", fromVideo);
        }
      },
    };
  }

  // The timeline of one video. opts: {videoNode, noteId: (frame) => string, key}; `key` names this timeline on the
  // page (one per video), so a live update with the same data and video gets back the same element.
  review.timeline = function (ctx, tl, opts) {
    var o = opts || {};
    var key = String(o.key || "final");
    var json = JSON.stringify(tl);
    var c = cache[key];
    if (c && c.json === json && c.video === (o.videoNode || null) && c.version === ctx.version) return c.node;
    if (c) c.stop();
    var t = build(ctx, tl, o, key);
    cache[key] = { json: json, video: o.videoNode || null, version: ctx.version, node: t.node, stop: t.stop,
      applyRows: t.applyRows };
    return t.node;
  };

  // The voice check: per line what was said against what should have been, words that differ marked.
  function diffCell(ctx, row) {
    var ops = row.diff || [];
    var kids = [];
    ops.forEach(function (op, i) {
      var word = String(op[1]);
      if (i) kids.push(" ");
      kids.push(op[0] === "same" ? word : ctx.el(op[0] === "missing" ? "del" : "mark", null, word));
    });
    return kids;
  }

  review.voice = function (ctx, v) {
    var el = ctx.el;
    var lines = (v && v.lines) || [];
    var out = [];
    if (v && v.why_not) out.push(el("p", { class: "note" }, String(v.why_not)));
    if (!lines.length) return el("div", { class: "voice" }, out);
    var rows = lines.map(function (row) {
      var verdict = [];
      if (row.stale) verdict.push(el("p", { class: "muted" }, "Not checked since the line changed"));
      else if (row.waiting) verdict.push(el("p", { class: "muted" }, "Waiting for a new take"));
      else if (row.ok === false) verdict.push(el("p", { class: "voice-bad" }, "Didn't pass the check"));
      var unheard = row.stale || row.waiting;
      var heard = row.said === null || row.said === undefined || unheard ? null : String(row.said);
      var diff = row.diff ? diffCell(ctx, row) : [];
      return el("tr", { "data-line": String(row.id) },
        el("td", null, el("strong", null, String(row.id)), el("br"), String(row.text)),
        el("td", null, heard === null ? el("span", { class: "muted" }, unheard ? "Not heard yet" : row.ok === null ? "Not checked" : "") : heard),
        el("td", null, diff, verdict,
          ctx.noteButton("line", row.id, "Re-take: " + row.id, "Re-take this line")));
    });
    out.push(el("div", { class: "tablewrap", role: "region", "aria-label": "Voice check", tabindex: "0" },
      el("table", null,
        el("thead", null, el("tr", null, ["Line", "What was said", "Difference"].map(function (h) {
          return el("th", { scope: "col" }, h);
        }))),
        el("tbody", null, rows))));
    return el("div", { class: "voice" }, out);
  };

  // The fact panel: per line the facts it cites and what is wrong, then every number on screen and the fact behind it.
  var factsOpen = null; // the user's choice, kept across live updates; null until they toggle it

  function factChip(ctx, f) {
    var label = String(f.id);
    var chip = f.is_link
      ? ctx.el("a", { class: "chip", href: String(f.source), target: "_blank", rel: "noopener noreferrer",
        title: String(f.claim || "") }, label)
      : ctx.el("span", { class: "chip", title: String(f.claim || "") + " (" + String(f.source || "") + ")" }, label);
    return chip;
  }

  review.facts = function (ctx, panel) {
    var el = ctx.el;
    var lines = (panel && panel.lines) || [];
    var rows = (panel && panel.on_screen) || [];
    if (!lines.length && !rows.length) return null;
    var items = lines.map(function (ln) {
      var flags = ln.flags || [];
      return el("li", { class: "fact-line" + (flags.length ? " flagged" : ""), "data-line": String(ln.id) },
        el("p", { title: String(ln.text) }, el("strong", null, "Line " + ln.id)),
        el("p", { class: "fact-chips" },
          (ln.facts || []).length ? (ln.facts || []).map(function (f) { return factChip(ctx, f); })
            : el("span", { class: "muted" }, "Cites no fact")),
        flags.map(function (w) { return el("p", { class: "voice-bad" }, String(w)); }),
        flags.length ? ctx.noteButton("line", ln.id, "Needs a source: " + ln.id, "Needs a source") : null);
    });
    var table = null;
    if (rows.length) {
      table = el("div", { class: "tablewrap", role: "region", "aria-label": "Numbers on screen", tabindex: "0" },
        el("table", null,
          el("thead", null, el("tr", null, ["Where", "Shown", "Number", "Fact"].map(function (h) {
            return el("th", { scope: "col" }, h);
          }))),
          el("tbody", null, rows.map(function (r) {
            return el("tr", { class: r.ok ? "" : "flagged" },
              el("td", null, String(r.where)), el("td", null, String(r.text)), el("td", null, String(r.number)),
              el("td", null, r.ok ? String(r.fact) : el("span", { class: "voice-bad" }, "No fact has this number")));
          }))));
    }
    var card = el("details", { class: "card facts-panel" },
      el("summary", null, "Facts behind every line"),
      el("ul", { class: "fact-lines" }, items),
      rows.length ? el("h4", null, "Numbers on screen") : null, table);
    card.open = factsOpen === null ? !!panel.flagged : factsOpen;
    card.addEventListener("toggle", function () { factsOpen = card.open; });
    return card;
  };

  review.STALE = STALE;
})();
