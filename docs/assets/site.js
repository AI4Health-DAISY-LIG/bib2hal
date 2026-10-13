// bib2hal documentation: shared header/footer, tabs, and the live list of releases.
(function () {
  "use strict";
  var REPO = "AI4Health-DAISY-LIG/bib2hal";
  var page = document.body.getAttribute("data-page") || "";
  var links = [["index.html", "Why bib2hal", "home"], ["how-to.html", "How to use it", "howto"],
               ["bibtex.html", "Get your .bib file", "bibtex"], ["privacy.html", "Your data", "privacy"]];

  var header = document.getElementById("site-header");
  if (header) {
    header.className = "top";
    header.innerHTML =
      '<div class="wrap"><a class="brand" href="index.html"><img src="assets/logo.png" alt="AI4Health @ DAISY logo">' +
      '<span><span class="name">bib2hal</span><br><span class="madeby">made by <b>AI4Health @ DAISY</b> · LIG</span></span></a>' +
      '<nav class="nav">' + links.map(function (l) {
        return '<a href="' + l[0] + '"' + (l[2] === page ? ' class="on"' : "") + ">" + l[1] + "</a>";
      }).join("") + '<a class="cta" href="download.html">Download</a></nav></div>';
  }
  var footer = document.getElementById("site-footer");
  if (footer) {
    footer.innerHTML =
      '<div class="wrap"><a class="brand" href="https://github.com/' + REPO + '"><img src="assets/logo.png" alt="">' +
      '<span>bib2hal <span class="v">__BIB2HAL_VERSION__</span> · made by <b>AI4Health @ DAISY</b> — LIG, Université Grenoble Alpes</span></a>' +
      '<span><a href="https://github.com/' + REPO + '">Source code</a> · MIT licence · ' +
      '<a href="https://github.com/' + REPO + '/issues">Report a problem</a></span></div>';
  }

  // tabs
  document.querySelectorAll("[data-tabs]").forEach(function (box) {
    var buttons = box.querySelectorAll(".tabs button");
    function select(id) {
      buttons.forEach(function (b) { b.classList.toggle("on", b.dataset.tab === id); });
      box.querySelectorAll(".panel").forEach(function (p) { p.classList.toggle("on", p.id === id); });
    }
    buttons.forEach(function (b) { b.onclick = function () { select(b.dataset.tab); history.replaceState(null, "", "#" + b.dataset.tab); }; });
    var want = location.hash.slice(1);
    select(Array.prototype.some.call(buttons, function (b) { return b.dataset.tab === want; }) ? want : buttons[0].dataset.tab);
  });

  // downloads
  var dl = document.getElementById("downloads");
  if (!dl) return;
  var ua = (navigator.userAgent || "").toLowerCase();
  var mine = ua.indexOf("windows") >= 0 ? "windows" : (ua.indexOf("mac") >= 0 ? "macos" : (ua.indexOf("linux") >= 0 ? "linux" : ""));
  var OS = [["windows", "Windows", "Windows 10 / 11 · 64-bit"], ["macos", "macOS", "Apple Silicon (M1 and later)"], ["linux", "Linux", "64-bit"]];
  function size(n) { return n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.round(n / 1e3) + " kB"; }
  function esc(s) { return String(s || "").replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
  fetch("https://api.github.com/repos/" + REPO + "/releases?per_page=10", {headers: {Accept: "application/vnd.github+json"}})
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (rels) {
      rels = rels.filter(function (r) { return !r.draft; });
      if (!rels.length) throw new Error("no release yet");
      var latest = rels[0];
      document.getElementById("latest-meta").innerHTML = "Latest version <b>" + esc(latest.tag_name) + "</b> · released " +
        new Date(latest.published_at).toLocaleDateString(undefined, {year: "numeric", month: "long", day: "numeric"}) +
        ' · <a href="' + esc(latest.html_url) + '">release notes</a>';
      dl.innerHTML = OS.map(function (o) {
        var a = latest.assets.filter(function (x) { return x.name.indexOf("bib2hal-" + o[0]) === 0; })[0];
        if (!a) return "";
        return '<a class="os' + (o[0] === mine ? " rec" : "") + '" href="' + esc(a.browser_download_url) + '"><b>' + o[1] + "</b><span>" + o[2] +
          " · " + size(a.size) + "</span><br><span class=\"mono\">" + esc(a.name) + "</span>" + (o[0] === mine ? '<br><span class="tag">Recommended for this computer</span>' : "") + "</a>";
      }).join("");
      var sums = latest.assets.filter(function (x) { return x.name === "SHA256SUMS.txt"; })[0];
      if (sums) document.getElementById("checksums").innerHTML = 'Checksums: <a href="' + esc(sums.browser_download_url) + '">SHA256SUMS.txt</a>';
      var older = document.getElementById("older");
      if (older && rels.length > 1) {
        older.innerHTML = "<h3>Previous versions</h3>" + rels.slice(1).map(function (r) {
          return '<details><summary>' + esc(r.tag_name) + ' <span class="release-meta">— ' + new Date(r.published_at).toLocaleDateString() + "</span></summary><ul>" +
            r.assets.map(function (a) { return '<li><a href="' + esc(a.browser_download_url) + '">' + esc(a.name) + "</a> (" + size(a.size) + ")</li>"; }).join("") + "</ul></details>";
        }).join("");
      }
    })
    .catch(function () {
      document.getElementById("latest-meta").innerHTML = "";
      dl.innerHTML = '<a class="os rec" href="https://github.com/' + REPO + '/releases"><b>No version published yet</b><span>The first release is being prepared. Check the releases page on GitHub, or come back soon.</span></a>';
    });
})();
