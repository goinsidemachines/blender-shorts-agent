/* The Post stage's thumbnails: BSA.post.thumbnails(ctx, content) draws each candidate in both sizes, again at feed
   size beside a neighbouring thumbnail's grey placeholder, its warnings and findings, "Use this one" and a note on it.
   Markup with ctx.el only (text, never HTML). */
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
          ctx.el("p", { class: "finding-head" }, ctx.el("span", { class: "sev" }, "Warning")), ctx.el("p", null, String(w)));
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
    return ctx.el("section", { class: "thumb-cand" + (c.chosen ? " chosen" : ""), "aria-labelledby": hid, "data-n": String(n) },
      ctx.el("div", { class: "thumb-head" }, ctx.el("h4", { id: hid }, name), c.chosen ? ctx.el("span", { class: "tag" }, "Chosen") : null),
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
          "data-k": "thumb:use:" + n, "aria-pressed": String(!!c.chosen),
          onclick: function () { ctx.chooseThumb(n); } }, "Use this one"),
        ctx.noteButton("thumbnail", String(n), "+ Note on candidate " + n)));
  }

  post.FEED_W = FEED_W;

  // The thumbnail section of the Post stage; null when the stage has no thumbnail block.
  post.thumbnails = function (ctx, content) {
    if (!content || !Array.isArray(content.candidates)) return null;
    var list = content.candidates;
    var chosen = content.thumbnail && typeof content.thumbnail.chosen === "number" ? content.thumbnail.chosen : null;
    var lead = chosen !== null ? "Candidate " + chosen + " is the thumbnail: its files are in out/. Pick another to change it."
      : "Pick the one to post. Each is shown at full size, then at feed size beside a neighbouring video.";
    return ctx.el("section", { class: "st-sec thumbs" },
      ctx.el("h3", null, "Thumbnail"),
      content.thumbs_problem ? ctx.el("p", { class: "problem" }, String(content.thumbs_problem)) : null,
      list.length ? [ctx.el("p", { class: "muted" }, lead), list.map(function (c) { return candidate(ctx, c); })]
        : content.thumbs_problem ? null : ctx.el("p", { class: "muted" }, NONE));
  };
})();
