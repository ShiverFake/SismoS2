// Camada "Prioridade de vistoria": células de 200 m ranqueadas pela % de mudança (ranking relativo).
(function () {
  const $ = (id) => document.getElementById(id);
  const id = window.ANALYSIS_ID;
  const toggle = $("priority-toggle");
  if (!toggle) return;
  const fmt = (n, d = 2) => (n == null ? "—" : Number(n).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d }));
  let data = null, layer = null;

  async function load() {
    if (data) return data;
    const res = await fetch(SismoS2.api(`/api/analises/${id}/prioridade`));
    const d = await res.json();
    if (!res.ok) throw new Error(d.error || "Não foi possível calcular a prioridade.");
    data = d;
    const colors = d.bands.reduce((acc, b) => { acc[b.key] = b.color; return acc; }, {});
    if (d.empty) colors[d.empty.key] = d.empty.color;
    $("priority-legend").innerHTML = d.bands.map((b) => `<li><span class="swatch" style="background:${b.color}"></span>
      ${b.label}: ${b.n_cells} células · ${fmt(b.area_km2)} km²</li>`).join("") +
      (d.empty && d.empty.n_cells ? `<li><span class="swatch" style="background:${d.empty.color};opacity:.45"></span>
      ${d.empty.label} (fora do ranking): ${d.empty.n_cells} células · ${fmt(d.empty.area_km2)} km²</li>` : "");
    const top = d.bands[0];
    if ($("kpi-priority-value")) $("kpi-priority-value").textContent = `${top.n_cells} células`;
    if ($("kpi-priority-hint")) $("kpi-priority-hint").textContent = `${fmt(d.top10_area_km2)} km², top 10% entre as células com construções`;
    if ($("kpi-ranked-value")) $("kpi-ranked-value").textContent = d.n_cells.toLocaleString("pt-BR");
    if ($("kpi-ranked-hint")) {
      const out = d.empty ? d.empty.n_cells : 0;
      $("kpi-ranked-hint").textContent = out ? `com construções; ${out.toLocaleString("pt-BR")} sem construções ficaram fora` : "células de 200 m com construções";
    }
    $("priority-msg").innerHTML = `As <b>10% células prioritárias somam ${fmt(d.top10_area_km2)} km²</b>
      (${top.n_cells} de ${d.n_cells} células; ${fmt(100 * d.top10_area_km2 / d.total_area_km2, 1)}% da área com dado).` +
      (d.empty && d.empty.n_cells ? ` ${d.empty.n_cells} células sem construções ficaram fora do ranking.` : "") +
      (d.n_water_cells ? ` ${d.n_water_cells} células com mais de 50% de água também.` : "");
    const gj = await fetch(SismoS2.file(`/api/analises/${id}/arquivos/${d.geojson}?t=${Date.now()}`)).then((r) => r.json());
    layer = L.geoJSON(gj, {
      style: (f) => {
        const b = f.properties.band;
        if (b === "empty") return { fillColor: colors.empty, fillOpacity: 0.12, color: colors.empty, weight: 0.3, opacity: 0.35 };
        return { fillColor: colors[b], fillOpacity: b === "rest" ? 0.08 : 0.5,
          color: b === "rest" ? "#64748b" : "#111827", weight: b === "rest" ? 0.3 : 0.8 };
      },
      onEachFeature: (f, l) => l.bindTooltip(f.properties.band === "empty"
        ? `Sem construções: menos de 10% de área construída ou nenhum prédio do OSM.<br>Fora do ranking de prioridade.`
        : `Posição ${f.properties.rank} de ${d.n_cells} · ${f.properties.band_label}<br>
        ${fmt(100 * f.properties.change, 1)}% dos pixels com mudança · ${fmt(f.properties.area_km2, 3)} km²<br>
        <i>ranking relativo, não diagnóstico de dano</i>`),
    });
    return d;
  }

  toggle.addEventListener("change", async () => {
    $("priority-legend").hidden = !toggle.checked;
    if (!toggle.checked) { if (layer) SismoS2.map.removeLayer(layer); return; }
    try {
      await load();
      layer.addTo(SismoS2.map);
    } catch (e) {
      $("priority-msg").textContent = e.message;
      toggle.checked = false;
    }
  });

  // camada padrão ao abrir o resultado: liga a prioridade assim que o mapa e os dados estiverem prontos
  let tries = 0;   // a análise pode ainda estar processando: tenta por até ~5 min
  const tryShow = () => load().then(() => {
    if (!SismoS2.map) throw new Error("mapa ainda não montado");
    if (!toggle.checked) { toggle.checked = true; toggle.dispatchEvent(new Event("change")); }
  }).catch(() => { if (++tries < 60) setTimeout(tryShow, tries < 5 ? 1000 : 5000); });
  setTimeout(tryShow, 1500);
})();
