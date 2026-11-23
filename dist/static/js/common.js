// Utilidades compartilhadas pelas páginas
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

  STATIC: window.SISMOS2_STATIC || null,
  api(path) {
    if (!this.STATIC) return path;
    const clean = path.split("?")[0].replace(/^\//, "");
    const last = clean.split("/").pop();
    return this.STATIC.base + (last.includes(".") ? clean : `${clean}.json`);
  },
  file(path) {
    return this.STATIC ? this.STATIC.base + path.replace(/^\//, "") : path;
  },
  page(path) {
    if (!this.STATIC || !path || !path.startsWith("/")) return path;
    const [p, hash] = path.split("#");
    const out = this.STATIC.base + p.replace(/^\//, "").replace(/\/?$/, "/");
    return hash ? `${out}#${hash}` : out;
  },
};
