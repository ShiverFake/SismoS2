// Utilidades compartilhadas pelas páginas (mapas base do Leaflet).
window.SismoS2 = {
  baseLayers() {
    return {
      "Satélite (Esri)": L.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        { maxZoom: 19, attribution: "Imagens &copy; Esri, Maxar, Earthstar Geographics" }
      ),
      "OpenStreetMap": L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19, attribution: "&copy; colaboradores do OpenStreetMap",
      }),
    };
  },
  bboxToBounds(b) {
    return L.latLngBounds([b[1], b[0]], [b[3], b[2]]);
  },

  // Vitrine estática (tools/build_vitrine.py): sem servidor Python, as rotas /api/... viram arquivos
  // .json e os links ficam relativos à página. Fora da vitrine, os três devolvem o caminho como está.
  STATIC: window.SISMOS2_STATIC || null,
  api(path) {   // dados: /api/analises/abc → <base>api/analises/abc.json (arquivos com extensão ficam iguais)
    if (!this.STATIC) return path;
    const clean = path.split("?")[0].replace(/^\//, "");
    const last = clean.split("/").pop();
    return this.STATIC.base + (last.includes(".") ? clean : `${clean}.json`);
  },
  file(path) {  // imagens e GeoJSON de /api/analises/<id>/arquivos/<nome> (mantém o ?t= de cache)
    return this.STATIC ? this.STATIC.base + path.replace(/^\//, "") : path;
  },
  page(path) {  // links de página: /analise/abc → <base>analise/abc/
    if (!this.STATIC || !path || !path.startsWith("/")) return path;
    const [p, hash] = path.split("#");
    const out = this.STATIC.base + p.replace(/^\//, "").replace(/\/?$/, "/");
    return hash ? `${out}#${hash}` : out;
  },
};
