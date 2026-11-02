/* Stage renderers: BSA.stages[id] = function (content, ctx) -> HTMLElement. Build markup with ctx.el only. */
(function () {
  "use strict";
  var BSA = (window.BSA = window.BSA || {});
  BSA.stages = BSA.stages || {};

  function seconds(v) {
    return typeof v === "number" ? (Math.round(v * 10) / 10) + " s" : "";
  }

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
})();
