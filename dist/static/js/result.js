// Página de resultado: abas (com teclado e #âncora), painel flutuante do mapa e etapas do processamento.
(function () {
  const tabs = [...document.querySelectorAll('.tabs [role="tab"]')];
  const panels = [...document.querySelectorAll(".tab-panel")];

  function select(key, focus) {
    const tab = tabs.find((t) => t.dataset.tab === key);
    if (!tab) return;
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", on);
      t.tabIndex = on ? 0 : -1;
    });
    panels.forEach((p) => { p.hidden = p.dataset.panel !== key; });
    if (focus) tab.focus();
    tab.scrollIntoView({ block: "nearest", inline: "nearest" });
    if (key === "mapa" && window.SismoS2 && SismoS2.map) {
      setTimeout(() => SismoS2.map.invalidateSize(), 0);   // o mapa estava escondido: recalcula o tamanho
    }
    if (history.replaceState) history.replaceState(null, "", key === "mapa" ? location.pathname : `#${key}`);
  }

  tabs.forEach((t, i) => {
    t.addEventListener("click", () => select(t.dataset.tab));
    t.addEventListener("keydown", (e) => {   // setas, Home e End entre as abas
      const keys = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 };
      if (!(e.key in keys)) return;
      e.preventDefault();
      select(tabs[(keys[e.key] + tabs.length) % tabs.length].dataset.tab, true);
    });
  });
  document.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => select(b.dataset.goto, true)));
  // links como "Mostrar na aba Mapa" levam de volta ao mapa
  ["val-grid-toggle", "val-layer-toggle", "ctl-layer-toggle"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", (e) => { if (e.target.checked) setTimeout(() => select("mapa"), 250); });
  });
  // no celular as abas rolam para o lado: um degradê na borda avisa que há mais abas
  const strip = document.querySelector(".result-head .tabs");
  if (strip) {
    const edges = () => {
      const max = strip.scrollWidth - strip.clientWidth;
      strip.classList.toggle("more-left", strip.scrollLeft > 4);
      strip.classList.toggle("more-right", strip.scrollLeft < max - 4);
    };
    strip.addEventListener("scroll", edges, { passive: true });
    window.addEventListener("resize", edges);
    edges();
  }
  const initial = location.hash.slice(1);
  if (initial && tabs.some((t) => t.dataset.tab === initial)) setTimeout(() => select(initial), 0);

  // painel flutuante recolhível
  const fp = document.getElementById("float-panel"), fpBtn = document.getElementById("fp-toggle");
  if (fp && fpBtn) {
    const narrow = window.matchMedia("(max-width: 760px)").matches;
    const setOpen = (open) => {
      fp.classList.toggle("collapsed", !open);
      fpBtn.setAttribute("aria-expanded", open);
      fpBtn.textContent = open ? "Recolher" : "Mostrar";
    };
    setOpen(!narrow);
    fpBtn.addEventListener("click", () => setOpen(fp.classList.contains("collapsed")));
  }

  // etapas da tela de processamento acompanham a barra de progresso
  window.SismoS2 = window.SismoS2 || {};
  SismoS2.updateProcSteps = (progress) => {
    const items = [...document.querySelectorAll("#proc-steps li")];
    items.forEach((li, i) => {
      const start = Number(li.dataset.at), next = items[i + 1] ? Number(items[i + 1].dataset.at) : 101;
      li.classList.toggle("done", progress >= next);
      li.classList.toggle("now", progress >= start && progress < next);
    });
  };
})();
