// Página "Eventos": terremotos do USGS e ativações do Copernicus EMS no mapa + lista com filtros.
(function () {
  const $ = (id) => document.getElementById(id);
  const fmt = (n, d = 1) => (n == null ? "—" : Number(n).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const br = (iso) => (iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const base = SismoS2.baseLayers();
  const map = L.map("map", { layers: [base["OpenStreetMap"]], preferCanvas: true, worldCopyJump: true }).setView([20, 20], 2);
  L.control.layers(base).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);
  const quakeLayer = L.layerGroup().addTo(map);
  const emsLayer = L.layerGroup().addTo(map);

  // raio proporcional à energia aproximada (cresce ~2× a cada grau de magnitude)
  const radius = (m) => Math.max(3, 3 * Math.pow(2, (m || 4.5) - 4.5));
  const quakeColor = (m) => (m >= 7 ? "#b91c1c" : m >= 6 ? "#ef4444" : m >= 5.5 ? "#f97316" : "#facc15");

  $("ev-period").addEventListener("change", (e) => { $("ev-custom").hidden = e.target.value !== "custom"; });
  $("ev-mag").addEventListener("input", (e) => { $("ev-mag-label").textContent = e.target.value; });
  $("ev-apply").addEventListener("click", load);

  function query() {
    const p = new URLSearchParams({ mag_min: $("ev-mag").value });
    if ($("ev-period").value === "custom") {
      if (!$("ev-start").value || !$("ev-end").value) return null;
      p.set("inicio", $("ev-start").value);
      p.set("fim", $("ev-end").value);
    } else {
      p.set("periodo", $("ev-period").value);
    }
    return p;
  }

  function periodRange() {
    if ($("ev-period").value === "custom") return [$("ev-start").value, $("ev-end").value];
    const days = $("ev-period").value === "week" ? 7 : 30;
    const end = new Date();
    const start = new Date(end.getTime() - days * 86400000);
    return [start.toISOString().slice(0, 10), end.toISOString().slice(0, 10)];
  }

  async function load() {
    $("ev-error").hidden = true;
    const p = query();
    if (!p) { showError("Escolha as datas de início e fim."); return; }
    loadQuakes(p);
    loadEms();
  }

  function showError(msg) { $("ev-error").textContent = msg; $("ev-error").hidden = false; }

  async function loadQuakes(p) {
    $("ev-usgs-list").innerHTML = '<li class="muted small">Carregando…</li>';
    quakeLayer.clearLayers();
    try {
      const d = SismoS2.STATIC ? await usgsDirect(p) : await fetch(`/api/eventos/terremotos?${p}`).then(async (res) => {
        const j = await res.json();
        if (!res.ok) throw new Error(j.error);
        return j;
      });
      $("ev-usgs-count").textContent = `(${d.count})`;
      $("ev-usgs-source").textContent = `Fonte: ${d.source}`;
      $("ev-usgs-list").innerHTML = d.quakes.length ? "" : '<li class="muted small">Nenhum terremoto com esses filtros.</li>';
      d.quakes.forEach((q) => {
        const marker = L.circleMarker([q.lat, q.lon], {
          radius: radius(q.magnitude), color: "#111827", weight: 1, fillColor: quakeColor(q.magnitude), fillOpacity: 0.8,
        }).bindPopup(quakePopup(q)).addTo(quakeLayer);
        const li = document.createElement("li");
        li.innerHTML = `<b class="mag" style="background:${quakeColor(q.magnitude)}">${fmt(q.magnitude)}</b>
          <span><span class="title">${esc(q.place)}</span><small class="muted">${br(q.time)} · ${fmt(q.depth_km, 0)} km de profundidade</small></span>`;
        li.addEventListener("click", () => { map.setView([q.lat, q.lon], 8); marker.openPopup(); });
        $("ev-usgs-list").appendChild(li);
      });
    } catch (e) {
      $("ev-usgs-list").innerHTML = "";
      showError(e.message || "Erro ao carregar o USGS");
    }
  }

  // vitrine estática: o navegador consulta o USGS direto (o feed e a consulta FDSN liberam CORS)
  const USGS_FEED = { week: "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
                      month: "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_month.geojson" };
  async function usgsDirect(p) {
    const magMin = Number(p.get("mag_min") || 4.5);
    let url, source;
    if (p.get("inicio")) {
      const q = new URLSearchParams({ format: "geojson", starttime: p.get("inicio"), endtime: p.get("fim"),
        minmagnitude: Math.max(magMin, 2.5), orderby: "time", limit: 2000 });
      url = `https://earthquake.usgs.gov/fdsnws/event/1/query?${q}`;
      source = "USGS FDSN (consulta histórica, direto do navegador)";
    } else {
      url = USGS_FEED[p.get("periodo")] || USGS_FEED.week;
      source = `USGS feed 4.5+ (${p.get("periodo") === "month" ? "30" : "7"} dias, direto do navegador)`;
    }
    const res = await fetch(url);
    if (!res.ok) throw new Error(`USGS indisponível (HTTP ${res.status})`);
    const data = await res.json();
    const quakes = (data.features || []).filter((f) => {
      const pr = f.properties || {};
      return (pr.type == null || pr.type === "earthquake") && pr.mag != null && pr.mag >= magMin && pr.time != null;
    }).map((f) => {
      const pr = f.properties, c = f.geometry.coordinates;
      return { id: f.id, magnitude: pr.mag, place: pr.place || "", time: new Date(pr.time).toISOString().slice(0, 19) + "+00:00",
        depth_km: c[2], lon: c[0], lat: c[1], url: pr.url || "", title: pr.title || "", alert: pr.alert, tsunami: !!pr.tsunami };
    }).sort((a, b) => (a.time < b.time ? 1 : -1));
    return { source, count: quakes.length, quakes };
  }

  function quakePopup(q) {
    return `<div class="ev-popup"><b>M ${fmt(q.magnitude)}</b> – ${esc(q.place)}<br>
      ${br(q.time)} ${q.time.slice(11, 16)} UTC · profundidade ${fmt(q.depth_km, 0)} km
      ${q.tsunami ? "<br>⚠ alerta de tsunami" : ""}${q.alert ? `<br>PAGER: ${esc(q.alert)}` : ""}<br>
      <a href="${q.url}" target="_blank" rel="noopener">página no USGS ↗</a><br>
      ${SismoS2.STATIC || !q.analyze_url ? "" : `<a class="btn small primary-link" href="${q.analyze_url}">Analisar com Sentinel-2</a>
      <small class="muted">sem área oficial: usa ~10 km em volta do epicentro</small>`}</div>`;
  }

  async function loadEms() {
    emsLayer.clearLayers();
    $("ev-ems-list").innerHTML = '<li class="muted small">Carregando (primeira vez pode levar alguns segundos)…</li>';
    const p = new URLSearchParams();
    if (!$("ev-ems-all").checked) {
      const [s, e] = periodRange();
      p.set("inicio", s);
      p.set("fim", e);
    }
    try {
      const res = await fetch(SismoS2.STATIC ? SismoS2.api("/api/eventos/ems") : `/api/eventos/ems?${p}`);
      const d = await res.json();
      if (!res.ok) throw new Error(d.error);
      if (SismoS2.STATIC && p.get("inicio")) {
        d.activations = d.activations.filter((a) => (a.event_time || "").slice(0, 10) >= p.get("inicio")
                                                 && (a.event_time || "").slice(0, 10) <= p.get("fim"));
        d.count = d.activations.length;
      }
      if (SismoS2.STATIC && d.generated_at) d.note = `${d.note} Lista gerada em ${br(d.generated_at)}.`;
      $("ev-ems-count").textContent = `(${d.count})`;
      $("ev-ems-note").textContent = d.note + (d.errors.length ? ` Sem detalhe: ${d.errors.join("; ")}` : "");
      $("ev-ems-list").innerHTML = d.activations.length ? "" : '<li class="muted small">Nenhuma ativação no período.</li>';
      d.activations.forEach((a) => {
        const group = L.featureGroup().addTo(emsLayer);
        a.aois.forEach((aoi) => {
          L.geoJSON(aoi.geometry, { style: { color: aoi.has_grading ? "#7c3aed" : "#64748b", weight: 2, fillOpacity: 0.15 } })
            .bindPopup(aoiPopup(a, aoi)).addTo(group);
        });
        if (!a.aois.length && a.centroid) {
          L.marker([a.centroid[1], a.centroid[0]]).bindPopup(`<b>${a.code}</b> ${esc(a.name)}`).addTo(group);
        }
        const li = document.createElement("li");
        li.innerHTML = `<b class="badge-ems ${a.closed ? "closed" : "open"}">${a.status}</b>
          <span><span class="title">${a.code} – ${esc(a.name)}</span>
          <small class="muted">${br(a.event_time)} · ${esc(a.countries.join(", "))} · ${a.aois.length} AOIs
          (${a.aois.filter((x) => x.has_grading).length} com mapa de dano)</small></span>`;
        li.addEventListener("click", () => {
          if (group.getLayers().length) map.fitBounds(group.getBounds(), { padding: [30, 30], maxZoom: 12 });
        });
        $("ev-ems-list").appendChild(li);
      });
    } catch (e) {
      $("ev-ems-list").innerHTML = "";
      showError(e.message || "Erro ao carregar o Copernicus EMS");
    }
  }

  function aoiPopup(a, aoi) {
    const [w, s, e, n] = aoi.bbox;
    const big = e - w > window.MAX_BBOX_DEG || n - s > window.MAX_BBOX_DEG;
    return `<div class="ev-popup"><b>${a.code}</b> – ${esc(a.name)}<br>
      AOI ${aoi.number}: <b>${esc(aoi.name)}</b><br>
      Evento: ${br(a.event_time)} · ativação ${a.status}<br>
      ${aoi.has_grading ? `Mapa de dano (GRA) v${aoi.gra_version || 1} · ${esc(aoi.gra_image || "")}` : "Sem mapa de dano (GRA) nesta AOI"}<br>
      <a href="${a.url}" target="_blank" rel="noopener">página no Copernicus EMS ↗</a><br>
      ${SismoS2.STATIC ? "" : `<a class="btn small primary-link" href="${aoi.analyze_url}">Analisar com Sentinel-2</a>
      ${big ? `<small class="muted">AOI maior que ${window.MAX_BBOX_DEG}°: o retângulo será reduzido em volta do centro</small>` : ""}`}</div>`;
  }

  load();
})();
