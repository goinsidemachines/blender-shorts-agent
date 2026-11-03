/* The Post stage's parts. BSA.post.thumbnails(ctx, content) draws each candidate in both sizes, again at feed size
   beside a neighbouring thumbnail's grey placeholder, its warnings and findings, "Use this one" and a note on it;
   BSA.post.fields the post text with counts, notes and platform badges; BSA.post.files the files to upload;
   BSA.post.checklist the before-you-post checklist. Markup with ctx.el only (text, never HTML). */
(function () {
  "use strict";
  var BSA = (window.BSA = window.BSA || {});
  var post = (BSA.post = BSA.post || {});

  var NONE = "No thumbnails yet. Claude makes them with BLENDER thumbs.";
  var FEED_W = 180; // a vertical thumbnail's width in a phone's feed, in CSS pixels

  function heroText(c) {
    var h = c.hero || {};
    var bits = [];
    if (h.shot) bits.push("shot " + h.shot);
    if (typeof h.frame === "number") bits.push("frame " + h.frame);
    var from = c.source === "preview" ? "the preview render" : c.source === "final" ? "the final render" : null;
    return bits.length ? "From " + bits.join(", ") + (from ? " of " + from : "") + "." : null;
  }

  function picture(ctx, c, ref, alt, cls) {
    var url = ref ? ctx.fileUrl(ref, c.tag) : "";
    return url ? ctx.el("img", { class: cls, src: url, alt: alt, loading: "lazy" })
      : ctx.el("div", { class: "ph " + cls }, "No file");
  }

  function issues(ctx, c) {
    var warnings = c.warnings || [];
    var findings = c.findings || [];
    if (!warnings.length && !findings.length) return ctx.el("p", { class: "muted" }, "No warnings or findings.");
    return ctx.el("ul", { class: "findings thumb-issues" },
      warnings.map(function (w) {
        return ctx.el("li", { class: "finding warning" },
          ctx.el("p", { class: "finding-head" }, ctx.el("span", { class: "sev" }, "Warning")),
          ctx.el("p", null, String(w)));
      }),
      findings.map(function (f) {
        var err = f.severity === "error";
        var check = String(f.check || "").replace(/_/g, " ");
        return ctx.el("li", { class: "finding " + (err ? "error" : "warning") },
          ctx.el("p", { class: "finding-head" }, ctx.el("span", { class: "sev" }, err ? "Problem" : "Warning"), " ",
            ctx.el("strong", null, check), f.size ? " · " + String(f.size) : null),
          ctx.el("p", null, String(f.message || "")));
      }));
  }

  function candidate(ctx, c) {
    var n = Number(c.n);
    var name = "Candidate " + n;
    var hid = "bsa-thumb-" + n;
    return ctx.el("section", { class: "thumb-cand" + (c.chosen ? " chosen" : ""), "aria-labelledby": hid,
      "data-n": String(n) },
      ctx.el("div", { class: "thumb-head" }, ctx.el("h4", { id: hid }, name),
        c.chosen ? ctx.el("span", { class: "tag" }, "Chosen") : null),
      heroText(c) ? ctx.el("p", { class: "muted" }, heroText(c)) : null,
      ctx.el("div", { class: "thumb-pair" },
        ctx.el("figure", { class: "thumb-fig thumb-v" }, picture(ctx, c, c.vertical, name + ", vertical", "thumb-img"),
          ctx.el("figcaption", null, "Vertical, 1080 × 1920 (Shorts and Reels)")),
        ctx.el("figure", { class: "thumb-fig thumb-l" }, picture(ctx, c, c.landscape, name + ", landscape", "thumb-img"),
          ctx.el("figcaption", null, "Landscape, 1280 × 720 (YouTube)"))),
      ctx.el("h5", null, "In the feed"),
      ctx.el("div", { class: "feed" },
        picture(ctx, c, c.vertical, name + " at feed size", "feed-img"),
        ctx.el("div", { class: "feed-ph", "aria-hidden": "true" })),
      issues(ctx, c),
      ctx.el("div", { class: "btns" },
        ctx.el("button", { type: "button", class: "btn" + (c.chosen ? "" : " primary"), "data-act": "1",
          "data-k": "thumb:use:" + n, "aria-pressed": String(!!c.chosen), "aria-describedby": hid,
          onclick: function () { ctx.chooseThumb(n); } }, "Use this one"),
        ctx.noteButton("thumbnail", String(n), "+ Note on candidate " + n)));
  }

  function capital(text) {
    var t = String(text || "");
    return t ? t.charAt(0).toUpperCase() + t.slice(1) : t;
  }

  // "<count> / <max>" for a field with a limit, coloured by its state; over the limit it also says so in words.
  function counter(ctx, f) {
    if (typeof f.count !== "number" || typeof f.max !== "number") return null;
    var state = f.state === "over" || f.state === "warn" ? f.state : "ok";
    return ctx.el("p", { class: "count count-" + state, "aria-live": "off" },
      ctx.el("span", { class: "count-n" }, f.count + " / " + f.max),
      state === "over" ? ctx.el("span", { class: "count-word" }, "Over the limit") : null);
  }

  function field(ctx, f) {
    var label = String(f.label || f.key);
    var name = label.toLowerCase();
    var text = String(f.text || "");
    var badges = text && Array.isArray(f.badges) ? f.badges : [];
    return ctx.el("div", { class: "field" },
      ctx.el("div", { class: "field-head" }, ctx.el("h4", null, label),
        text ? ctx.el("button", { type: "button", class: "btn small", "data-k": "copy:" + f.key,
          onclick: function () { ctx.copy(text, "the " + name); } }, "Copy the " + name) : null),
      text ? ctx.el("pre", { class: "copytext" }, text) : ctx.el("p", { class: "muted" }, "Not written yet."),
      text ? counter(ctx, f) : null,
      f.note ? ctx.el("p", { class: "field-note" }, capital(f.note)) : null,
      badges.length ? ctx.el("p", { class: "fits" }, badges.map(function (b) {
        return ctx.el("span", { class: "tag fit" }, "Fits " + String(b));
      })) : null,
      ctx.noteButton("field", f.key, "+ Note on the " + name));
  }

  // The post text, one field at a time: "Copy the <label>", its count against the limit, its warning or problem
  // and, for the description, the platforms it fits.
  post.fields = function (ctx, fields) {
    if (!Array.isArray(fields)) return null;
    return ctx.el("section", { class: "st-sec post-text" },
      ctx.el("h3", null, "Post text"),
      ctx.el("div", { class: "fields" }, fields.map(function (f) { return field(ctx, f); })));
  };

  // The files in out/ to upload, each with "Copy path"; a caption file cut for an older timing says so.
  post.files = function (ctx, files) {
    if (!Array.isArray(files)) return null;
    var have = files.filter(function (f) { return f && f.exists; });
    return ctx.el("section", { class: "st-sec post-files" },
      ctx.el("h3", null, "Files"),
      have.length ? ctx.el("ul", { class: "file-list" }, have.map(function (f) {
        var id = "bsa-file-" + String(f.key);
        return ctx.el("li", { class: "file-row" + (f.stale ? " stale" : "") },
          ctx.el("p", { class: "file-head" }, ctx.el("strong", { id: id }, String(f.label || f.key)),
            f.size ? ctx.el("span", { class: "muted" }, " · " + String(f.size)) : null),
          ctx.el("p", { class: "file-rel" }, ctx.el("code", null, String(f.rel || ""))),
          f.stale ? ctx.el("p", { class: "problem" }, "Made for an older timing: run the final edit again.") : null,
          ctx.el("button", { type: "button", class: "btn small", "data-k": "copy-path:" + f.key,
            "aria-describedby": id, onclick: function () { ctx.copy(String(f.path || ""), "the path of " + f.rel); } },
          "Copy path"));
      })) : ctx.el("p", { class: "muted" }, "No files in out/ yet."));
  };

  // "Before you post": a box per item. Live, a tick is saved; offline the boxes work but are not kept.
  post.checklist = function (ctx, items, problem) {
    if (!Array.isArray(items) || !items.length) return null;
    return ctx.el("section", { class: "st-sec checklist", "aria-labelledby": "bsa-checklist-h" },
      ctx.el("h3", { id: "bsa-checklist-h" }, "Before you post"),
      problem ? ctx.el("p", { class: "problem" }, String(problem)) : null,
      ctx.offline ? ctx.el("p", { class: "muted" }, "Ticks aren't saved in the offline view.") : null,
      ctx.el("ul", { class: "check-list" }, items.map(function (it) {
        var id = "bsa-check-" + String(it.id);
        return ctx.el("li", null,
          ctx.el("input", { type: "checkbox", id: id, "data-k": "check:" + it.id, "data-act": "1",
            checked: ctx.ticked(it.id, it.done),
            onchange: function (e) { ctx.tick(it.id, e.target.checked); } }),
          ctx.el("label", { for: id }, String(it.text || "")));
      })));
  };

  post.FEED_W = FEED_W;

  // The thumbnail section of the Post stage; null when the stage has no thumbnail block.
  post.thumbnails = function (ctx, content) {
    if (!content || !Array.isArray(content.candidates)) return null;
    var list = content.candidates;
    var chosen = content.thumbnail && typeof content.thumbnail.chosen === "number" ? content.thumbnail.chosen : null;
    var pressed = list.some(function (c) { return c.chosen; });
    var lead = chosen === null
      ? "Pick the one to post. Each is shown at full size, then at feed size beside a neighbouring video."
      : pressed ? "Candidate " + chosen + " is the thumbnail: its files are in out/. Pick another to change it."
        : "The thumbnail files in out/ are from an earlier make of candidate " + chosen + ", which has been made " +
          "again since. Pick again to post one of these.";
    return ctx.el("section", { class: "st-sec thumbs" },
      ctx.el("h3", null, "Thumbnail"),
      content.thumbs_problem ? ctx.el("p", { class: "problem" }, String(content.thumbs_problem)) : null,
      list.length ? [ctx.el("p", { class: "muted" }, lead), list.map(function (c) { return candidate(ctx, c); })]
        : content.thumbs_problem ? null : ctx.el("p", { class: "muted" }, NONE));
  };
})();
