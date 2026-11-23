// Validação contra dados oficiais
(function () {
  const $ = (id) => document.getElementById(id);
  const id = window.ANALYSIS_ID;
  const api = `/api/analises/${id}/validacao`;
  const fileUrl = (name) => SismoS2.file(`/api/analises/${id}/arquivos/${name}?t=${Date.now()}`);
  const pct = (v) => (v == null ? "—" : `${(100 * v).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`);
  const num = (v, d = 3) => (v == null ? "—" : Number(v).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const OUTCOME_STYLE = {
    tp: { color: "#22c55e", radius: 5 }, fn: { color: "#ef4444", radius: 5 },
    fp: { color: "#f59e0b", radius: 4 }, tn: { color: "#94a3b8", radius: 2.5 }, ignored: { color: "#a78bfa", radius: 3 },
  };
  let unitsLayer = null;

  $("val-source").addEventListener("change", (e) => {
    const file = e.target.value === "file";
    $("val-ems").hidden = file;
    $("val-file-wrap").hidden = !file;
  });

  $("val-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("val-error").hidden = true;
    const source = $("val-source").value;
    const common = { source, buffer_m: $("val-buffer").value, possible_mode: $("val-possible").value,
      unit_mode: $("val-unit").value, grid_threshold: Number($("val-grid-thr").value) / 100 };
    let opts;
    if (source === "file") {
      const f = $("val-file").files[0];
      if (!f) return showError("Selecione um arquivo.");
      const fd = new FormData();
      Object.entries(common).forEach(([k, v]) => fd.append(k, v));
      fd.append("file", f);
      opts = { method: "POST", body: fd };
    } else {
      opts = {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...common, activation: $("val-activation").value, aoi: $("val-aoi").value }),
      };
    }
    $("val-btn").disabled = true;
    try {
      const res = await fetch(api, opts);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Erro ao iniciar a validação");
      $("val-form").hidden = true;
      $("val-results").hidden = true;
      poll();
    } catch (e) {
      showError(e.message);
    } finally {
      $("val-btn").disabled = false;
    }
  });

  $("val-again").addEventListener("click", () => {
    $("validation").classList.remove("has-results");
    $("val-form").hidden = false;
    $("val-form").scrollIntoView({ behavior: "smooth" });
  });

  function showError(msg) {
    $("val-error").textContent = msg;
    $("val-error").hidden = false;
  }

  function poll() {
    fetch(SismoS2.api(api)).then((r) => r.json()).then((v) => {
      if (v.status === "none") return;
      if (v.status === "processing") {
        $("val-form").hidden = true;
        $("val-progress").hidden = false;
        $("val-bar").style.width = `${v.progress}%`;
        $("val-msg").textContent = v.message;
        return setTimeout(poll, 2000);
      }
      $("val-progress").hidden = true;
      if (v.status === "error") {
        $("val-form").hidden = false;
        return showError(v.error);
      }
      render(v);
    }).catch(() => setTimeout(poll, 4000));
  }

  function render(v) {
    $("val-form").hidden = true;
    $("validation").classList.add("has-results");
    $("val-results").hidden = false;
    const c = v.current;
    const ref = refLabel(v);
    $("val-source-info").textContent =
      `Referência: ${ref.long} · ${v.n_evaluable.toLocaleString("pt-BR")} edificações avaliadas ` +
      `(${v.n_positive} com dano, ${v.n_negative} sem dano) · tolerância ${v.buffer_m} m` +
      (v.unit_label ? ` · unidade: ${v.unit_label}` : "") +
      (v.possible_label ? ` · "possivelmente": ${v.possible_label}` : (v.include_possible ? " · 'possivelmente' conta como dano" : ""));

    const card = (label, value, sub) => `<div class="metric"><span>${label}</span><strong>${value}</strong>${sub ? `<small>${sub}</small>` : ""}</div>`;
    $("val-cards").innerHTML =
      card("Precisão", pct(c.precision), "dos alertas, quantos eram dano") +
      card("Recall", pct(c.recall), "dos danos, quantos foram achados") +
      card("F1", num(c.f1), `base "tudo é dano": ${num(v.all_positive_baseline.f1)}`) +
      card("Kappa", num(c.kappa), kappaLabel(c.kappa)) +
      card("Exatidão global", pct(c.overall_accuracy), `prevalência de dano: ${pct(v.prevalence)}`);

    renderUrban(v.urban);
    renderSensitivity(v.sensitivity);
    renderCI(v.confidence_intervals);
    renderGrid(v.grid);
    renderGridControls(v.grid_controls);
    renderMain(v);
    renderNeighborhood(v.neighborhood);
    renderLink(v.link_diagnostics);
    $("val-confusion").innerHTML = `
      <tr><th></th><th colspan="2">Referência (${esc(ref.short)})</th></tr>
      <tr><th>Previsto</th><th>Com dano</th><th>Sem dano</th></tr>
      <tr><th>Mudança</th><td class="tp">${c.tp}<small>VP</small></td><td class="fp">${c.fp}<small>FP</small></td></tr>
      <tr><th>Sem mudança</th><td class="fn">${c.fn}<small>FN</small></td><td class="tn">${c.tn}<small>VN</small></td></tr>`;

    const order = ["destroyed", "damaged", "possibly", "none"];
    $("val-grades").innerHTML = `<thead><tr><th>Grau (referência)</th><th>Prédios</th><th>Marcados</th></tr></thead><tbody>` +
      order.filter((g) => v.by_grade[g] && v.by_grade[g].n).map((g) => {
        const d = v.by_grade[g];
        return `<tr><td>${d.label}<div class="bar"><span style="width:${100 * (d.rate || 0)}%;background:${g === "none" ? "#f59e0b" : "#22c55e"}"></span></div></td>
          <td>${d.n}</td><td>${pct(d.rate)}</td></tr>`;
      }).join("") + "</tbody>";

    const best = v.best;
    $("val-experiments").innerHTML = `<caption>★ melhor F1 · ● configuração desta análise · passe o mouse para ver a matriz</caption><thead><tr><th>Limiar</th><th>Alvo</th><th>Prec.</th><th>Rec.</th><th>F1</th><th>κ</th></tr></thead><tbody>` +
      v.experiments.map((r) => {
        const cls = [r.is_current ? "current" : "", r === best || (r.method_label === best.method_label && r.target === best.target) ? "best" : ""].join(" ");
        return `<tr class="${cls}" title="${r.changed_percent}% da área marcada · VP ${r.tp} · FP ${r.fp} · FN ${r.fn} · VN ${r.tn}">
          <td>${r.method_label}</td><td>${r.target === "todas" ? "todas" : "entulho"}</td>
          <td>${num(r.precision, 2)}</td><td>${num(r.recall, 2)}</td><td><b>${num(r.f1, 2)}</b></td><td>${num(r.kappa, 2)}</td></tr>`;
      }).join("") + "</tbody>";

    $("val-notes").innerHTML = (v.notes || []).map((n) => `<li>${n}</li>`).join("");
    $("val-csv").href = fileUrl("validation.csv");
    $("val-json").href = fileUrl("validation.json");
    $("val-geojson").href = fileUrl("validation_units.geojson");

    if (unitsLayer) { SismoS2.map.removeLayer(unitsLayer); unitsLayer = null; }
    $("val-layer-toggle").checked = false;
    $("val-legend").hidden = true;
  }

  $("val-layer-toggle").addEventListener("change", async (e) => {
    $("val-legend").hidden = !e.target.checked;
    if (!e.target.checked) { if (unitsLayer) SismoS2.map.removeLayer(unitsLayer); return; }
    if (!unitsLayer) {
      const gj = await fetch(fileUrl("validation_units.geojson")).then((r) => r.json());
      const order = { tn: 0, ignored: 1, fp: 2, fn: 3, tp: 4 };
      gj.features.sort((a, b) => order[a.properties.outcome] - order[b.properties.outcome]);
      const renderer = L.canvas({ padding: 0.3 });
      unitsLayer = L.geoJSON(gj, {
        pointToLayer: (f, latlng) => {
          const s = OUTCOME_STYLE[f.properties.outcome];
          return L.circleMarker(latlng, { renderer, radius: s.radius, color: "#0f172a", weight: 0.6,
            fillColor: s.color, fillOpacity: 0.95 });
        },
        onEachFeature: (f, layer) => layer.bindTooltip(
          `${{ tp: "Dano detectado", fn: "Dano NÃO detectado", fp: "Alarme falso", tn: "Sem dano", ignored: "Ignorado" }[f.properties.outcome]}` +
          ` · referência: ${{ destroyed: "destruído", damaged: "danificado", possibly: "possivelmente danificado", none: "sem dano" }[f.properties.grade]}` +
          (f.properties.origin === "referencia" ? " (ponto sem prédio no OSM)" : "")),
      });
    }
    unitsLayer.addTo(SismoS2.map);
  });

  // ---- avaliação por quadrícula
  $("val-grid-thr").addEventListener("input", (e) => { $("val-grid-thr-label").textContent = e.target.value; });
  let gridData = null, gridLayer = null;
  const gridColor = (c) => {
    const t = Math.min(1, c / 0.5);
    const stops = [[254, 240, 138], [249, 115, 22], [127, 29, 29]];
    const [a, b, f] = t < 0.5 ? [stops[0], stops[1], t / 0.5] : [stops[1], stops[2], (t - 0.5) / 0.5];
    return `rgb(${a.map((x, i) => Math.round(x + (b[i] - x) * f)).join(",")})`;
  };

  // rótulo da referência sempre a partir da fonte real
  const esc = (t) => String(t == null ? "" : t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  function refLabel(v) {
    if (v.source_short) return { short: v.source_short, long: v.source_label || v.source };
    const s = (v.source || "").toLowerCase();
    if (s.includes("hotosm") || s.includes("hot_")) return { short: "HOT/OSM", long: "Prédios destruídos mapeados pelo HOT no OpenStreetMap" };
    if (s.startsWith("copernicus ems") || s.includes("emsr")) return { short: "Copernicus EMS", long: v.source };
    return { short: "arquivo enviado", long: v.source || "Arquivo enviado" };
  }

  // 1) resultado principal
  function renderMain(v) {
    const g = v.grid && v.grid.sizes && v.grid.sizes["200"];
    const b = v.grid_controls && v.grid_controls.sizes && v.grid_controls.sizes["200"];
    const okB = b && !b.error && b.analyses && b.analyses.length > 1;
    const ci = (x) => (x && x[0] != null ? `<small>IC 95% ${num(x[0], 2)} a ${num(x[1], 2)}</small>` : "");
    const p = (x) => (x == null ? "—" : x < 0.001 ? "< 0,001" : num(x, 3));
    const bar = (val, color) => `<span class="bar"><span style="width:${Math.max(0, Math.min(1, val || 0)) * 100}%;background:${color}"></span></span>`;
    let html = "", verdict = "";
    if (okB) {
      const ev = b.analyses[0], mean = b.controls_mean || {}, d = mean.vs_event_delong || {};
      const nCtl = b.analyses.length - 1;
      html = `<div class="auc-compare big">
          <span>Evento</span>${bar(ev.auc, "var(--red)")}<b>${num(ev.auc, 2)}</b>
          <span>Média de ${nCtl} ${nCtl === 1 ? "ano" : "anos"} de controle</span>${bar(mean.auc, "var(--ink-faint)")}<b>${num(mean.auc, 2)}</b>
        </div>
        <dl class="val-stats">
          <div><dt>AUC do evento</dt><dd>${num(ev.auc, 2)} ${ci(ev.auc_ci95)}</dd></div>
          <div><dt>Diferença evento − controles</dt><dd>${d.diff > 0 ? "+" : ""}${num(d.diff, 2)} ${ci(d.ci95)}</dd></div>
          <div><dt>p de DeLong (pareado)</dt><dd>${p(d.p_value)}</dd></div>
          <div><dt>Células avaliadas</dt><dd>${b.n_cells} <small>${b.n_damaged_cells} danificadas</small></dd></div>
        </dl>`;
      const sig = d.p_value != null && d.p_value < 0.05 && d.diff > 0;
      verdict = sig ? `<b class="ok-text">Sinal acima do ruído.</b> A mudança separa as células danificadas melhor do que nos anos sem terremoto.`
                    : `<b class="warn-text">Sem diferença significativa</b> em relação aos anos sem terremoto: o sinal pode ser ruído de fundo.`;
    } else if (g && g.auc) {
      html = `<div class="auc-compare big"><span>Evento</span>${bar(g.auc.value, "var(--red)")}<b>${num(g.auc.value, 2)}</b></div>
        <dl class="val-stats"><div><dt>AUC do evento</dt><dd>${num(g.auc.value, 2)} ${ci(g.auc.ci95)}</dd></div>
          <div><dt>Células avaliadas</dt><dd>${g.n_cells} <small>${g.n_damaged_cells} danificadas</small></dd></div></dl>`;
      verdict = `Sem anos de controle para comparar: rode-os na aba <b>Controles</b> e valide de novo.`;
    } else {
      html = `<p class="muted">Área pequena demais para células de 200 m.</p>`;
    }
    $("val-auc").innerHTML = html + `<p class="hint">AUC: chance de uma célula danificada ter mais mudança do que uma não danificada
      (0,5 = acaso; 1 = separação perfeita). Referência: ${esc(refLabel(v).long)}.</p>`;
    $("val-verdict").innerHTML = verdict;
    $("val-triage-svg").innerHTML = g && g.triage_curve ? triageSvg(g.triage_curve, g.triage) : `<p class="muted small">Sem curva de triagem.</p>`;
  }

  function triageSvg(tc, rows) {
    const W = 420, H = 260, L = 40, R = 12, T = 12, B = 34;
    const x = (v) => L + v * (W - L - R), y = (v) => T + (1 - v) * (H - T - B);
    const path = (ys) => tc.x.map((xv, i) => `${i ? "L" : "M"}${x(xv).toFixed(1)} ${y(ys[i]).toFixed(1)}`).join(" ");
    const ticks = [0, 0.25, 0.5, 0.75, 1];
    const grid = ticks.map((t) => `<line x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/><text x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${t * 100}%</text>
      <text x="${x(t)}" y="${H - 14}" text-anchor="middle">${t * 100}%</text>`).join("");
    const marks = (rows || []).slice(0, 3).map((r) => `<circle cx="${x(r.fraction_cells)}" cy="${y(r.share_of_damaged)}" r="4"/>
      <text class="mark" x="${x(r.fraction_cells) + 7}" y="${y(r.share_of_damaged) + 14}">${pct(r.share_of_damaged)}</text>`).join("");
    return `<svg viewBox="0 0 ${W} ${H}" class="triage-svg" role="img"
        aria-label="Curva de triagem: visitando 10% das células alcançam-se ${rows && rows[0] ? pct(rows[0].share_of_damaged) : "—"} dos prédios com dano">
      <g class="grid">${grid}</g>
      <text class="axis" x="${(L + W - R) / 2}" y="${H - 1}" text-anchor="middle">células visitadas</text>
      <path class="oracle" d="${path(tc.oracle)}"/><path class="random" d="${path(tc.random)}"/>
      <path class="system" d="${path(tc.system)}"/><g class="marks">${marks}</g>
    </svg>
    <ul class="triage-key"><li><span class="k system"></span>Sistema (mais mudança primeiro)</li>
      <li><span class="k oracle"></span>Máximo possível</li><li><span class="k random"></span>Ao acaso</li></ul>`;
  }

  function renderGrid(g) {
    gridData = g || null;
    if (!g) { $("val-grid-table").innerHTML = ""; return; }
    const sizes = Object.keys(g.sizes).sort((a, b) => a - b);
    const ci = (x) => (x && x[0] != null ? `<small class="muted">(${num(x[0], 2)}–${num(x[1], 2)})</small>` : "");
    const cell = (m, f) => ("error" in m ? "—" : f(m));
    const rows = [
      ["Células avaliadas", (m) => m.n_cells.toLocaleString("pt-BR")],
      ["Células danificadas (≥ 20%)", (m) => `${m.n_damaged_cells} (${pct(m.n_damaged_cells / m.n_cells)})`],
      ["ρ de Spearman (IC 95%)", (m) => `${num(m.spearman.rho, 2)} ${ci(m.spearman.ci95)}`],
      ["AUC (IC 95%)", (m) => `${num(m.auc.value, 2)} ${ci(m.auc.ci95)}`],
      [`Precisão @ ${Math.round(100 * g.threshold)}%`, (m) => pct(m.at_threshold.precision)],
      [`Recall @ ${Math.round(100 * g.threshold)}%`, (m) => pct(m.at_threshold.recall)],
      [`F1 @ ${Math.round(100 * g.threshold)}%`, (m) => num(m.at_threshold.f1, 2)],
      ...[0, 1, 2].map((i) => [`Triagem: ${Math.round(100 * [0.1, 0.2, 0.3][i])}% das células`,
        (m) => `${pct(m.triage[i].share_of_damaged)} <small class="muted">(aleatório ${pct(m.triage[i].random_expected)};
          máx. ${pct(m.triage[i].oracle_share)})</small>`]),
    ];
    $("val-grid-table").innerHTML = `<thead><tr><th></th>${sizes.map((s) => `<th>${s} m</th>`).join("")}</tr></thead><tbody>` +
      rows.map(([label, f]) => `<tr><td>${label}</td>${sizes.map((s) => `<td>${cell(g.sizes[s], f)}</td>`).join("")}</tr>`).join("") +
      `</tbody><caption>Só células com ≥ ${g.rules.min_buildings} prédios do OSM e ≥ ${100 * g.rules.min_valid_share}% dos pixels válidos.
        Triagem = % de todos os prédios com dano que estão nas células de maior % de mudança.</caption>`;
    $("val-grid-size").value = g.sizes["200"] ? "200" : String(g.default_size);
    showGridSize();
  }

  function showGridSize() {
    if (!gridData) return;
    const m = gridData.sizes[$("val-grid-size").value];
    $("val-grid-plot").hidden = !m || "error" in m;
    if (m && !("error" in m)) $("val-grid-plot").src = SismoS2.file(`/api/analises/${id}/arquivos/${m.plot}?t=${Date.now()}`);
    $("val-grid-triage").innerHTML = m && m.triage ? `<thead><tr><th>Visitar</th><th>Células</th><th>Prédios com dano alcançados</th>
      <th>Aleatório</th><th>Máximo possível</th><th>Ganho</th></tr></thead><tbody>` + m.triage.map((t) => `<tr>
      <td>${pct(t.fraction_cells)}</td><td>${t.n_cells}</td><td>${t.damaged_buildings} (${pct(t.share_of_damaged)})</td>
      <td>${pct(t.random_expected)}</td><td>${pct(t.oracle_share)}</td><td>${num(t.lift, 1)}×</td></tr>`).join("") + "</tbody>" : "";
    if ($("val-grid-toggle").checked) loadGridLayer();
  }
  $("val-grid-size").addEventListener("change", showGridSize);

  async function loadGridLayer() {
    const map = SismoS2.map;
    if (gridLayer) { map.removeLayer(gridLayer); gridLayer = null; }
    const m = gridData && gridData.sizes[$("val-grid-size").value];
    if (!m) return;
    const gj = await fetch(SismoS2.file(`/api/analises/${id}/arquivos/${m.geojson}?t=${Date.now()}`)).then((r) => r.json());
    gridLayer = L.geoJSON(gj, {
      style: (f) => ({ fillColor: gridColor(f.properties.change), fillOpacity: 0.55,
        color: f.properties.damaged ? "#dc2626" : "#1f2937", weight: f.properties.damaged ? 2.5 : 0.4 }),
      onEachFeature: (f, l) => l.bindTooltip(`Mudança: ${pct(f.properties.change)} dos pixels<br>
        Dano (referência): ${f.properties.n_damaged} de ${f.properties.n_buildings} prédios (${pct(f.properties.damage)})<br>
        Magnitude CVA média: ${num(f.properties.magnitude, 3)}`),
    }).addTo(map);
  }
  $("val-grid-toggle").addEventListener("change", (e) => {
    $("val-grid-legend").hidden = !e.target.checked;
    if (e.target.checked) loadGridLayer();
    else if (gridLayer) { SismoS2.map.removeLayer(gridLayer); gridLayer = null; }
  });

  // evento × controles
  function renderGridControls(gc) {
    $("val-gc").hidden = !gc;
    if (!gc) return;
    const p = (x) => (x == null ? "—" : x < 0.001 ? "< 0,001" : num(x, 3));
    const ci = (x) => (x && x[0] != null ? ` <small class="muted">(${num(x[0], 2)}–${num(x[1], 2)})</small>` : "");
    $("val-gc-note").textContent = `Pixels válidos no evento e em todos os controles: ${pct(gc.common_valid_share)} da área. ` +
      gc.controls.map((c) => `Controle ${c.year}: ${c.dates.join(" → ")} (AOT ${c.aot.map((x) => (x == null ? "?" : num(x, 2))).join(" / ")})`).join(" · ");
    let html = `<thead><tr><th>Células</th><th>Análise</th><th>AUC (IC 95%)</th><th>Evento − controle (DeLong)</th><th>p bruto</th>
      <th>p Holm</th><th>Triagem 10/20/30%</th></tr></thead><tbody>`;
    Object.keys(gc.sizes).sort((a, b) => a - b).forEach((s) => {
      const b = gc.sizes[s];
      if (b.error) { html += `<tr><td>${s} m</td><td colspan="6">${b.error}</td></tr>`; return; }
      b.analyses.forEach((a, i) => {
        const d = a.vs_event_delong;
        html += `<tr class="${a.is_event ? "current" : ""}"><td>${i === 0 ? `${s} m <small class="muted">(${b.n_cells} cél.)</small>` : ""}</td>
          <td>${a.label}</td><td>${num(a.auc, 2)}${ci(a.auc_ci95)}</td>
          <td>${d ? `${d.diff > 0 ? "+" : ""}${num(d.diff, 2)}${ci(d.ci95)}` : ""}</td><td>${d ? p(d.p_value) : ""}</td>
          <td>${d ? p(d.p_holm) : ""}</td><td>${a.triage.map((t) => pct(t.share_of_damaged)).join(" / ")}</td></tr>`;
      });
      if (b.controls_mean) {
        const d = b.controls_mean.vs_event_delong;
        const prim = d && d.primary;
        html += `<tr class="agg ${prim ? "primary" : ""}"><td></td><td>Média dos controles${prim ? " ★ principal" : ""}</td>
          <td>${num(b.controls_mean.auc, 2)}${b.controls_mean.auc_sd != null ? ` ± ${num(b.controls_mean.auc_sd, 2)}` : ""}</td>
          <td>${d ? `${d.diff > 0 ? "+" : ""}${num(d.diff, 2)}${ci(d.ci95)}` : ""}</td><td>${d ? p(d.p_value) : ""}</td>
          <td>${prim ? "— (principal)" : d ? p(d.p_holm) : ""}</td>
          <td>${b.controls_mean.triage.map((x) => pct(x)).join(" / ")}</td></tr>`;
      }
    });
    const mt = gc.multiple_testing || {};
    $("val-gc-table").innerHTML = html + `</tbody><caption>DeLong pareado: diferença de AUC nas mesmas células, com IC 95%.
      ★ Comparação principal (definida antes): evento × média dos controles com células de 200 m — p sem correção.
      Demais ${mt.family_size || ""} comparações deste local: p corrigido por Holm (controla a chance de algum falso
      positivo entre todas). "Média dos controles" usa a % de mudança média dos controles em cada célula.</caption>`;
    $("val-gc-plot").hidden = !gc.roc_plot;
    if (gc.roc_plot) $("val-gc-plot").src = SismoS2.file(`/api/analises/${id}/arquivos/${gc.roc_plot}?t=${Date.now()}`);
  }

  // intervalos de confiança por bootstrap
  function renderCI(ci) {
    if (!ci) { $("val-ci").innerHTML = ""; return; }
    const names = { precision: "Precisão", recall: "Recall", f1: "F1", kappa: "Kappa",
      recall_destroyed: "Recall – Destroyed", recall_damaged: "Recall – Damaged", recall_possibly: "Recall – Possibly damaged" };
    const isRate = (k) => k !== "f1" && k !== "kappa";
    const f = (k, x) => (x == null ? "—" : isRate(k) ? pct(x) : num(x));
    $("val-ci").innerHTML = `<thead><tr><th>Métrica</th><th>Valor</th><th>IC 95%</th><th>n</th></tr></thead><tbody>` +
      Object.keys(names).filter((k) => ci.metrics[k]).map((k) => { const m = ci.metrics[k];
        return `<tr><td>${names[k]}</td><td><b>${f(k, m.value)}</b></td><td>${f(k, m.lo)} – ${f(k, m.hi)}</td><td>${m.n}</td></tr>`; }).join("") +
      `</tbody><caption>Percentis 2,5 e 97,5 de ${ci.n_boot} reamostragens dos prédios com reposição. n = prédios na
        matriz (métricas gerais) ou da classe (recall por classe).</caption>`;
  }

  // recall/alarme falso por faixa de distância ao prédio destruído mais próximo
  function renderNeighborhood(nb) {
    if (!nb) { $("val-neigh").innerHTML = ""; $("val-neigh-plot").hidden = true; return; }
    $("val-neigh-plot").hidden = false;
    $("val-neigh-plot").src = SismoS2.file(`/api/analises/${id}/arquivos/${nb.plot}?t=${Date.now()}`);
    const cell = (c) => (c.n ? `${pct(c.rate)} <small class="muted">(${pct(c.lo)}–${pct(c.hi)}; n=${c.n})</small>` : "—");
    $("val-neigh").innerHTML = `<thead><tr><th>Distância</th><th>Recall Damaged</th><th>Recall Possibly</th>
      <th>Alarme falso (sem dano)</th></tr></thead><tbody>` +
      nb.rows.map((r) => `<tr><td>${r.band}</td><td>${cell(r.damaged_recall)}</td><td>${cell(r.possibly_recall)}</td>
        <td>${cell(r.false_alarm)}</td></tr>`).join("") +
      `</tbody><caption>Entre parênteses: IC 95% de Wilson e número de prédios. ${nb.note}.</caption>`;
  }

  // modo de unidade × tratamento do "possivelmente", com recall por classe
  function renderSensitivity(rows) {
    if (!rows) { $("val-sensitivity").innerHTML = ""; return; }
    const rc = (r, g) => (r.recall_by_class[g].rate == null ? "—" : pct(r.recall_by_class[g].rate));
    $("val-sensitivity").innerHTML = `<thead><tr><th>Unidade</th><th>"Possivelmente"</th><th>Prec.</th><th>Rec.</th>
      <th>F1</th><th>κ</th><th>Rec. destr.</th><th>Rec. danif.</th><th>Rec. possív.</th></tr></thead><tbody>` +
      rows.map((r) => `<tr class="${r.is_selected ? "current" : ""}"><td>${r.unit_label}</td><td>${r.possible_label}</td>
        <td>${num(r.precision, 2)}</td><td>${num(r.recall, 2)}</td><td><b>${num(r.f1, 2)}</b></td><td>${num(r.kappa, 2)}</td>
        <td>${rc(r, "destroyed")}</td><td>${rc(r, "damaged")}</td><td>${rc(r, "possibly")}</td></tr>`).join("") +
      `</tbody><caption>● combinação escolhida no formulário · recall por classe = % dos pontos daquela classe
        com mudança detectada (não depende do tratamento do "possivelmente")</caption>`;
  }

  // de onde vêm as diferenças entre "pontos do GRA" e "prédios avaliados"
  function renderLink(diag) {
    if (!diag) { $("val-link").innerHTML = ""; return; }
    const order = ["destroyed", "damaged", "possibly", "none"].filter((g) => diag[g]);
    const dist = (d) => Object.entries(d.distancia_predio_mais_proximo).filter(([, n]) => n)
      .map(([k, n]) => `${k}: ${n}`).join("; ") || "—";
    $("val-link").innerHTML = `<thead><tr><th>Classe</th><th>Pontos</th><th>Ligados</th><th>Sem prédio ≤ 10 m</th>
      <th>Fundidos</th><th>Fora da área</th><th>Pixel inválido</th></tr></thead><tbody>` +
      order.map((g) => { const d = diag[g]; return `<tr><td>${d.label}</td><td>${d.total}</td><td>${d.ligados_osm}</td>
        <td title="distância até o prédio do OSM mais próximo: ${dist(d)}">${d.sem_predio_osm_10m}</td>
        <td>${d.fundidos_em_predio_compartilhado}</td><td>${d.fora_da_area_comum}</td><td>${d.em_pixel_invalido}</td></tr>`; }).join("") +
      `</tbody><caption>Fundidos = pontos ligados a um prédio do OSM que já tinha outro ponto (no modo "prédio do OSM"
        viram uma unidade só). Passe o mouse em "Sem prédio" para ver a distância até o prédio mais próximo.</caption>`;
  }

  // mesma matriz restrita aos prédios em área construída
  function renderUrban(u) {
    $("val-urban").hidden = !u;
    if (!u) return;
    const card = (label, value) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`;
    $("val-urban-cards").innerHTML = card("Precisão", pct(u.precision)) + card("Recall", pct(u.recall)) +
      card("F1", num(u.f1)) + card("Kappa", num(u.kappa)) +
      card("Prédios urbanos", `${u.n_buildings.toLocaleString("pt-BR")} (${pct(u.share_of_evaluated)})`);
    $("val-urban-confusion").innerHTML = `
      <tr><th></th><th colspan="2">Referência</th></tr>
      <tr><th>Previsto</th><th>Com dano</th><th>Sem dano</th></tr>
      <tr><th>Mudança</th><td class="tp">${u.tp}<small>VP</small></td><td class="fp">${u.fp}<small>FP</small></td></tr>
      <tr><th>Sem mudança</th><td class="fn">${u.fn}<small>FN</small></td><td class="tn">${u.tn}<small>VN</small></td></tr>`;
  }

  function kappaLabel(k) {
    if (k == null) return "";
    if (k < 0) return "pior que o acaso";
    if (k <= 0.2) return "concordância leve";
    if (k <= 0.4) return "concordância razoável";
    if (k <= 0.6) return "concordância moderada";
    if (k <= 0.8) return "concordância substancial";
    return "concordância quase perfeita";
  }

  poll();
})();
