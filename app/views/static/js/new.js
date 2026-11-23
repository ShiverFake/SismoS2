// Página "Nova análise"
(function () {
  const $ = (id) => document.getElementById(id);
  const qs = new URLSearchParams(window.location.search);
  const base = SismoS2.baseLayers();
  const map = L.map("map", { layers: [base["Satélite (Esri)"]] }).setView([25, 20], 2);
  L.control.layers(base).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);

  // ------------------------------------------------------------------ mapa e retângulo
  const drawn = new L.FeatureGroup().addTo(map);
  map.addControl(new L.Control.Draw({
    position: "topleft",
    draw: {
      polygon: false, polyline: false, circle: false, marker: false, circlemarker: false,
      rectangle: { shapeOptions: { color: "#ef4444", weight: 2, fillOpacity: 0.06 } },
    },
    edit: { featureGroup: drawn },
  }));
  const rectTool = new L.Draw.Rectangle(map, { shapeOptions: { color: "#ef4444", weight: 2, fillOpacity: 0.06 } });
  $("draw-btn").addEventListener("click", () => rectTool.enable());

  window.EARTHQUAKES.forEach((e) => {
    const c = SismoS2.bboxToBounds(e.bbox).getCenter();
    L.circleMarker(c, { radius: 7, color: "#fff", weight: 2, fillColor: "#ef4444", fillOpacity: 0.9 })
      .bindTooltip(`${e.name}<br>M${e.magnitude}, ${e.date}`)
      .on("click", () => applyPreset(e.id))
      .addTo(map);
  });

  const fields = ["west", "south", "east", "north"];
  function setRect(bounds, fit) {
    drawn.clearLayers();
    drawn.addLayer(L.rectangle(bounds, { color: "#ef4444", weight: 2, fillOpacity: 0.06 }));
    if (fit) map.fitBounds(bounds, { padding: [40, 40] });
  }
  function writeBbox(bounds) {
    $("west").value = bounds.getWest().toFixed(4);
    $("south").value = bounds.getSouth().toFixed(4);
    $("east").value = bounds.getEast().toFixed(4);
    $("north").value = bounds.getNorth().toFixed(4);
    updateSize();
  }
  function readBbox() {
    const v = fields.map((f) => parseFloat($(f).value));
    return v.every(Number.isFinite) ? v : null;
  }
  const fmt = (x, d = 1) => x.toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d });
  function bboxInfo(b) {
    const lat = ((b[1] + b[3]) / 2) * Math.PI / 180;
    const w = (b[2] - b[0]) * 111.32 * Math.cos(lat), h = (b[3] - b[1]) * 110.57;
    return { w, h, tooBig: b[2] - b[0] > MAX_BBOX_DEG || b[3] - b[1] > MAX_BBOX_DEG };
  }
  function updateSize() {
    const b = readBbox(), el = $("bbox-size");
    if (!b) { el.textContent = ""; return; }
    const i = bboxInfo(b);
    el.innerHTML = `Área escolhida: <b>${fmt(i.w)} × ${fmt(i.h)} km</b> (${fmt(i.w * i.h, 0)} km²)` +
      (i.tooBig ? `<br><span class="bad-text">Maior que ${fmt(MAX_BBOX_DEG)}° de lado: diminua o retângulo.</span>` : "");
  }
  function clearPreset() {
    $("preset").value = "";
    $("preset-desc").textContent = "";
    document.querySelectorAll(".preset").forEach((p) => p.setAttribute("aria-pressed", "false"));
  }
  map.on(L.Draw.Event.CREATED, (e) => {
    drawn.clearLayers();
    drawn.addLayer(e.layer);
    writeBbox(e.layer.getBounds());
    clearPreset();
  });
  map.on(L.Draw.Event.EDITED, (e) => e.layers.eachLayer((l) => writeBbox(l.getBounds())));
  fields.forEach((f) => $(f).addEventListener("change", () => {
    const b = readBbox();
    if (b && b[0] < b[2] && b[1] < b[3]) setRect(SismoS2.bboxToBounds(b), true);
    updateSize();
  }));

  function linkEms(code, aoi) {
    $("ems_activation").value = code || "";
    $("ems_aoi").value = aoi || "";
    $("ems-link").hidden = !code;
    if (code) {
      $("ems-link").innerHTML = `Ligada à ativação <b>${code}</b>${aoi ? ` (AOI ${aoi})` : ""} do Copernicus EMS: o resultado
        terá a camada de dano oficial e a validação já vem preenchida.`;
    }
  }

  // ------------------------------------------------------------------ exemplos
  function applyPreset(id) {
    const e = window.EARTHQUAKES.find((x) => x.id === id);
    if (!e) return;
    $("preset").value = e.id;
    document.querySelectorAll(".preset").forEach((p) => p.setAttribute("aria-pressed", String(p.dataset.id === id)));
    $("preset-desc").textContent = e.description;
    $("name").value = e.name;
    ["before_start", "before_end", "after_start", "after_end", "max_cloud"].forEach((k) => { $(k).value = e[k]; });
    $("anniversary").checked = false;
    syncAnniversary();
    linkEms(e.ems_activation, e.ems_aoi);
    const bounds = SismoS2.bboxToBounds(e.bbox);
    setRect(bounds, true);
    writeBbox(bounds);
  }
  document.querySelectorAll(".preset").forEach((p) => {
    p.setAttribute("aria-pressed", "false");
    p.addEventListener("click", () => applyPreset(p.dataset.id));
  });

  // ------------------------------------------------------------------ busca de cidade (Nominatim/OSM)
  async function searchCity() {
    const q = $("city-q").value.trim(), list = $("city-results"), msg = $("city-msg");
    if (q.length < 2) return;
    msg.textContent = "Buscando…";
    list.hidden = true;
    try {
      const url = `https://nominatim.openstreetmap.org/search?format=json&limit=5&accept-language=pt-BR&q=${encodeURIComponent(q)}`;
      const res = await fetch(url, { headers: { Accept: "application/json" } });
      const items = await res.json();
      if (!items.length) { msg.textContent = "Nenhum lugar encontrado com esse nome."; return; }
      msg.textContent = "Escolha um resultado. O retângulo fica centrado nele, com cerca de 8 km de lado.";
      list.innerHTML = "";
      items.forEach((it) => {
        const li = document.createElement("li"), b = document.createElement("button");
        b.type = "button";
        b.textContent = it.display_name;
        b.addEventListener("click", () => {
          const lat = Number(it.lat), lon = Number(it.lon);
          const dLat = 0.036, dLon = 0.036 / Math.cos(lat * Math.PI / 180);
          const bounds = L.latLngBounds([lat - dLat, lon - dLon], [lat + dLat, lon + dLon]);
          setRect(bounds, true);
          writeBbox(bounds);
          clearPreset();
          linkEms(null);
          if (!$("name").value) $("name").value = it.display_name.split(",")[0];
          list.hidden = true;
          msg.textContent = "Ajuste o retângulo no mapa (ícone de edição) se precisar.";
        });
        li.appendChild(b);
        list.appendChild(li);
      });
      list.hidden = false;
    } catch (e) {
      msg.textContent = "Não foi possível buscar agora. Desenhe o retângulo no mapa.";
    }
  }
  $("city-btn").addEventListener("click", searchCity);
  $("city-q").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); searchCity(); } });

  // ------------------------------------------------------------------ datas
  function minusOneYear(iso) {
    if (!iso) return "";
    const [y, m, d] = iso.split("-").map(Number);
    const day = m === 2 && d === 29 ? 28 : d;
    return `${y - 1}-${String(m).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  }
  function syncAnniversary() {
    const on = $("anniversary").checked;
    ["before_start", "before_end"].forEach((k) => { $(k).readOnly = on; $(k).classList.toggle("locked", on); });
    if (on) {
      $("before_start").value = minusOneYear($("after_start").value);
      $("before_end").value = minusOneYear($("after_end").value);
    }
    dateWarnings();
  }
  $("anniversary").addEventListener("change", syncAnniversary);
  ["after_start", "after_end"].forEach((k) => $(k).addEventListener("change", syncAnniversary));
  ["before_start", "before_end"].forEach((k) => $(k).addEventListener("change", dateWarnings));

  const today = new Date().toISOString().slice(0, 10);
  const br = (iso) => iso ? iso.split("-").reverse().join("/") : "";
  function dateWarnings() {
    const out = [];
    const v = (k) => $(k).value;
    if (v("after_start") > today) {
      out.push(["error", `O período depois começa em ${br(v("after_start"))}, uma data que ainda não chegou: ainda não
        existem imagens do Sentinel-2 desse período. Escolha um início até hoje (${br(today)}).`]);
    } else if (v("after_end") > today) {
      out.push(["warn", `A data final (${br(v("after_end"))}) está no futuro. O Sentinel-2 passa a cada 5 dias: só entram as
        imagens que já existem até hoje.`]);
    }
    if (v("before_end") && v("after_start") && v("before_end") >= v("after_start")) {
      out.push(["error", "O período antes precisa terminar antes do período depois começar."]);
    }
    if (v("after_start") && v("after_end") && v("after_start") > v("after_end")) {
      out.push(["error", "No período depois, a data inicial vem depois da final."]);
    }
    if (v("before_start") && v("before_end") && v("before_start") > v("before_end")) {
      out.push(["error", "No período antes, a data inicial vem depois da final."]);
    }
    $("date-warnings").innerHTML = out.map(([k, t]) => `<div class="alert ${k}">${t}</div>`).join("");
    return out.some(([k]) => k === "error");
  }

  // ------------------------------------------------------------------ etapas
  let step = 1;
  const steps = [...document.querySelectorAll(".wizard-step")];
  function check(n) {
    if (n === 1) {
      const b = readBbox();
      if (!b || !(b[0] < b[2] && b[1] < b[3])) return "Escolha um exemplo, busque uma cidade ou desenhe um retângulo.";
      if (bboxInfo(b).tooBig) return "O retângulo está grande demais.";
    }
    if (n === 2) {
      if (["before_start", "before_end", "after_start", "after_end"].some((k) => !$(k).value)) return "Preencha as quatro datas.";
      if (dateWarnings()) return "Corrija as datas.";
    }
    if (n === 3 && !$("name").value.trim()) return "Dê um nome à análise.";
    return "";
  }
  function go(n) {
    step = n;
    steps.forEach((s) => { s.hidden = Number(s.dataset.step) !== n; });
    document.querySelectorAll("#wizard-steps li").forEach((li) => {
      const k = Number(li.dataset.step);
      li.classList.toggle("done", k < n);
      li.classList.toggle("now", k === n);
      if (k === n) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
    $("step-count").textContent = `Etapa ${n} de 4`;
    $("prev-btn").hidden = n === 1;
    $("next-btn").hidden = n === 4;
    $("submit-btn").hidden = n !== 4;
    $("step-error").textContent = "";
    if (n === 4) review();
    const h = steps[n - 1].querySelector("h2");
    h.tabIndex = -1;
    h.focus({ preventScroll: true });
    steps[n - 1].scrollTop = 0;
  }
  $("next-btn").addEventListener("click", () => {
    const err = check(step);
    $("step-error").textContent = err;
    if (!err) go(step + 1);
  });
  $("prev-btn").addEventListener("click", () => go(step - 1));
  document.querySelectorAll("#wizard-steps li").forEach((li) => li.addEventListener("click", () => {
    const k = Number(li.dataset.step);
    for (let i = 1; i < k; i++) if (check(i)) return;
    go(k);
  }));

  function review() {
    const b = readBbox(), i = bboxInfo(b);
    const rows = [
      ["Nome", $("name").value],
      ["Área", `${fmt(i.w)} × ${fmt(i.h)} km (${fmt(i.w * i.h, 0)} km²)`],
      ["Antes", `${br($("before_start").value)} a ${br($("before_end").value)}${$("anniversary").checked ? " (par de aniversário)" : ""}`],
      ["Depois", `${br($("after_start").value)} a ${br($("after_end").value)}`],
      ["Cenas", $("median_composite").checked ? "Mediana de até 3 cenas" : "Melhor cena de cada período"],
      ["Relevo", $("topo_correction").checked ? "Com correção (Copernicus DEM)" : "Sem correção"],
      ["Limiar", $("threshold_method").value === "otsu" ? "Otsu" : `Média + ${fmt(Number($("k").value))} × desvio`],
      ["Dano oficial", $("ems_activation").value ? `Copernicus EMS ${$("ems_activation").value}` : "Nenhum ligado"],
    ];
    $("review").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;")}</dd>`).join("");
  }

  // ------------------------------------------------------------------ envio
  $("analysis-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    for (let i = 1; i <= 3; i++) { const e = check(i); if (e) { go(i); $("step-error").textContent = e; return; } }
    const err = $("form-error");
    err.hidden = true;
    const payload = {
      name: $("name").value.trim(),
      bbox: readBbox(),
      event_id: $("preset").value || null,
      before_start: $("before_start").value, before_end: $("before_end").value,
      after_start: $("after_start").value, after_end: $("after_end").value,
      max_cloud: Number($("max_cloud").value),
      threshold_method: $("threshold_method").value,
      k: Number($("k").value),
      min_region_px: Number($("min_region_px").value),
      median_composite: $("median_composite").checked,
      topo_correction: $("topo_correction").checked,
      ems_activation: $("ems_activation").value || null,
      ems_aoi: $("ems_aoi").value || null,
    };
    const btn = $("submit-btn");
    btn.disabled = true;
    btn.lastElementChild.textContent = "Enviando…";
    try {
      const res = await fetch("/api/analises", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Não foi possível criar a análise.");
      window.location.href = data.url;
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
      btn.disabled = false;
      btn.lastElementChild.textContent = "Processar imagens";
    }
  });

  // ------------------------------------------------------------------ pré-preenchimento
  if (qs.get("bbox")) {
    const b = qs.get("bbox").split(",").map(Number);
    if (b.length === 4 && b.every(Number.isFinite)) {
      const bounds = SismoS2.bboxToBounds(b);
      setRect(bounds, true);
      writeBbox(bounds);
    }
    if (qs.get("nome")) $("name").value = qs.get("nome");
    ["before_start", "before_end", "after_start", "after_end"].forEach((k) => { if (qs.get(k)) $(k).value = qs.get(k); });
    linkEms(qs.get("ems"), qs.get("aoi"));
    dateWarnings();
  }
  const redo = qs.get("refazer");
  if (redo) {
    fetch(`/api/analises/${encodeURIComponent(redo)}`).then((r) => r.json()).then((a) => {
      if (!a.bbox) return;
      const anniv = qs.get("aniversario") === "1";
      $("name").value = anniv ? `${a.name} (par de aniversário)` : a.name;
      ["before_start", "before_end", "after_start", "after_end", "max_cloud", "threshold_method", "k", "min_region_px"]
        .forEach((k) => { if (a[k] !== undefined && a[k] !== null) $(k).value = a[k]; });
      $("median_composite").checked = !!a.median_composite;
      $("topo_correction").checked = a.topo_correction !== false;
      $("anniversary").checked = anniv;
      if (a.event_id && window.EARTHQUAKES.some((e) => e.id === a.event_id)) {
        $("preset").value = a.event_id;
        document.querySelectorAll(".preset").forEach((p) => p.setAttribute("aria-pressed", String(p.dataset.id === a.event_id)));
      }
      linkEms(a.ems_activation, a.ems_aoi);
      syncAnniversary();
      const bounds = SismoS2.bboxToBounds(a.bbox);
      setRect(bounds, true);
      writeBbox(bounds);
      go(4);
    });
  }
})();
