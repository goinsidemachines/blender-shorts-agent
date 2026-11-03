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
  var NO_EDIT = "This video has no timing record; run the edit again to line up the markers.";

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
      kids.push(el("p", { class: "problem" }, tl.no_edit ? NO_EDIT : STALE));
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
        } }, "Note at this moment"),
      // the final video only: a frame note asking Claude to make a thumbnail candidate from this moment
      key === "final" ? el("button", { type: "button", class: "note-btn", "data-act": "1", "data-k": "tl:" + key + ":thumb",
        onclick: function () {
          var f = state.frame || 1;
          var s = shotAt(f);
          ctx.noteAt("frame", noteId(f), "Thumbnail from frame " + f + (s ? " (shot " + s.id + ")" : ""),
            "Use this frame for the thumbnail", s ? s.id : null);
        } }, "Use this frame for the thumbnail") : null));
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

  var SOURCE_CHIPS = { take: "From a paragraph take", line: "Spoken on its own", fallback: "Spoken on its own" };

  function sourceChip(ctx, row) {
    var text = SOURCE_CHIPS[row.source];
    if (!text) return null;
    if (row.source === "fallback" && row.reason) text += ": " + String(row.reason);
    return ctx.el("p", { class: "src-chip src-" + row.source }, text);
  }

  // What the user's own voice tool printed that needs a look (its warnings and fallbacks), with where the full log is.
  function toolNotes(ctx, v) {
    var notes = (v && v.log_notes) || [];
    if (!notes.length) return null;
    var el = ctx.el;
    return el("section", { class: "voice-notes", "aria-labelledby": "voice-notes-h" },
      el("h3", { id: "voice-notes-h" }, "Your voice tool said"),
      el("ul", null, notes.map(function (n) { return el("li", null, String(n)); })),
      v.log ? el("p", { class: "muted" }, "The full log is in ", el("code", null, String(v.log)), ".") : null);
  }

  review.voice = function (ctx, v) {
    var el = ctx.el;
    var lines = (v && v.lines) || [];
    var out = [];
    var notes = toolNotes(ctx, v);
    if (notes) out.push(notes);
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
        el("td", null, el("strong", null, String(row.id)), el("br"), String(row.text), sourceChip(ctx, row)),
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
  var factsOpen = {}; // stage id -> open, set only by the user's own toggle; absent until they touch it

  // A cited fact: its id (a link when the source is one), the claim, and the source as text when it isn't a link.
  function factItem(ctx, f) {
    var chip = f.is_link
      ? ctx.el("a", { class: "chip", href: String(f.source), target: "_blank", rel: "noopener noreferrer" }, String(f.id))
      : ctx.el("span", { class: "chip" }, String(f.id));
    return ctx.el("div", { class: "fact-item" },
      chip,
      ctx.el("p", { class: "muted" }, String(f.claim || "")),
      f.is_link ? null : ctx.el("p", { class: "muted" }, "Source: " + String(f.source || "")));
  }

  review.facts = function (ctx, panel) {
    var el = ctx.el;
    var lines = (panel && panel.lines) || [];
    var rows = (panel && panel.on_screen) || [];
    if (!lines.length && !rows.length) return null;
    var items = lines.map(function (ln) {
      var flags = ln.flags || [];
      var cited = ln.facts || [];
      return el("li", { class: "fact-line" + (flags.length ? " flagged" : ""), "data-line": String(ln.id) },
        el("p", null, el("strong", null, "Line " + ln.id)),
        el("p", { class: "muted" }, String(ln.text)),
        cited.length ? cited.map(function (f) { return factItem(ctx, f); }) : el("p", { class: "muted" }, "Cites no fact"),
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
    var stage = String((ctx.stage && ctx.stage.id) || "");
    var user = Object.prototype.hasOwnProperty.call(factsOpen, stage);
    var want = user ? factsOpen[stage] : !!panel.flagged; // untouched: open whenever something is flagged
    card.open = want;
    // The toggle event from setting `open` above arrives later; only a change from what we set is the user's.
    var set = want;
    card.addEventListener("toggle", function () {
      if (card.open === set) return;
      set = card.open;
      factsOpen[stage] = card.open;
    });
    return card;
  };

  // Before and after: Story, Storyboard and Final against a version the user saw (ctx.history, newest first).
  var compareOpen = {}; // stage id -> the version whose view is open, kept across live updates while it is offered
  var boardSide = "now"; // below 768 px each storyboard shot (and the post's thumbnail) shows one: "before" or "now"
  var cmpVideos = null; // {key, now, before}: the final's two compare videos, kept across live updates
  var showUnchanged = false; // the Story compare's unchanged lines, collapsed until the user opens them

  function playQuietly(v) {
    var p = v.play();
    if (p && typeof p.catch === "function") p.catch(function () { /* not loaded yet, or blocked: stays paused */ });
  }

  // Play, pause and seek on either video drive the other. The guard is the state itself: a video is only told to
  // do what it isn't already doing, so the event the follower then fires finds the leader in step and stops there.
  // While both play, the before video is put back in step with now's when it drifts more than 2 frames.
  function syncVideos(a, b, fps) {
    var tol = 0.5 / fps;
    a.addEventListener("timeupdate", function () {
      if (a.paused || b.paused || a.seeking || b.seeking) return;
      if (Math.abs(b.currentTime - a.currentTime) > 2 / fps) {
        try { b.currentTime = a.currentTime; } catch (e) { /* not seekable yet */ }
      }
    });
    function align(src, dst) {
      if (Math.abs(dst.currentTime - src.currentTime) > tol) {
        try { dst.currentTime = src.currentTime; } catch (e) { /* not seekable yet */ }
      }
    }
    function follow(src, dst) {
      src.addEventListener("play", function () { align(src, dst); if (dst.paused) playQuietly(dst); });
      src.addEventListener("pause", function () { if (!dst.paused) dst.pause(); align(src, dst); });
      src.addEventListener("seeking", function () { align(src, dst); });
    }
    follow(a, b);
    follow(b, a);
  }

  function compareVideos(ctx, content, ref, version) {
    var nowUrl = content.video ? ctx.fileUrl(content.video, ctx.version) : "";
    var beforeUrl = ctx.fileUrl(ref);
    var key = nowUrl + "#" + ctx.version + "|" + beforeUrl;
    if (cmpVideos && cmpVideos.key === key) return cmpVideos;
    if (cmpVideos) { // stop the old pair's downloads (see stages.js release)
      [cmpVideos.now, cmpVideos.before].forEach(function (v) {
        if (v) { v.pause(); v.removeAttribute("src"); v.load(); }
      });
    }
    var now = nowUrl ? ctx.el("video", { class: "player", controls: true, playsinline: true, preload: "metadata",
      src: nowUrl, "aria-label": "The final video now" }, "Your browser cannot play this video.") : null;
    var before = ctx.el("video", { class: "player", controls: true, playsinline: true, preload: "metadata", muted: true,
      src: beforeUrl, "aria-label": "The final video in version " + version + ", muted" },
    "Your browser cannot play this video.");
    before.muted = true;
    if (now) {
      var fps = content.timeline && Number(content.timeline.fps) > 0 ? Number(content.timeline.fps) : 30;
      syncVideos(now, before, fps);
    }
    cmpVideos = { key: key, now: now, before: before };
    [now, before].forEach(function (v) { if (v) v.addEventListener("play", pauseOthers); });
    return cmpVideos;
  }

  function inPair(v) {
    return !!cmpVideos && (v === cmpVideos.now || v === cmpVideos.before);
  }

  // One sound at a time: the compare pair playing pauses every other video on the page (the main player), and any
  // other video starting pauses the pair.
  function pauseOthers() {
    document.querySelectorAll("video").forEach(function (v) { if (!inPair(v) && !v.paused) v.pause(); });
  }
  document.addEventListener("play", function (e) {
    var v = e.target;
    if (!cmpVideos || !(v instanceof HTMLVideoElement) || inPair(v)) return;
    [cmpVideos.now, cmpVideos.before].forEach(function (w) { if (w && w.isConnected && !w.paused) w.pause(); });
  }, true); // media events don't bubble: caught on the way down

  // One line's words: same as they are, <del> for a word only version N had, <ins> for a new one, and a <mark>
  // holding both for a word that took another's place (worddiff's ["changed", new, old]). Each change also says so in
  // words for a screen reader.
  function diffWords(el, ops) {
    var kids = [];
    function sr(text) { return el("span", { class: "sr-only" }, text); }
    ops.forEach(function (op, i) {
      var word = String(op[1]);
      if (i) kids.push(" ");
      if (op[0] === "missing") kids.push(el("del", null, sr("removed: "), word));
      else if (op[0] === "extra") kids.push(el("ins", null, sr("added: "), word));
      else if (op[0] === "changed" && op.length > 2) {
        kids.push(el("mark", null, sr("changed from: "), el("del", null, String(op[2])), " ", sr("to: "),
          el("ins", null, word)));
      } else if (op[0] === "changed") kids.push(el("mark", null, sr("changed to: "), word));
      else kids.push(word);
    });
    return kids;
  }

  // How a line differs from version N: null when it doesn't.
  function lineChange(ops) {
    var kinds = {};
    ops.forEach(function (op) { kinds[op[0]] = true; });
    if (!kinds.missing && !kinds.extra && !kinds.changed) return null;
    if (!kinds.same && !kinds.changed && !kinds.extra) return "Removed line";
    if (!kinds.same && !kinds.changed && !kinds.missing) return "New line";
    return "Changed";
  }

  function storyView(ctx, h) {
    var el = ctx.el;
    var lines = (h.compare && h.compare.lines) || [];
    var rows = [];
    var quiet = [];
    lines.forEach(function (ln) {
      var ops = ln.diff || [];
      var change = lineChange(ops);
      var row = el("li", { class: "cmp-line" + (change ? " changed" : ""), "data-line": String(ln.id) },
        el("p", { class: "cmp-line-head" }, el("strong", null, String(ln.id)),
          change ? el("span", { class: "tag" }, change) : null),
        el("p", null, ops.length ? diffWords(el, ops) : el("span", { class: "muted" }, "(no words)")));
      if (!change) {
        row.hidden = !showUnchanged;
        quiet.push(row);
      }
      rows.push(row);
    });
    var toggle = null;
    if (quiet.length) {
      toggle = el("button", { type: "button", class: "btn small", "aria-expanded": String(showUnchanged),
        "data-k": "compare:story:unchanged",
        onclick: function () {
          showUnchanged = !showUnchanged;
          toggle.setAttribute("aria-expanded", String(showUnchanged));
          quiet.forEach(function (r) { r.hidden = !showUnchanged; });
        } }, "Show unchanged lines");
    }
    var changed = rows.length - quiet.length;
    return [
      el("p", { class: "muted" }, "Struck-through words were in version " + h.version + " and underlined words are new. " +
        "A highlighted pair is a word that took another's place: the old word struck through, then the new one."),
      el("p", null, (changed === 1 ? "1 line" : changed + " lines") + " changed" +
        (quiet.length ? ", " + quiet.length + " unchanged." : ".")),
      toggle ? el("div", { class: "btns cmp-btns" }, toggle) : null,
      el("ul", { class: "cmp-lines" }, rows),
    ];
  }

  function stillFig(ctx, side, label, ref, alt, none, tag) {
    var el = ctx.el;
    var url = ref ? ctx.fileUrl(ref, tag) : "";
    var pic = url ? el("img", { class: "still", src: url, alt: alt, loading: "lazy" })
      : el("div", { class: "ph" }, none || (side === "before" ? "No picture then" : "No picture now"));
    return el("figure", { class: "cmp-fig cmp-" + side }, pic, el("figcaption", null, label));
  }

  function storyboardView(ctx, h, stageId) {
    var el = ctx.el;
    var shots = (h.compare && h.compare.shots) || [];
    var beforeLabel = "Before (version " + h.version + ")";
    var list = el("ul", { class: "cmp-shots cmp-show-" + boardSide }, shots.map(function (s) {
      var name = "Shot " + s.id;
      return el("li", { class: "cmp-shot", "data-shot": String(s.id) },
        el("h4", null, name + (s.title ? ": " + s.title : "")),
        el("div", { class: "cmp-pair" },
          stillFig(ctx, "before", beforeLabel, s.before, name + " before (version " + h.version + ")"),
          stillFig(ctx, "now", "Now", s.after, name + " now")));
    }));
    var look = lookCompare(ctx, h);
    var sides = {};
    function pick(side) {
      boardSide = side;
      list.className = "cmp-shots cmp-show-" + side;
      if (look) look.list.className = "cmp-shots cmp-show-" + side;
      Object.keys(sides).forEach(function (k) { sides[k].setAttribute("aria-pressed", String(k === side)); });
    }
    [["before", "Show before"], ["now", "Show now"]].forEach(function (pair) {
      sides[pair[0]] = el("button", { type: "button", class: "btn small", "aria-pressed": String(boardSide === pair[0]),
        "data-k": "compare:" + stageId + ":side:" + pair[0], onclick: function () { pick(pair[0]); } }, pair[1]);
    });
    return [el("div", { class: "cmp-side", role: "group", "aria-label": "Which picture each shot shows" },
      sides.before, sides.now), list, look ? look.kids : null];
  }

  // The storyboard's look preview then and now: the style and set, and each look frame as a before/after pair.
  function lookCompare(ctx, h) {
    var el = ctx.el;
    var look = h.compare && h.compare.look;
    if (!look) return null;
    function words(side) { return side && side.style ? side.style + ", set: " + (side.set || "none") : "no look"; }
    var list = el("ul", { class: "cmp-shots cmp-show-" + boardSide }, (look.frames || []).map(function (f) {
      var name = "The look in shot " + f.shot;
      return el("li", { class: "cmp-shot cmp-look", "data-look": String(f.shot) },
        el("h5", null, name),
        el("div", { class: "cmp-pair" },
          stillFig(ctx, "before", "Before (version " + h.version + ")", f.before,
            name + " before (version " + h.version + ")"),
          stillFig(ctx, "now", "Now", f.after, name + " now", null, ctx.version)));
    }));
    return { list: list, kids: [
      el("h4", { class: "cmp-look-head" }, "Look"),
      el("p", { class: "cmp-look-line" },
        "Style/set before → now: " + words(look.before) + " → " + words(look.after)),
      list,
    ] };
  }

  function finalView(ctx, h, content) {
    var el = ctx.el;
    var cmp = h.compare || {};
    if (cmp.video && cmp.video.file) {
      var v = compareVideos(ctx, content, cmp.video, h.version);
      return [
        el("p", { class: "muted" }, "Play, pause or move either video and the other follows. Version " + h.version +
          " is muted."),
        el("div", { class: "cmp-videos" },
          el("figure", { class: "cmp-fig" }, v.now || el("p", { class: "muted" }, "The final video is not made yet."),
            el("figcaption", null, "Now")),
          el("figure", { class: "cmp-fig" }, v.before, el("figcaption", null, "Before (version " + h.version + ")"))),
      ];
    }
    var findings = cmp.findings || [];
    return [
      el("p", null, "Version " + h.version + " had no video kept; its findings were:"),
      findings.length ? el("ul", { class: "findings cmp-findings" }, findings.map(function (f) {
        var check = String(f.check || "").replace(/_/g, " ");
        return el("li", { class: "finding " + (f.severity === "error" ? "error" : "warning") },
          el("p", { class: "finding-head" }, el("strong", null, check)),
          el("p", null, String(f.message || "")),
          f.where ? el("p", { class: "where" }, String(f.where)) : null);
      })) : el("p", { class: "muted" }, "None: every check passed."),
    ];
  }

  // Post: the chosen thumbnail then and now (one at a time below 768 px, as on Storyboard), and each field's words.
  function postView(ctx, h, stageId) {
    var el = ctx.el;
    var cmp = h.compare || {};
    var th = cmp.thumbnail || {};
    var pair = el("div", { class: "cmp-thumbs cmp-show-" + boardSide },
      el("div", { class: "cmp-pair" },
        stillFig(ctx, "before", "Before (version " + h.version + ")", th.before,
          "Thumbnail before (version " + h.version + ")", "No thumbnail chosen then"),
        stillFig(ctx, "now", "Now", th.after, "Thumbnail now", "No thumbnail chosen now", ctx.version)));
    var sides = {};
    function pick(side) {
      boardSide = side;
      pair.className = "cmp-thumbs cmp-show-" + side;
      Object.keys(sides).forEach(function (k) { sides[k].setAttribute("aria-pressed", String(k === side)); });
    }
    [["before", "Show before"], ["now", "Show now"]].forEach(function (p) {
      sides[p[0]] = el("button", { type: "button", class: "btn small", "aria-pressed": String(boardSide === p[0]),
        "data-k": "compare:" + stageId + ":side:" + p[0], onclick: function () { pick(p[0]); } }, p[1]);
    });
    var fields = (cmp.fields || []).map(function (f) {
      var ops = f.diff || [];
      var change = lineChange(ops);
      return el("li", { class: "cmp-line" + (change ? " changed" : ""), "data-field": String(f.key) },
        el("p", { class: "cmp-line-head" }, el("strong", null, String(f.key)),
          change ? el("span", { class: "tag" }, change === "Changed" ? change : change.replace("line", "field")) : null),
        el("p", null, ops.length ? diffWords(el, ops) : el("span", { class: "muted" }, "(empty)")));
    });
    return [
      el("h4", null, "Thumbnail"),
      el("div", { class: "cmp-side", role: "group", "aria-label": "Which thumbnail to show" }, sides.before, sides.now),
      pair,
      el("h4", null, "Fields"),
      el("p", { class: "muted" }, "Struck-through words were in version " + h.version + " and underlined words are new."),
      el("ul", { class: "cmp-lines" }, fields),
    ];
  }

  review.compare = function (ctx, stageId, content) {
    var el = ctx.el;
    var hist = (ctx.history || []).filter(function (h) { return h && typeof h.version === "number" && h.compare; });
    if (!hist.length) return null;
    var open = hist.find(function (h) { return h.version === compareOpen[stageId]; }) || null;
    if (!open) delete compareOpen[stageId]; // that version is no longer offered
    var viewId = "bsa-cmp-" + String(stageId).replace(/[^A-Za-z0-9_-]/g, "_");
    var node;
    var buttons = hist.map(function (h) {
      var on = !!open && open.version === h.version;
      var k = "compare:" + stageId + ":" + h.version;
      return el("button", { type: "button", class: "btn small", "aria-expanded": String(on),
        "aria-controls": on ? viewId : null, "data-k": k,
        onclick: function () {
          compareOpen[stageId] = on ? null : h.version;
          var fresh = review.compare(ctx, stageId, content);
          node.replaceWith(fresh);
          var again = fresh.querySelector('[data-k="' + CSS.escape(k) + '"]');
          if (again) again.focus();
        } }, "Compare with version " + h.version);
    });
    var view = null;
    if (open) {
      var kids = stageId === "story" ? storyView(ctx, open)
        : stageId === "storyboard" ? storyboardView(ctx, open, stageId)
          : stageId === "post" ? postView(ctx, open, stageId)
            : finalView(ctx, open, content || {});
      view = el("div", { class: "cmp-view", id: viewId, role: "region",
        "aria-label": "Version " + open.version + " against now" }, kids);
    }
    node = el("section", { class: "st-sec compare" },
      el("h3", null, "Before and after"),
      el("div", { class: "btns cmp-btns" }, buttons),
      view);
    return node;
  };

  review.STALE = STALE;
})();
