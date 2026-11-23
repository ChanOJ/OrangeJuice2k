/* Orange Juice 2000 archive: skin chooser (default skin tonic), client-side search.
   Source: scripts/oj.js, copied to site/js/oj.js by scripts/statify.py (the skin list is filled in).
   No cookie: the chosen skin is kept in localStorage. No inline script needed (CSP friendly). */
(function () {
  "use strict";
  var SKINS = /*SKINS*/[];
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var KEY = "oj_skin";

  function load(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  function store(k, v) {
    try {
      if (v === null) { window.localStorage.removeItem(k); } else { window.localStorage.setItem(k, v); }
    } catch (e) { /* private mode, storage disabled: the choice is simply not remembered */ }
  }

  var m = window.location.pathname.match(/^\/skin\/([^\/]+)(\/.*)$/);
  var current = m ? m[1] : "tonic";
  var prefix = m ? "/skin/" + m[1] : "";
  var path = m ? m[2] : window.location.pathname;

  function skinPath(name, p) { return (name === "tonic" ? "" : "/skin/" + name) + p; }
  function known(name) { return SKINS.indexOf(name) >= 0; }

  // Home page at the root only: the skin the visitor chose, if any (no redirect anywhere else).
  // New visitors always get tonic: the 2000 skin of the day (Day MOD 4) is not reproduced, an
  // archive should look the same on every visit.
  if (!m && (path === "/" || path === "/index.html")) {
    var want = load(KEY);
    if (want && known(want) && want !== "tonic") { window.location.replace(skinPath(want, "/index.html")); return; }
  }

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fold(s) {
    s = String(s || "").toLowerCase();
    return s.normalize ? s.normalize("NFD").replace(/[\u0300-\u036f]/g, "") : s;
  }
  function param(name) {
    var r = new RegExp("[?&]" + name + "=([^&]*)").exec(window.location.search);
    return r ? decodeURIComponent(r[1].replace(/\+/g, " ")) : "";
  }

  function ready(fn) {
    if (document.readyState !== "loading") { fn(); } else { document.addEventListener("DOMContentLoaded", fn); }
  }

  ready(function () {
    // Skin chooser (skins.html)
    var go = document.getElementById("oj-skin-go");
    if (go) {
      var chosen = load(KEY) || "";
      var radios = document.querySelectorAll('#oj-skins input[name="skin"]');
      for (var i = 0; i < radios.length; i++) { if (radios[i].value === chosen) { radios[i].checked = true; } }
      go.addEventListener("click", function () {
        var v = null;
        for (var j = 0; j < radios.length; j++) { if (radios[j].checked) { v = radios[j].value; } }
        if (v === null) { return; }
        store(KEY, v === "" ? null : v);
        var target = v === "" ? "tonic" : v;
        var back = "/index.html";
        try {
          var ref = document.referrer ? new URL(document.referrer) : null;
          if (ref && ref.origin === window.location.origin) {
            var rp = ref.pathname.replace(/^\/skin\/[^\/]+/, "");
            if (rp !== "/skins.html" && rp !== "/search.html") { back = rp === "/" ? "/index.html" : rp; }
          }
        } catch (e) { /* old browser: back to the home page */ }
        window.location.href = skinPath(target, back);
      });
    }

    // Search fields (home box, people / groups / picture library pages, results page)
    var boxes = document.querySelectorAll(".oj-search");
    for (var b = 0; b < boxes.length; b++) {
      (function (box) {
        var input = box.querySelector('input[type="TEXT"], input[type="text"]');
        var button = box.querySelector('input[type="BUTTON"], input[type="button"]');
        var select = box.querySelector("select.oj-type");
        function run() {
          var q = input.value.replace(/^\s+|\s+$/g, "");
          if (!q) { return; }
          var t = select ? select.value : box.getAttribute("data-t");
          window.location.href = prefix + "/search.html?t=" + encodeURIComponent(t) + "&q=" + encodeURIComponent(q);
        }
        input.addEventListener("keydown", function (e) { if (e.keyCode === 13) { e.preventDefault(); run(); } });
        if (button) { button.addEventListener("click", run); }
      })(boxes[b]);
    }

    // Results page
    var out = document.getElementById("oj-results");
    if (!out) { return; }
    var t = param("t") || "news";
    var q = param("q");
    var box = document.querySelector("#oj-results").parentNode.querySelector(".oj-search");
    if (box) {
      box.querySelector('input[type="TEXT"], input[type="text"]').value = q;
      var sel = box.querySelector("select.oj-type");
      if (sel) { sel.value = t; }
    }
    if (!q) { out.innerHTML = ""; return; }
    var file = { news: "news", pics: "pictures", photos: "pictures", people: "people", groups: "groups" }[t] || "news";
    var words = fold(q).split(/\s+/).filter(function (w) { return w; });
    var icons = out.getAttribute("data-icons");
    out.innerHTML = "searching...";
    var xhr = new XMLHttpRequest();
    xhr.open("GET", "/idx/" + file + ".json");
    xhr.onload = function () {
      var rows = JSON.parse(xhr.responseText);
      var hits = rows.filter(function (r) {
        if (t === "pics" && r[1] !== 0) { return false; }
        if (t === "photos" && r[1] !== 1) { return false; }
        var hay = fold(r.join(" "));
        return words.every(function (w) { return hay.indexOf(w) >= 0; });
      });
      var max = 300, html = [];
      html.push("<P><FONT class=smtxt>" + hits.length + " result" + (hits.length === 1 ? "" : "s") +
                (hits.length > max ? " (first " + max + " shown)" : "") + "</FONT></P>");
      hits.slice(0, max).forEach(function (r) {
        if (file === "news") {
          var d = r[1].split("-");
          var title = esc(r[2]);
          if (r[7]) {
            var internal = r[7].charAt(0) === "/";
            title = '<A href="' + esc(internal ? prefix + r[7] : r[7]) + '"' + (internal ? "" : ' target="_blank"') + ">" +
                    title + "</A>";
          }
          var page = r[6] === 1 ? "/index.html" : "/news/page-" + r[6] + ".html";
          html.push('<P><IMG src="' + esc(icons) + (r[5] < 10 ? "0" : "") + r[5] + '.gif" align=bottom border=0> ' +
                    "<FONT class=title2>" + title + "</FONT> <FONT class=date>[" + d[2] + " " + MONTHS[+d[1] - 1] +
                    " " + d[0] + "]</FONT><BR>" + esc(r[3]) + '<BR><FONT class=smtxt>(<I>' + esc(r[4]) + "</I> - " +
                    '<A href="' + prefix + page + '" class=txt>news page ' + r[6] + "</A>)</FONT></P>");
        } else if (file === "pictures") {
          var id5 = ("00000" + r[0]).slice(-5);
          var info = [["Author", r[4]], ["Event", r[5]], ["Who", r[6]], ["Place", r[7]]]
            .filter(function (x) { return x[1]; })
            .map(function (x) { return "<BR><I>" + x[0] + ":</I> " + esc(x[1]); }).join("");
          html.push('<TABLE width="100%"><TR><TD><FONT class=smtxt><B>' + esc(r[2] || "[no legend]") + "</B> " +
                    '<A href="' + prefix + "/piclib/" + r[0] + '.html"><IMG src="/pictures/piclib/tn/' + id5 +
                    '.jpg" alt="' + esc(r[3]) + '" border=0 align=left></A>' + info +
                    '</FONT></TD></TR><TR><TD>&nbsp;</TD></TR></TABLE>');
        } else if (file === "people") {
          html.push('<A href="' + prefix + "/people/" + r[0] + '.html">' + esc(r[1]) + "</A><BR>");
        } else {
          html.push('<A href="' + prefix + "/groups/" + r[1] + '.html">' + esc(r[0]) + "</A>" +
                    (r[3] ? " <FONT class=smtxt>(" + esc(r[3]) + ")</FONT>" : "") +
                    (r[2] ? ' <A href="' + esc(r[2]) + '" target="_blank" class=txt>&gt;www</A>' : "") + "<BR>");
        }
      });
      out.innerHTML = html.join("\n");
    };
    xhr.onerror = function () { out.innerHTML = "The search index could not be loaded."; };
    xhr.send();
  });
})();
