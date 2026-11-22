// Página de resultado: acompanha o processamento e exibe as camadas no Leaflet.
(function () {
  const $ = (id) => document.getElementById(id);
  const id = window.ANALYSIS_ID;
  const fileUrl = (name) => SismoS2.file(`/api/analises/${id}/arquivos/${name}`);
  const fmt = (n, d = 2) => (n == null ? "—" : Number(n).toLocaleString("pt-BR", { maximumFractionDigits: d }));

  const base = SismoS2.baseLayers();
  const map = L.map("map", { layers: [base["Satélite (Esri)"]], preferCanvas: true });
  SismoS2.map = map; // usado por validation.js
  let areaBounds = SismoS2.bboxToBounds(window.ANALYSIS_BBOX);
  L.control.scale({ imperial: false }).addTo(map);
  const aoi = L.rectangle(areaBounds, { color: "#ef4444", weight: 2, fill: false, dashArray: "6 4" }).addTo(map);

  // Enquadra a área analisada. O invalidateSize() é essencial: o mapa é criado antes de o
  // layout (barra lateral + flex) definir o tamanho final do contêiner, e sem ele o Leaflet
  // calcula o zoom para um contêiner menor e a área aparece pequena.
  function fitArea(animate) {
    map.invalidateSize();
    map.fitBounds(areaBounds, { padding: [12, 12], maxZoom: 17, animate: !!animate });
  }
  fitArea(false);
  requestAnimationFrame(() => fitArea(false));
  window.addEventListener("load", () => fitArea(false));
  window.addEventListener("resize", () => map.invalidateSize());

  const FitControl = L.Control.extend({
    options: { position: "topleft" },
    onAdd() {
      const btn = L.DomUtil.create("a", "leaflet-bar fit-btn");
      btn.href = "#";
      btn.title = "Enquadrar a área analisada";
      btn.innerHTML = "⤢";
      L.DomEvent.on(btn, "click", (e) => { L.DomEvent.stop(e); fitArea(true); });
      return btn;
    },
  });
  new FitControl().addTo(map);

  // Barra de cores das camadas contínuas (ΔNDVI e magnitude)
  const colorbarControl = L.control({ position: "bottomright" });
  colorbarControl.onAdd = () => {
    const div = L.DomUtil.create("div", "colorbars");
    div.hidden = true;
    L.DomEvent.disableClickPropagation(div); // clicar na legenda não consulta pixel
    return div;
  };
  colorbarControl.addTo(map);
  let scales = null;

  // group "base": imagens (checkbox, uma por vez) · "analysis": seleção única (radio)
  // · "mask": hachura dos pixels sem informação (checkbox independente)
  const LAYERS = [
    { key: "before", file: "before.png", label: "Imagem antes (cor verdadeira)", group: "base" },
    { key: "after", file: "after.png", label: "Imagem depois (cor verdadeira)", group: "base" },
    { key: "change", file: "change.png", label: "Mapa de mudanças (classes)", group: "analysis" },
    { key: "dndvi", file: "dndvi.png", label: "ΔNDVI (depois − antes)", group: "analysis" },
    { key: "magnitude", file: "magnitude.png", label: "Magnitude CVA", group: "analysis" },
    { key: "none", file: null, label: "Nenhuma camada de análise", group: "analysis" },
    { key: "mask", file: "mask.png", label: "Nuvem / sem dado (hachura)", group: "mask" },
  ];
  const overlays = {};

  let wasProcessing = false;
  function poll() {
    fetch(SismoS2.api(`/api/analises/${id}`))
      .then((r) => r.json())
      .then((a) => {
        $("progress-bar").style.width = `${a.progress}%`;
        $("progress-msg").textContent = a.message;
        if (SismoS2.updateProcSteps) SismoS2.updateProcSteps(a.progress);
        if (a.status === "done") {
          // terminou com a página aberta: recarrega para mostrar KPIs e abas montados no servidor
          if (wasProcessing) { window.location.reload(); return; }
          return render(a);
        }
        if (a.status === "error") {
          // a tela de processamento continua, mas mostra o erro no lugar da barra
          $("progress-box").querySelector(".progress").hidden = true;
          $("proc-steps").hidden = true;
          $("progress-msg").hidden = true;
          $("error-box").textContent = a.error || "Erro desconhecido";
          $("error-box").hidden = false;
          showDelete();
          return;
        }
        wasProcessing = true;
        setTimeout(poll, 2000);
      })
      .catch(() => setTimeout(poll, 4000));
  }

  function render(a) {
    const r = a.result;
    $("progress-box").hidden = true;
    $("results").hidden = false;
    const bounds = L.latLngBounds(r.bounds);
    areaBounds = bounds;
    scales = r.scales || null;
    fitArea(false);
    enableProbe(a);
    const opacity = $("opacity").value / 100;

    // Camadas: imagens antes/depois = checkbox (uma por vez); análise = radio (seleção única);
    // hachura de nuvem/sem dado = checkbox independente, sempre por cima.
    const list = $("layer-list");
    const groupTitle = { base: "Imagens", analysis: "Camada de análise", mask: "Máscara" };
    let lastGroup = null;
    LAYERS.forEach((ly) => {
      if (ly.file) {
        overlays[ly.key] = L.imageOverlay(fileUrl(ly.file), bounds, {
          opacity: ly.group === "analysis" ? opacity : 1,
          className: "pixelated",
          zIndex: { base: 1, analysis: 2, mask: 3 }[ly.group],
        });
      }
      if (ly.group !== lastGroup) {
        const h = document.createElement("div");
        h.className = "layer-group";
        h.textContent = groupTitle[ly.group];
        list.appendChild(h);
        lastGroup = ly.group;
      }
      const type = ly.group === "analysis" ? "radio" : "checkbox";
      const row = document.createElement("label");
      row.className = "layer-row";
      row.innerHTML = `<input type="${type}" ${type === "radio" ? 'name="analysis-layer"' : ""} data-key="${ly.key}"> ${ly.label}`;
      list.appendChild(row);
    });
    list.addEventListener("change", (ev) => {
      const key = ev.target.dataset.key;
      const ly = LAYERS.find((l) => l.key === key);
      if (ly.group === "analysis") {
        selectAnalysisLayer(key);
      } else if (ev.target.checked) {
        if (ly.group === "base") {
          LAYERS.filter((l) => l.group === "base" && l.key !== key).forEach((l) => toggle(l.key, false));
        }
        toggle(key, true);
      } else toggle(key, false);
      aoi.bringToFront();
    });
    toggle("after", true);
    selectAnalysisLayer("none");   // a camada padrão é a prioridade de vistoria (priority.js); as classes são opcionais
    toggle("mask", true);

    $("opacity").addEventListener("input", (ev) => {
      LAYERS.filter((l) => l.group === "analysis" && l.file).forEach((l) => overlays[l.key].setOpacity(ev.target.value / 100));
    });

    $("legend").innerHTML = r.legend.map((l) =>
      `<li><span class="swatch" style="background:${l.color}"></span>${l.name}</li>`).join("") +
      `<li><span class="swatch hatch"></span>Nuvem / sem dado (sem informação)</li>`;

    renderWarnings(a);

    const s = r.statistics;
    const summary = [
      ["Área analisada (válida)", `${fmt(s.valid_area_km2)} km²`],
      ["Área com mudança", `${fmt(s.changed_area_km2, 3)} km² (${fmt(s.changed_percent)}%)`],
      ["Limiar CVA", `${fmt(s.cva_threshold, 4)} (${a.threshold_method === "otsu" ? "Otsu" : `μ + ${a.k}σ`})`],
      ...(s.valid_fraction != null ? [["Retângulo com pixels válidos", `${fmt(100 * s.valid_fraction, 1)}%`]] : []),
      [`NDVI médio antes → depois${r.ndvi_stats_mask ? " (sem água)" : ""}`,
        `${fmt(r.mean_ndvi_before, 3)} → ${fmt(r.mean_ndvi_after, 3)}`],
      ...(r.median_ndvi_before != null
        ? [[`NDVI mediano antes → depois${r.ndvi_stats_mask ? " (sem água)" : ""}`,
          `${fmt(r.median_ndvi_before, 3)} → ${fmt(r.median_ndvi_after, 3)}`]] : []),
      ...(s.dndvi_median != null ? [["ΔNDVI mediano (sem água)", fmt(s.dndvi_median, 3)]] : []),
      ...(r.median_composite ? [["Composição", "mediana de até 3 cenas por período"]] : []),
      ...(r.topo ? [["Correção topográfica", r.topo.applied
        ? `C-correction (Copernicus DEM; declividade média ${fmt(r.topo.slope_mean_deg, 1)}°)` +
          (r.topo.bands_total ? ` · ${r.topo.bands_corrected}/${r.topo.bands_total} bandas corrigidas (r ≥ 0,2)` : "")
        : (r.topo.requested ? "não aplicada (ver notas)" : "desligada")]] : []),
      ...(s.steep ? [[`Mudança em declividade > ${s.steep.limit_deg}°`,
        `${fmt(s.steep.changed_percent, 1)}% da mudança (essas encostas são ${fmt(s.steep.area_percent, 1)}% da área)`]] : []),
      ...(s.urban ? [["Área urbana analisada (WorldCover)", `${fmt(s.urban.area_km2, 2)} km² (${fmt(s.urban.percent_of_valid, 1)}% da área válida)`],
        ["Mudança dentro da área urbana", `${fmt(s.urban.changed_km2, 3)} km² (${fmt(s.urban.changed_percent, 2)}%)`]] : []),
      ...(s.bright_fixed_km2 ? [["Superfície clara fixa (não é nuvem)", `${fmt(s.bright_fixed_km2, 3)} km²`]] : []),
      ["Resolução da grade", `${r.grid.resolution_m} m · ${r.grid.width}×${r.grid.height} px`],
      ["Tempo de processamento", `${fmt(r.elapsed_s, 1)} s`],
    ];
    $("summary").innerHTML = summary.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");

    const maxPct = Math.max(...s.classes.filter((c) => c.id !== 0).map((c) => c.percent), 0.001);
    $("stats-body").innerHTML = s.classes.filter((c) => c.id !== 0).map((c) => `
      <tr><td><span class="swatch" style="background:${c.color}"></span>${c.name}
        <div class="bar"><span style="width:${(100 * c.percent) / maxPct}%;background:${c.color}"></span></div></td>
      <td>${fmt(c.area_km2, 3)}</td><td>${fmt(c.percent)}</td></tr>`).join("");

    const memberList = (sc) => (sc.n_scenes > 1
      ? `<span><b>Mediana de ${sc.n_scenes} cenas:</b> ${sc.scenes.map((m) =>
        `${m.datetime.slice(0, 10)} (${fmt(100 * m.clear_fraction, 0)}% limpa)`).join(", ")}</span>`
      : "");
    const sceneCard = (title, sc) => `
      <div class="scene"><strong>${title}${sc.n_scenes > 1 ? ` · ${sc.n_scenes} cenas` : ""}</strong>
        ${memberList(sc)}
        <span>${sc.datetime.slice(0, 10)} · ${sc.platform}</span>
        <span>Tile ${sc.tile} · órbita R${sc.relative_orbit != null ? sc.relative_orbit : "?"} · nuvens na cena ${fmt(sc.cloud_cover, 1)}%</span>
        <span>Área sem nuvem: ${fmt(100 * sc.clear_fraction, 1)}%</span>
        ${sc.radiometry ? `<span>Baseline ${sc.processing_baseline || "?"} · ${sc.radiometry}</span>` : ""}
        <code>${sc.item_id}</code></div>`;
    const alignment = r.same_tile && r.same_orbit
      ? `<div class="alert ok">✓ Mesmo tile e mesma órbita nas duas datas (imagens co-registradas).</div>` : "";
    $("scenes").innerHTML = alignment + sceneCard("Antes", r.before) + sceneCard("Depois", r.after);

    $("downloads").innerHTML = [
      ["change_classes.tif", "Classes de mudança"],
      ["dndvi.tif", "ΔNDVI"],
      ["magnitude.tif", "Magnitude CVA"],
      ["analysis.json", "Metadados (JSON)"],
    ].map(([f, l]) => `<a class="btn small" href="${fileUrl(f)}" download>${l}</a>`).join("");

    setupCompare(bounds);
    showDelete();
  }

  function toggle(key, on) {
    const cb = document.querySelector(`#layer-list input[data-key="${key}"]`);
    if (cb) cb.checked = on;
    if (!overlays[key]) return;
    if (on) overlays[key].addTo(map); else map.removeLayer(overlays[key]);
    updateColorbars();
  }

  function selectAnalysisLayer(key) {
    LAYERS.filter((l) => l.group === "analysis").forEach((l) => toggle(l.key, l.key === key));
  }

  // ---- avisos de qualidade (amarelos) no topo dos resultados
  function renderWarnings(a) {
    const list = (a.result.warnings || []).map((w) => (typeof w === "string" ? { message: w } : w));
    const notes = (a.result.notes || []).map((n) => `<div class="alert info">ℹ ${n}</div>`).join("");
    if ($("no-warnings")) $("no-warnings").hidden = list.length + (a.result.notes || []).length > 0;
    $("quality-warnings").innerHTML = notes + list.map((w) => `
      <div class="alert warn">⚠ ${w.message}
        ${w.action && w.action.type === "anniversary"
          && !SismoS2.STATIC ? `<div><a class="btn small" href="/nova?refazer=${id}&aniversario=1">Refazer com par de aniversário</a></div>` : ""}
      </div>`).join("");
  }

  function updateColorbars() {
    const box = colorbarControl.getContainer();
    const active = ["dndvi", "magnitude"].filter((k) => overlays[k] && map.hasLayer(overlays[k]));
    if (!scales || !active.length) { box.innerHTML = ""; box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = active.map((k) => colorbar(scales[k], k)).join("");
  }

  function colorbar(sc, key) {
    const span = sc.max - sc.min;
    const pos = (v) => `${(100 * (v - sc.min)) / span}%`;
    const grad = sc.stops.map(([p, c]) => `${c} ${(100 * p).toFixed(1)}%`).join(", ");
    // ΔNDVI: a faixa central transparente fica marcada com hachura sobre o gradiente
    const fade = key === "dndvi" && sc.fade
      ? `<div class="cb-fade" style="left:${pos(-sc.fade[0])};width:calc(${pos(sc.fade[0])} - ${pos(-sc.fade[0])})"></div>` : "";
    const marker = key === "magnitude"
      ? `<div class="cb-marker" style="left:${pos(sc.threshold)}" title="limiar"></div>` : "";
    const ticks = sc.ticks.map((t) =>
      `<span style="left:${pos(t)}">${Number(t).toLocaleString("pt-BR", { maximumFractionDigits: key === "dndvi" ? 1 : 3 })}</span>`).join("");
    return `<div class="colorbar"><div class="cb-title">${sc.label}</div>
      <div class="cb-bar" style="background:linear-gradient(to right, ${grad})">${fade}${marker}</div>
      <div class="cb-ticks">${ticks}</div><div class="cb-hint">${sc.hint || ""}</div></div>`;
  }

  // ---- clique no mapa: valores do pixel
  function enableProbe(a) {
    if (SismoS2.STATIC) return;   // a leitura do pixel precisa do servidor (bandas salvas)
    map.getContainer().classList.add("probe");
    const highlight = L.rectangle([[0, 0], [0, 0]], { color: "#fff", weight: 2, fill: false, interactive: false });
    map.on("click", async (e) => {
      const { lat, lng } = e.latlng;
      const popup = L.popup({ maxWidth: 280, className: "probe-popup" }).setLatLng(e.latlng)
        .setContent("<em>Consultando pixel…</em>").openOn(map);
      try {
        const res = await fetch(`/api/analises/${id}/pixel?lat=${lat.toFixed(6)}&lon=${lng.toFixed(6)}`);
        const p = await res.json();
        if (!res.ok) throw new Error(p.error || "Erro ao consultar o pixel");
        if (!p.inside) { popup.setContent("Fora da área analisada."); map.removeLayer(highlight); return; }
        // destaca o pixel (~10 m) consultado
        const d = (a.result.grid.resolution_m || 10) / 2;
        const dLat = d / 110540, dLng = d / (111320 * Math.cos((lat * Math.PI) / 180));
        highlight.setBounds([[lat - dLat, lng - dLng], [lat + dLat, lng + dLng]]).addTo(map);
        popup.setContent(probeHtml(p));
      } catch (err) {
        popup.setContent(`<span class="err">${err.message}</span>`);
      }
    });
    map.on("popupclose", () => map.removeLayer(highlight));
  }

  function probeHtml(p) {
    const n = (v, d = 3) => (v == null ? "—" : Number(v).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d }));
    const sign = (v) => (v == null ? "—" : (v > 0 ? "+" : "") + n(v));
    const above = p.magnitude != null && p.threshold != null && p.magnitude > p.threshold;
    return `<div class="probe">
      <div class="probe-class"><span class="swatch" style="background:${p.class_color}"></span><b>${p.class_name}</b></div>
      <table>
        <tr><th>NDVI antes</th><td>${n(p.ndvi_before)}</td></tr>
        <tr><th>NDVI depois</th><td>${n(p.ndvi_after)}</td></tr>
        <tr><th>ΔNDVI</th><td class="${p.dndvi < -0.15 ? "neg" : p.dndvi > 0.15 ? "pos" : ""}">${sign(p.dndvi)}</td></tr>
        <tr><th>Magnitude CVA</th><td>${n(p.magnitude, 4)} <small class="${above ? "neg" : ""}">${p.magnitude == null ? "" : above ? "acima do limiar" : "abaixo do limiar"} (${n(p.threshold, 4)})</small></td></tr>
      </table>
      ${p.slope_deg != null || p.urban != null ? `<table>
        ${p.slope_deg != null ? `<tr><th>Declividade</th><td>${n(p.slope_deg, 1)}°</td></tr>` : ""}
        ${p.urban != null ? `<tr><th>Área construída</th><td>${p.urban ? "sim (WorldCover 50)" : "não"}</td></tr>` : ""}
      </table>` : ""}
      ${p.valid ? "" : '<div class="warnline">Pixel com nuvem ou sem dado em alguma das datas</div>'}
      <div class="coords">${p.lat.toFixed(5)}, ${p.lon.toFixed(5)} · pixel (${p.row}, ${p.col})</div>
    </div>`;
  }

  // ---- comparação lado a lado com mapas sincronizados
  let compareReady = false;
  function setupCompare(bounds) {
    document.querySelectorAll(".view-toggle button").forEach((btn) =>
      btn.addEventListener("click", () => {
        const compare = btn.dataset.view === "compare";
        document.querySelectorAll(".view-toggle button").forEach((b) => b.classList.toggle("active", b === btn));
        $("map").hidden = compare;
        $("compare").hidden = !compare;
        if (compare && !compareReady) buildCompare(bounds);
        if (!compare) map.invalidateSize();
      }));
  }

  function buildCompare(bounds) {
    compareReady = true;
    const mk = (el, file) => {
      const m = L.map(el, { layers: [SismoS2.baseLayers()["Satélite (Esri)"]], zoomControl: false });
      L.imageOverlay(fileUrl(file), bounds, { className: "pixelated" }).addTo(m);
      L.rectangle(bounds, { color: "#ef4444", weight: 1, fill: false }).addTo(m);
      m.fitBounds(bounds);
      return m;
    };
    const a = mk("map-before", "before.png");
    const b = mk("map-after", "after.png");
    L.control.zoom({ position: "topright" }).addTo(b);
    let syncing = false;
    const sync = (src, dst) => src.on("move", () => {
      if (syncing) return;
      syncing = true;
      dst.setView(src.getCenter(), src.getZoom(), { animate: false });
      syncing = false;
    });
    sync(a, b);
    sync(b, a);
  }

  function showDelete() {
    const btn = $("delete-btn");
    // em caso de erro o botão fica fora da seção de resultados
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    const dialog = $("delete-dialog");
    btn.addEventListener("click", () => {
      $("delete-error").hidden = true;
      dialog.showModal();
      $("delete-cancel").focus(); // foco no botão seguro
    });
    $("delete-cancel").addEventListener("click", () => dialog.close());
    $("delete-confirm").addEventListener("click", async () => {
      const ok = $("delete-confirm");
      ok.disabled = true;
      ok.textContent = "Excluindo…";
      try {
        const res = await fetch(`/api/analises/${id}`, { method: "DELETE" });
        if (!res.ok) throw new Error((await res.json()).error || "Não foi possível excluir.");
        window.location.href = "/";
      } catch (e) {
        $("delete-error").textContent = e.message;
        $("delete-error").hidden = false;
        ok.disabled = false;
        ok.textContent = "Excluir definitivamente";
      }
    });
  }

  poll();
})();
