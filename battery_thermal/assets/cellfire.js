/* cellfire.js -- browser-side rendering for the CellFire dashboard.

   Why this exists: the solver output (every timestep of the 3D field) lives in
   a dcc.Store. A server-side callback that takes that store as an input has to
   upload all of it (~2.4 MB) on every slider tick, which made scrubbing take
   over a second and playback impossible. These callbacks run in the browser,
   so scrubbing, playback and click-to-seek never touch the network. */

(function () {
  "use strict";

  var FONT = '"Barlow Semi Condensed", "Segoe UI", system-ui, sans-serif';
  var INK = "#dfe5ea";
  var MUTED = "#8b98a5";
  var GRID = "rgba(223, 229, 234, 0.08)";
  var LIMIT = "#ff5d73";

  // Thermal ramp for the 3D field -- the one place the interface uses
  // saturated colour, so heat is the first thing the eye finds.
  var THERMAL = [
    [0.0, "#24307a"], [0.22, "#5b2a9a"], [0.45, "#c2336f"],
    [0.7, "#ff7a1a"], [1.0, "#ffe27a"],
  ];

  var coordCache = { key: null, x: null, y: null, z: null };

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function activeModes(store, modes) {
    var keys = Object.keys(store).filter(function (k) { return k !== "meta"; });
    var picked = (modes || []).filter(function (m) { return store[m]; });
    return picked.length ? picked : keys;
  }

  function fmtElapsed(sec) {
    var s = Math.round(sec), m = Math.floor(s / 60), h = Math.floor(m / 60);
    m = m % 60; s = s % 60;
    if (h) return h + " h " + m + " min";
    return m + " min " + (s < 10 ? "0" : "") + s + " s";
  }

  function emptyFigure(message) {
    return {
      data: [],
      layout: {
        paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
        xaxis: { visible: false }, yaxis: { visible: false },
        margin: { l: 0, r: 0, t: 0, b: 0 },
        annotations: [{
          text: message, xref: "paper", yref: "paper", x: 0.5, y: 0.5,
          showarrow: false, font: { size: 15, color: MUTED, family: FONT },
        }],
      },
    };
  }

  function coords(meta) {
    var key = [meta.nx, meta.ny, meta.nz, meta.Lx, meta.Ly, meta.Lz].join("|");
    if (coordCache.key === key) return coordCache;
    var x = [], y = [], z = [], i, j, k;
    var xs = [], ys = [], zs = [];
    for (i = 0; i < meta.nx; i++) xs.push(meta.Lx * 100 * i / (meta.nx - 1));
    for (j = 0; j < meta.ny; j++) ys.push(meta.Ly * 100 * j / (meta.ny - 1));
    for (k = 0; k < meta.nz; k++) zs.push(meta.Lz * 1000 * k / (meta.nz - 1));
    // order matches T[z][y][x] flattened (x fastest)
    for (k = 0; k < meta.nz; k++)
      for (j = 0; j < meta.ny; j++)
        for (i = 0; i < meta.nx; i++) { x.push(xs[i]); y.push(ys[j]); z.push(zs[k]); }
    coordCache = { key: key, x: x, y: y, z: z };
    return coordCache;
  }

  function outline(meta) {
    var X = meta.Lx * 100, Y = meta.Ly * 100, Z = meta.Lz * 1000;
    var c = [[0, 0, 0], [X, 0, 0], [X, Y, 0], [0, Y, 0], [0, 0, Z], [X, 0, Z], [X, Y, Z], [0, Y, Z]];
    var edges = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]];
    var x = [], y = [], z = [];
    edges.forEach(function (e) {
      x.push(c[e[0]][0], c[e[1]][0], null);
      y.push(c[e[0]][1], c[e[1]][1], null);
      z.push(c[e[0]][2], c[e[1]][2], null);
    });
    return {
      type: "scatter3d", mode: "lines", x: x, y: y, z: z, hoverinfo: "skip",
      line: { color: "rgba(223,229,234,0.35)", width: 2 }, showlegend: false,
    };
  }

  function volumeFigure(store, modes, mode, frame, meta) {
    // One colour range for the whole run and for every cooling mode shown, so
    // the field visibly heats up as you scrub and modes are comparable.
    var lo = Infinity, hi = -Infinity;
    modes.forEach(function (m) {
      lo = Math.min(lo, Math.min.apply(null, store[m].tmin));
      hi = Math.max(hi, Math.max.apply(null, store[m].tmax));
    });
    var span = Math.max(hi - lo, 2.0);           // never stretch a 0.1 degC wobble over the whole ramp
    var isomin = lo - 0.15 * span, isomax = lo + span;

    var c = coords(meta), v = [];
    store[mode].T[frame].forEach(function (plane) {
      plane.forEach(function (row) { row.forEach(function (t) { v.push(t); }); });
    });

    var ratioY = meta.Ly / meta.Lx;
    return {
      data: [
        {
          type: "volume", x: c.x, y: c.y, z: c.z, value: v,
          isomin: isomin, isomax: isomax, cmin: isomin, cmax: isomax,
          opacity: 0.35, surface_count: 18, colorscale: THERMAL,
          hovertemplate: "%{value:.1f} °C<extra></extra>",
          colorbar: {
            title: { text: "°C", font: { color: MUTED, family: FONT } },
            tickfont: { color: MUTED, family: FONT, size: 12 },
            thickness: 10, len: 0.72, outlinewidth: 0, x: 0.99,
          },
        },
        outline(meta),
      ],
      layout: {
        paper_bgcolor: "rgba(0,0,0,0)", font: { color: INK, family: FONT },
        margin: { l: 0, r: 0, t: 0, b: 0 },
        uirevision: "camera",                       // keep the user's rotation while scrubbing
        scene: {
          uirevision: "camera",
          aspectmode: "manual", aspectratio: { x: 1, y: ratioY, z: 0.28 },
          camera: { eye: { x: 0.8, y: -0.95, z: 0.6 }, center: { x: 0, y: 0, z: -0.15 } },
          xaxis: { title: { text: "length (cm)" }, gridcolor: GRID, color: MUTED, showbackground: false },
          yaxis: { title: { text: "width (cm)" }, gridcolor: GRID, color: MUTED, showbackground: false },
          zaxis: { title: { text: "thickness (mm)" }, gridcolor: GRID, color: MUTED, showbackground: false },
        },
        hoverlabel: { bgcolor: "#1e252c", font: { color: INK, family: FONT }, bordercolor: "#2e3842" },
      },
    };
  }

  function lineFigure(store, modes, frame, key, opts) {
    var meta = store.meta, traces = [], shapes = [], annotations = [];
    var scale = opts.scale || 1, ymin = Infinity, ymax = -Infinity, cursorT = null;

    // SoC does not depend on cooling, so it is drawn once in a neutral colour
    (opts.single ? modes.slice(0, 1) : modes).forEach(function (m) {
      var d = store[m];
      var xs = d.times.map(function (t) { return t / 60; });
      var ys = d[key].map(function (v) { return v * scale; });
      ys.forEach(function (v) { ymin = Math.min(ymin, v); ymax = Math.max(ymax, v); });
      if (cursorT === null) cursorT = xs[frame];
      var color = opts.single ? INK : meta.colors[m], label = opts.single ? "State of charge" : meta.labels[m];
      traces.push({
        type: "scatter", mode: "lines", x: xs, y: ys, name: label, legendgroup: m,
        showlegend: !!opts.legend, line: { color: color, width: 2.2 },
        hovertemplate: "%{y:.1f}" + opts.unit,
      });
      traces.push({
        type: "scatter", mode: "markers", x: [xs[frame]], y: [ys[frame]], legendgroup: m,
        showlegend: false, hoverinfo: "skip",
        marker: { color: color, size: 8, line: { color: "#14181d", width: 2 } },
      });
    });

    var pad = Math.max((ymax - ymin) * 0.12, opts.minPad || 0.5);
    var top = ymax + pad, bottom = ymin - pad;
    if (opts.floor !== undefined) bottom = Math.max(bottom, opts.floor);
    if (opts.limit !== undefined && opts.limit !== null) {
      if (opts.limit <= ymax + Math.max(12, (ymax - ymin) * 0.9)) {
        top = Math.max(top, opts.limit + pad);
        shapes.push({
          type: "line", xref: "paper", x0: 0, x1: 1, y0: opts.limit, y1: opts.limit,
          line: { color: LIMIT, width: 1.4, dash: "dot" },
        });
        annotations.push({
          xref: "paper", x: 0, y: opts.limit, yanchor: "bottom", xanchor: "left", showarrow: false,
          text: "limit " + opts.limit + " °C", font: { color: LIMIT, size: 12, family: FONT },
        });
      } else {
        annotations.push({
          xref: "paper", yref: "paper", x: 0, y: 1, yanchor: "top", xanchor: "left", showarrow: false,
          text: "limit " + opts.limit + " °C is above this range", font: { color: MUTED, size: 12, family: FONT },
        });
      }
    }
    if (opts.ceiling !== undefined) top = Math.min(top, opts.ceiling);
    if (cursorT !== null) {
      shapes.push({
        type: "line", xref: "x", yref: "paper", x0: cursorT, x1: cursorT, y0: 0, y1: 1,
        line: { color: "rgba(223,229,234,0.5)", width: 1.2, dash: "dash" },
      });
    }

    return {
      data: traces,
      layout: {
        paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
        font: { color: INK, family: FONT, size: 13 },
        margin: { l: 52, r: 12, t: 8, b: 40 },
        xaxis: { title: { text: "Time (min)", standoff: 6 }, gridcolor: GRID, zeroline: false, color: MUTED },
        yaxis: { title: { text: opts.title, standoff: 6 }, gridcolor: GRID, zeroline: false, color: MUTED, range: [bottom, top] },
        shapes: shapes, annotations: annotations, hovermode: "x unified",
        legend: { orientation: "h", x: 1, xanchor: "right", y: 1.14, bgcolor: "rgba(0,0,0,0)", font: { color: INK } },
        hoverlabel: { bgcolor: "#1e252c", font: { color: INK, family: FONT }, bordercolor: "#2e3842" },
        uirevision: "fixed",
      },
    };
  }

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    cellfire: {
      /* frame, store, cooling checklist, 3D mode, temp limit -> 4 figures + readout */
      render: function (frame, store, modes, volMode, limit) {
        if (!store || !store.meta) {
          var e = emptyFigure("Run a simulation to see the temperature field");
          return [e, emptyFigure("Waiting for a run"), emptyFigure("Waiting for a run"),
                  emptyFigure("Waiting for a run"), "No simulation yet"];
        }
        var active = activeModes(store, modes);
        var first = active[0];
        var n = store[first].times.length;
        var f = clamp(Math.round(frame || 0), 0, n - 1);
        var vm = (volMode && store[volMode]) ? volMode : first;
        if (active.indexOf(vm) < 0) active = [vm].concat(active);

        var lim = (limit === null || limit === undefined || limit === "") ? undefined : Number(limit);
        var vol = volumeFigure(store, active, vm, f, store.meta);
        var tmax = lineFigure(store, active, f, "tmax", { title: "°C", unit: " °C", legend: true, limit: lim });
        var tavg = lineFigure(store, active, f, "tavg", { title: "°C", unit: " °C", legend: false });
        var soc = lineFigure(store, active, f, "soc", { title: "SoC (%)", unit: " %", scale: 100, legend: false,
                                                        minPad: 3, ceiling: 104, floor: -2, single: true });
        var d = store[vm];
        var readout = fmtElapsed(d.times[f]) + "   ·   SoC " + (d.soc[f] * 100).toFixed(0) +
                      "%   ·   peak " + d.tmax[f].toFixed(1) + " °C";
        return [vol, tmax, tavg, soc, readout];
      },

      /* play/pause button */
      toggle: function (clicks, disabled) {
        var nowDisabled = !disabled;
        return [nowDisabled, nowDisabled ? "Play" : "Pause"];
      },

      /* playback tick or a click on a chart -> new frame */
      advance: function (ticks, c1, c2, c3, current, max, store) {
        var ctx = window.dash_clientside.callback_context;
        var trig = (ctx.triggered && ctx.triggered.length) ? ctx.triggered[0].prop_id : "";
        if (trig.indexOf("-graph.clickData") > -1) {
          var click = trig.indexOf("tmax") === 0 ? c1 : trig.indexOf("tavg") === 0 ? c2 : c3;
          if (click && click.points && click.points.length && store && store.meta) {
            var mode = Object.keys(store).filter(function (k) { return k !== "meta"; })[0];
            var times = store[mode].times, x = click.points[0].x * 60, best = 0, dist = Infinity;
            for (var i = 0; i < times.length; i++) {
              var dd = Math.abs(times[i] - x);
              if (dd < dist) { dist = dd; best = i; }
            }
            return clamp(best, 0, max || 0);
          }
          return window.dash_clientside.no_update;
        }
        if (trig.indexOf("play-interval") === 0) {
          var nxt = (current || 0) + 1;
          return nxt > (max || 0) ? 0 : nxt;
        }
        return window.dash_clientside.no_update;
      },

      /* slider value read-outs */
      labels: function (c, amb, lx, ly) {
        return [Number(c).toFixed(1) + " C", amb + " °C", lx + " cm", ly + " cm"];
      },
    },
  });
})();
