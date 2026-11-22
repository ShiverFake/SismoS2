// Camada "Dano oficial (Copernicus EMS)": prédios do produto de grading coloridos pelo grau de dano.
(function () {
  const ems = window.ANALYSIS_EMS;
  const toggle = document.getElementById("official-toggle");
  if (!ems || !toggle) return;
  const msg = document.getElementById("official-msg");
  const legend = document.getElementById("official-legend");
  const COLORS = { destroyed: "#7f1d1d", damaged: "#f97316", possibly: "#facc15", none: "#94a3b8" };
  const LABELS = { destroyed: "Destruído", damaged: "Danificado", possibly: "Possivelmente danificado", none: "Sem dano" };
  let layer = null;

  if (!ems.aoi) {
    toggle.disabled = true;
    msg.textContent = `Ativação ${ems.code} sem AOI definida: escolha a AOI na validação (abaixo).`;
    return;
  }

  toggle.addEventListener("change", async () => {
    const map = SismoS2.map;
    legend.hidden = !toggle.checked;
    if (!toggle.checked) { if (layer) map.removeLayer(layer); return; }
    if (!layer) {
      msg.textContent = "Baixando o produto de grading do Copernicus EMS…";
      try {
        const res = await fetch(SismoS2.api(`/api/eventos/ems/${ems.code}/aoi/${ems.aoi}/grading.geojson`));
        const gj = await res.json();
        if (!res.ok) throw new Error(gj.error || "Produto indisponível.");
        const renderer = L.canvas({ padding: 0.3 });
        const style = (f) => ({ color: "#0f172a", weight: 0.8, fillColor: COLORS[f.properties.grade], fillOpacity: 0.9 });
        layer = L.geoJSON(gj, {
          style,
          pointToLayer: (f, latlng) => L.circleMarker(latlng, Object.assign({ renderer, radius: 4 }, style(f))),
          onEachFeature: (f, l) => l.bindTooltip(`Copernicus EMS: ${LABELS[f.properties.grade]}`),
        });
        const c = gj.properties.counts;
        msg.textContent = `${gj.properties.source}: ${c.destroyed} destruídos, ${c.damaged} danificados, ` +
          `${c.possibly} possivelmente danificados.`;
      } catch (e) {
        msg.textContent = e.message;
        toggle.checked = false;
        legend.hidden = true;
        return;
      }
    }
    layer.addTo(SismoS2.map);
  });
})();
