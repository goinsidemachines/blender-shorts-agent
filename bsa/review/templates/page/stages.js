/* Stage renderers: BSA.stages[id] = function (content, ctx) -> HTMLElement. Build markup with ctx.el only.
   A renderer may carry hooks that the answer card reads:
   - asks(content, stage): false when the stage needs no question from the user (default: it asks);
   - choice(content, stage): {choice: {...}} sent with Approve, or {error: "..."} when nothing is picked yet. */
(function () {
  "use strict";
  var BSA = (window.BSA = window.BSA || {});
  BSA.stages = BSA.stages || {};

  function seconds(v) {
    return typeof v === "number" ? (Math.round(v * 10) / 10) + " s" : "";
  }

  function oneDecimal(v) {
    return (Math.round(Number(v) * 10) / 10).toFixed(1);
  }

  // A render time in words: "45 s", "5 min", "1 h 20 min"; null when there is no estimate.
  function duration(s) {
    if (typeof s !== "number" || !isFinite(s) || s <= 0) return null;
    if (s < 90) return Math.round(s) + " s";
    var m = Math.round(s / 60);
    if (m < 90) return m + " min";
    var h = Math.floor(m / 60);
    var rest = m % 60;
    return rest ? h + " h " + rest + " min" : h + " h";
  }

  function megabytes(n) {
    return (Number(n) / 1000000).toFixed(1) + " MB";
  }

  function pct(v) {
    var n = Number(v);
    return (isFinite(n) ? n : 0) + "%";
  }

  function rect(r) {
    r = r || {};
    return "left:" + pct(r.l) + ";top:" + pct(r.t) + ";width:" + pct(r.w) + ";height:" + pct(r.h);
  }

  function section(ctx, title, kids) {
    return ctx.el("section", { class: "st-sec" }, ctx.el("h3", null, title), kids);
  }

  function tag(ctx, text) {
    return text ? ctx.el("span", { class: "tag" }, String(text)) : null;
  }

  // An http(s) source as a link that opens on its own; anything else stays plain text.
  function source(ctx, fact) {
    if (fact.is_link) {
      return ctx.el("a", { class: "src", href: String(fact.source), target: "_blank", rel: "noopener noreferrer" },
        String(fact.source));
    }
    return ctx.el("span", { class: "src" }, String(fact.source || ""));
  }

  // A scrolling table wrapper is focusable, so keyboard users can scroll it too.
  function table(ctx, label, heads, rows) {
    return ctx.el("div", { class: "tablewrap", role: "region", "aria-label": label, tabindex: "0" },
      ctx.el("table", null,
        ctx.el("thead", null, ctx.el("tr", null, heads.map(function (h) { return ctx.el("th", { scope: "col" }, h); }))),
        ctx.el("tbody", null, rows.map(function (r) {
          return ctx.el("tr", null, r.map(function (c) { return ctx.el("td", null, c === null || c === undefined ? "" : String(c)); }));
        }))));
  }

  // The user's hard rules, each with how this short keeps it.
  function rulesPanel(ctx, rows, lead) {
    if (!rows || !rows.length) return null;
    var byId = {};
    rows.forEach(function (r) { byId[r.id] = r; });
    return section(ctx, "Your hard rules", [
      ctx.el("p", { class: "muted" }, lead),
      ctx.el("ul", { class: "rules" }, rows.map(function (r) {
        var meta = [r.scope === "this short" ? "this short only" : "every short"];
        if (r.part_of && byId[r.part_of]) meta.push("part of: " + byId[r.part_of].text);
        if (r.kind) meta.push("also checked automatically: " + r.kind);
        var cleared = Object.keys(r.cleared || {});
        return ctx.el("li", { class: "rule" },
          ctx.el("p", { class: "rule-text" }, String(r.text || "")),
          ctx.el("p", { class: "rule-meta" }, meta.join(" · ")),
          r.kept ? ctx.el("p", { class: "kept" }, "Kept: " + r.kept)
            : ctx.el("p", { class: "kept missing" }, "Not answered yet: Claude must say how this short keeps it."),
          cleared.length ? ctx.el("p", { class: "rule-meta" }, "Words a check flagged, and why they keep the rule:") : null,
          cleared.length ? ctx.el("ul", { class: "cleared" }, cleared.map(function (w) {
            return ctx.el("li", null, ctx.el("strong", null, w), ": " + r.cleared[w]);
          })) : null);
      })),
    ]);
  }

  // ------------------------------------------------------------------ Interview

  // Interview: the answers collected so far, read-only (an overall note is offered beside every stage).
  BSA.stages.interview = function (content, ctx) {
    var rows = [
      ["Title", content.title],
      ["Topic", content.topic],
      ["Audience", content.audience],
      ["Length", seconds(content.length_s)],
      ["Field", content.field],
    ];
    var dl = ctx.el("dl", { class: "answers" });
    rows.forEach(function (r) {
      if (r[1] === null || r[1] === undefined || r[1] === "") return;
      dl.append(ctx.el("dt", null, r[0]), ctx.el("dd", null, String(r[1])));
    });
    return ctx.el("div", null, ctx.el("p", { class: "muted" }, "What you told Claude at the start."), dl);
  };

  // ------------------------------------------------------------------ Story

  function paceBar(ctx, row) {
    if (!row) return null;
    var total = Math.max(Number(row.seconds) || 0, 1e-6);
    var spoken = Math.min((Number(row.spoken_s) || 0) / total, 1);
    var slow = !!row.too_silent;
    var note = oneDecimal(row.spoken_s) + " s spoken, " + oneDecimal(row.silent_s) + " s silent of " +
      oneDecimal(row.seconds) + " s";
    if (slow) note += row.spoken_s ? ": mostly silent, try about " + oneDecimal(row.suggest_s) + " s" : ": no narration";
    return ctx.el("div", { class: "pace" + (slow ? " slow" : "") },
      ctx.el("div", { class: "pacebar", "aria-hidden": "true" },
        ctx.el("span", { style: "width:" + (100 * spoken).toFixed(1) + "%" })),
      ctx.el("p", { class: "pacenote" }, note));
  }

  function assetRows(list) {
    return list.map(function (a) { return [a.name, a.from, a.license, megabytes(a.bytes)]; });
  }

  function assets(ctx, a) {
    var pending = (a && a.pending) || [];
    var done = (a && a.downloaded) || [];
    var heads = ["Name", "From", "Licence", "Size"];
    var kids = [];
    if (!pending.length && !done.length) {
      kids.push(ctx.el("p", { class: "muted" }, "Nothing to download: this short needs no outside files."));
    }
    if (pending.length) {
      var sum = pending.reduce(function (n, x) { return n + (Number(x.bytes) || 0); }, 0);
      kids.push(ctx.el("p", null, "These will be downloaded after your yes (" + megabytes(sum) + " in total), and not before:"),
        table(ctx, "Downloads waiting for your yes", heads, assetRows(pending)));
    }
    if (done.length) {
      kids.push(ctx.el("p", null, "Already downloaded:"), table(ctx, "Downloaded files", heads, assetRows(done)));
    }
    return section(ctx, "Downloads", kids);
  }

  function look(ctx, lk) {
    lk = lk || {};
    var options = lk.options || [];
    if (!lk.note && !options.length) return null;
    return section(ctx, "Look", [
      lk.note ? ctx.el("p", { class: "muted" }, String(lk.note)) : null,
      options.length ? ctx.el("ul", { class: "looks" }, options.map(function (o) {
        var name = String(o.name || "");
        return ctx.el("li", { class: "look" },
          o.file ? ctx.el("img", { src: ctx.fileUrl(o.file), alt: "Look: " + name, loading: "lazy" })
            : ctx.el("div", { class: "ph" }, "Picture not made yet"),
          ctx.el("p", { class: "look-name" }, name),
          o.description ? ctx.el("p", { class: "muted" }, String(o.description)) : null);
      })) : null,
    ]);
  }

  // The lines that say the hook and the closing line, found by their text: {hook: id|null, closing: id|null}.
  function hookLines(content, beats) {
    var all = [];
    beats.forEach(function (b) { (b.lines || []).forEach(function (ln) { all.push(ln); }); });
    var hook = content.hook ? all.find(function (ln) { return ln.text === content.hook; }) : null;
    var closing = content.closing ? all.slice().reverse().find(function (ln) { return ln.text === content.closing; }) : null;
    return { hook: hook ? hook.id : null, closing: closing ? closing.id : null };
  }

  function lineTags(ctx, roles, id) {
    var tags = [];
    if (roles.hook === id) tags.push(tag(ctx, "Hook"), " ");
    if (roles.closing === id) tags.push(tag(ctx, "Closing line"), " ");
    return tags;
  }

  // The hook or closing line on its own, when no line of the beats says it.
  function quoteBlock(ctx, label, text) {
    return text ? ctx.el("p", { class: "quote" }, tag(ctx, label), " ", String(text)) : null;
  }

  BSA.stages.story = function (content, ctx) {
    var beats = content.beats || [];
    var roles = hookLines(content, beats);
    var facts = content.facts || [];
    return ctx.el("div", { class: "st-story" },
      ctx.el("p", { class: "lead" }, String(content.lead || "")),
      roles.hook ? null : quoteBlock(ctx, "Hook", content.hook),
      section(ctx, "The story", ctx.el("ol", { class: "beats" }, beats.map(function (b) {
        return ctx.el("li", { class: "beat" },
          ctx.el("span", { class: "num", "aria-hidden": "true" }, String(b.n)),
          ctx.el("div", { class: "beat-body" },
            ctx.el("div", { class: "beat-head" }, ctx.el("h4", null, String(b.title || b.shot)), tag(ctx, b.badge)),
            b.picture ? ctx.el("p", { class: "muted picture" }, String(b.picture)) : null,
            ctx.el("ul", { class: "lines" }, (b.lines || []).map(function (ln) {
              return ctx.el("li", { class: "line" },
                ctx.el("p", { class: "line-text" }, lineTags(ctx, roles, ln.id), String(ln.text)),
                ctx.noteButton("line", ln.id, "+ Note on line " + ln.id));
            })),
            paceBar(ctx, b.pace)));
      }))),
      roles.closing ? null : quoteBlock(ctx, "Closing line", content.closing),
      section(ctx, "Facts", facts.length ? ctx.el("ul", { class: "facts" }, facts.map(function (f) {
        return ctx.el("li", null, ctx.el("p", null, String(f.claim || "")), source(ctx, f));
      })) : ctx.el("p", { class: "muted" }, "No facts are quoted in this short.")),
      look(ctx, content.look),
      assets(ctx, content.assets),
      rulesPanel(ctx, content.rules, "Every short follows these. Check that this story keeps each one."));
  };

  // ------------------------------------------------------------------ Storyboard

  // The enlarge dialog: one for the page, kept outside the stage so live updates don't close it.
  var zoom = null;

  function zoomDialog(ctx) {
    if (zoom && document.body.contains(zoom.dialog)) return zoom;
    var z = { cards: [], idx: 0, back: null };
    z.img = ctx.el("img", { alt: "" });
    z.safe = ctx.el("div", { class: "safe" });
    z.cap = ctx.el("div", { class: "cap" });
    z.capText = ctx.el("span");
    z.cap.append(z.capText);
    z.frame = ctx.el("div", { class: "frame" }, z.img, z.safe, z.cap);
    z.title = ctx.el("p", { class: "zt", id: "bsa-zoom-title" });
    z.prev = ctx.el("button", { type: "button", class: "btn small", onclick: function () { step(-1); } }, "Previous caption");
    z.next = ctx.el("button", { type: "button", class: "btn small", onclick: function () { step(1); } }, "Next caption");
    z.safeBtn = ctx.el("button", { type: "button", class: "btn small", "aria-pressed": "false", onclick: function () {
      var on = z.frame.classList.toggle("showsafe");
      z.safeBtn.setAttribute("aria-pressed", String(on));
    } }, "Show safe area");
    z.close = ctx.el("button", { type: "button", class: "btn primary small", onclick: function () { z.dialog.close(); } },
      "Close");
    z.dialog = ctx.el("dialog", { class: "zoom", "aria-labelledby": "bsa-zoom-title" },
      ctx.el("div", { class: "zwrap" }, z.frame, z.title,
        ctx.el("div", { class: "zbar" }, z.prev, z.next, z.safeBtn, z.close)));
    z.dialog.addEventListener("click", function (e) { if (e.target === z.dialog) z.dialog.close(); });
    z.dialog.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight") step(1);
      else if (e.key === "ArrowLeft") step(-1);
    });
    z.dialog.addEventListener("close", function () {
      var back = z.back && document.querySelector('[data-k="' + CSS.escape(z.back) + '"]');
      if (back) back.focus();
    });
    document.body.append(z.dialog);
    zoom = z;

    function step(d) {
      if (!z.cards.length) return;
      z.idx = (z.idx + d + z.cards.length) % z.cards.length;
      showCard();
    }
    return z;
  }

  function showCard() {
    zoom.capText.textContent = zoom.cards.length ? zoom.cards[zoom.idx] : "";
    zoom.prev.disabled = zoom.next.disabled = zoom.cards.length < 2;
  }

  function openZoom(ctx, frame, item) {
    var z = zoomDialog(ctx);
    z.frame.setAttribute("style", "--cap-px:" + (Number(frame.cap_px) || 64));
    z.safe.setAttribute("style", rect(frame.safe));
    z.cap.setAttribute("style", rect(frame.caption));
    z.img.src = ctx.fileUrl(item.file);
    z.img.alt = item.alt;
    z.title.textContent = item.title;
    z.cards = item.cards;
    z.idx = 0;
    z.back = item.key;
    showCard();
    if (!z.dialog.open) z.dialog.showModal();
    z.close.focus();
  }

  function placeholder(ctx) {
    return ctx.el("div", { class: "ph" }, "Picture not made yet");
  }

  // A still that opens the enlarge dialog; a placeholder when there is no picture or it fails to load.
  function thumb(ctx, frame, item, label) {
    if (!item.file || !ctx.fileUrl(item.file)) return placeholder(ctx);
    var btn = ctx.el("button", { type: "button", class: "thumb", "data-k": item.key, "aria-label": label,
      onclick: function () { openZoom(ctx, frame || {}, item); } },
    ctx.el("img", { class: "still", src: ctx.fileUrl(item.file), alt: item.alt, loading: "lazy",
      onerror: function () { if (btn.isConnected) btn.replaceWith(placeholder(ctx)); } }));
    return btn;
  }

  function timing(ctx, content) {
    var shots = content.shots || [];
    return ctx.el("div", { class: "timing" },
      ctx.el("div", { class: "tl" }, shots.map(function (s) {
        return ctx.el("button", { type: "button", class: "seg", style: "flex:" + (Number(s.flex) || 1) + " 1 0",
          "aria-label": "Shot " + s.n + ": " + s.title + ", " + s.time,
          title: s.title + ", " + s.time,
          onclick: function () {
            var t = document.querySelector('[data-k="' + CSS.escape("enlarge:" + s.id) + '"]') ||
              document.getElementById("shot-" + s.id);
            if (t) t.focus();
          } }, String(s.n));
      })),
      ctx.el("div", { class: "tlscale", "aria-hidden": "true" },
        ctx.el("span", null, "0.0 s"), ctx.el("span", null, oneDecimal(content.total_s) + " s")));
  }

  function shotCard(ctx, content, s) {
    var cards = [];
    (s.lines || []).forEach(function (ln) { cards = cards.concat(ln.cards || []); });
    var title = s.n + ". " + (s.title || s.id);
    var item = { key: "enlarge:" + s.id, file: s.still, alt: "Shot " + s.n + ": " + (s.title || s.id),
      title: title + " (" + s.time + ")", cards: cards };
    return ctx.el("li", { class: "shot", id: "shot-" + s.id, tabindex: "-1" },
      thumb(ctx, content.frame, item, "Enlarge shot " + s.n),
      ctx.el("div", { class: "meta" },
        ctx.el("span", { class: "tag" }, s.id + (s.badge ? " · " + s.badge : "")),
        ctx.el("h4", null, title),
        ctx.el("span", { class: "time" }, String(s.time)),
        (s.lines || []).map(function (ln) {
          return ctx.el("div", { class: "sline" },
            ctx.el("p", null, "“" + ln.text + "”",
              typeof ln.at_s === "number" ? ctx.el("span", { class: "time" }, " at " + oneDecimal(ln.at_s) + " s") : null),
            (ln.cards || []).length ? ctx.el("p", { class: "cards" }, ln.cards.map(function (c) {
              return ctx.el("span", { class: "capcard" }, String(c));
            })) : null);
        }),
        (s.labels || []).length ? ctx.el("p", { class: "cards" }, ctx.el("span", { class: "muted" }, "On screen: "),
          s.labels.map(function (l) { return ctx.el("span", { class: "capcard" }, String(l)); })) : null,
        ctx.el("div", { class: "shot-notes" },
          ctx.noteButton("shot", s.id, "+ Note on shot " + s.id),
          s.still ? ctx.noteButton("still", s.id, "+ Note on still " + s.id) : null)));
  }

  function closingCard(ctx, content) {
    var c = content.closing;
    if (!c) return null;
    var item = { key: "enlarge:closing", file: c.file ? { file: c.file } : null, alt: "Closing card", title: "Closing card (the end)",
      cards: [] };
    return ctx.el("li", { class: "shot closing", id: "shot-closing", tabindex: "-1" },
      thumb(ctx, content.frame, item, "Enlarge the closing card"),
      ctx.el("div", { class: "meta" },
        ctx.el("span", { class: "tag" }, "End"),
        ctx.el("h4", null, "Closing card"),
        ctx.el("span", { class: "time" }, "held " + oneDecimal(c.held) + " s"),
        ctx.el("p", null, "The video ends on this after the story's last line. It adds no music or sound."),
        ctx.el("p", null, c.said ? "Said over it: “" + c.said + "”" : "Nothing is said over it."),
        (c.warnings || []).length ? ctx.el("ul", { class: "warns" }, c.warnings.map(function (w) {
          return ctx.el("li", null, ctx.el("strong", null, "Warning: "), String(w));
        })) : null));
  }

  BSA.stages.storyboard = function (content, ctx) {
    return ctx.el("div", { class: "st-board" },
      ctx.el("p", { class: "lead" }, String(content.lead || "")),
      section(ctx, "Timing", timing(ctx, content)),
      section(ctx, "Shots", [
        ctx.el("p", { class: "muted" }, "Open a picture to enlarge it and see its captions where they will appear."),
        ctx.el("ol", { class: "shots" }, (content.shots || []).map(function (s) { return shotCard(ctx, content, s); }),
          closingCard(ctx, content)),
      ]),
      rulesPanel(ctx, content.rules, "Check every picture against these too, the closing card included."));
  };

  // ------------------------------------------------------------------ Engine

  var picked = {}; // "stage@version" -> the engine picked on this page, kept across live updates

  function pickKey(stage) {
    return stage.id + "@" + stage.version;
  }

  function currentPick(content, stage) {
    var key = pickKey(stage);
    if (picked[key]) return picked[key];
    var d = stage.decision;
    if (d && d.version === stage.version && d.choice && d.choice.engine) return d.choice.engine;
    return null;
  }

  BSA.stages.engine = function (content, ctx) {
    var name = function (e) { return String(e || "").toUpperCase(); };
    if (!asksEngine(content)) {
      var short = content.from === "short.json";
      return ctx.el("div", { class: "st-engine" },
        ctx.el("p", { class: "big" }, name(content.engine) + (short ? ", from this short" : ", from your settings")),
        ctx.el("p", { class: "muted" }, short
          ? "Nothing to choose here. The short's short.json picks it (render.engine)."
          : "Nothing to choose here. The render.engine setting picks it for every short."));
    }
    var s = ctx.stage;
    var now = currentPick(content, s);
    var open = ctx.answerable(); // locked while an answer is on its way or decisions can't be read
    return ctx.el("div", { class: "st-engine" },
      ctx.el("p", { class: "lead" }, "Pick the engine that renders this short, then approve."),
      ctx.el("fieldset", { class: "choices" },
        ctx.el("legend", null, "Render engine"),
        (content.choices || []).map(function (ch) {
          var id = "bsa-engine-" + ch.engine;
          var est = duration(ch.estimate_s);
          return ctx.el("label", { class: "choice", for: id },
            ctx.el("input", { type: "radio", name: "bsa-engine", id: id, value: ch.engine, "data-k": "engine:" + ch.engine,
              checked: now === ch.engine, disabled: !open,
              onchange: function () { picked[pickKey(s)] = ch.engine; } }),
            ctx.el("span", { class: "choice-name" }, name(ch.engine)),
            ctx.el("span", { class: "muted" }, est ? "about " + est : "no estimate yet"));
        })));
  };

  // An engine that comes from the user's settings or from short.json is already chosen: nothing to ask.
  function asksEngine(content) {
    return content.from !== "settings" && content.from !== "short.json";
  }

  BSA.stages.engine.asks = asksEngine;

  BSA.stages.engine.choice = function (content, stage) {
    var e = currentPick(content, stage);
    return e ? { choice: { engine: e } } : { error: "Pick an engine first, then approve." };
  };

  // ------------------------------------------------------------------ Narration

  BSA.stages.narration = function (content, ctx) {
    var rows = [
      ["Status", content.status || "Not started yet"],
      ["Voice", content.provider],
      ["Message", content.message],
      ["Takes", typeof content.takes === "number" ? String(content.takes) : null],
    ];
    var dl = ctx.el("dl", { class: "answers" });
    rows.forEach(function (r) {
      if (r[1] === null || r[1] === undefined || r[1] === "") return;
      dl.append(ctx.el("dt", null, r[0]), ctx.el("dd", null, String(r[1])));
    });
    return ctx.el("div", null,
      ctx.el("p", { class: "muted" }, "Claude records the voice for every line. Add a note beside this stage if it needs a change."),
      dl);
  };
})();
