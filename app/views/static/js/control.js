// Áreas de controle
(function () {
  const $ = (id) => document.getElementById(id);
  const id = window.ANALYSIS_ID;
  const api = `/api/analises/${id}/controle`;
  const fmt = (n, d = 2) => (n == null ? "—" : Number(n).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const br = (iso) => (iso ? iso.split("-").reverse().join("/") : "—");
  let overlay = null;

  function renderYearChoices(years) {
    $("ctl-years").innerHTML = years.map((y) =>
      `<label class="check"><input type="checkbox" value="${y}" checked> ${y}</label>`).join("");
  }

  $("ctl-btn").addEventListener("click", async () => {
    $("ctl-error").hidden = true;
    $("ctl-btn").disabled = true;
    try {
      const res = await fetch(api, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refazer: $("ctl-redo").checked }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Não foi possível iniciar os controles.");
      poll();
    } catch (e) {
      $("ctl-error").textContent = e.message;
      $("ctl-error").hidden = false;
    } finally {
      $("ctl-btn").disabled = false;
    }
  });

  function poll() {
    fetch(SismoS2.api(api)).then((r) => r.json()).then((c) => {
      if (c.status === "is_control") {
        $("ctl-start").hidden = true;
        $("ctl-is-control").hidden = false;
        $("ctl-is-control").innerHTML = `Esta é uma <b>análise de controle${c.year ? ` (${c.year})` : ""}</b> de
          <a href="${SismoS2.page(c.event_url)}">outra análise</a>. Lá aparece a comparação evento × controles.` +
          (c.excluded ? `<div class="ctl-excluded-note">Fora da média dos controles desde ${br(c.excluded.at.slice(0, 10))}.
            Motivo: <i>${escapeHtml(c.excluded.reason)}</i></div>` : "");
        return;
      }
      if (c.status === "none") { renderYearChoices(c.default_years || []); return; }
      $("ctl-progress").hidden = false;
      const statusById = {};
      ((c.comparison && c.comparison.controls_status) || []).forEach((s) => { statusById[s.id] = s; });
      $("ctl-progress").innerHTML = c.controls.map((k) => {
        const st = statusById[k.id] || {};
        const flags = st.flags || [];
        const excluded = k.excluded;
        return `
        <div class="ctl-item ${excluded ? "is-excluded" : ""}" data-id="${k.id}">
          <a href="${SismoS2.page(k.url)}">Controle ${k.year || ""}</a>
          <span class="badge ${k.status}">${{ done: "concluído", error: "erro", processing: "processando", pending: "na fila" }[k.status]}</span>
          ${excluded ? '<span class="badge excluded">fora da média</span>' : ""}
          ${k.status === "done" ? `<small class="muted">${br(k.before[0])} → ${br(k.after[0])}${st.noise_median != null ? ` · ruído (magnitude mediana) ${fmt(st.noise_median, 3)}` : ""}</small>` : ""}
          ${k.status === "processing" || k.status === "pending"
            ? `<div class="progress"><div class="ctl-bar" style="width:${k.progress}%"></div></div><small class="muted">${k.message}</small>` : ""}
          ${k.status === "error" ? `<small class="err">${k.error}</small>` : ""}
          ${flags.map((f) => `<small class="ctl-flag">⚠ ${f.message}</small>`).join("")}
          ${excluded ? `<small class="ctl-reason">Motivo: <i>${escapeHtml(excluded.reason)}</i> (${br(excluded.at.slice(0, 10))})</small>` : ""}
          ${k.status === "done" ? (excluded
            ? `<button type="button" class="btn small ctl-include">Voltar para a média</button>`
            : `<button type="button" class="btn small ctl-exclude ${flags.length ? "warn" : ""}">Tirar da média…</button>`) : ""}
          <div class="ctl-exclude-form" hidden>
            <label>Motivo (fica registrado)
              <input type="text" maxlength="300" value="${escapeHtml(flags.map((f) => f.message).join("; "))}">
            </label>
            <div class="dialog-actions">
              <button type="button" class="btn small ctl-cancel">Cancelar</button>
              <button type="button" class="btn small danger-solid ctl-confirm">Tirar da média</button>
            </div>
            <small class="err" hidden></small>
          </div>
        </div>`;
      }).join("");
      bindControlButtons(statusById);
      const years = new Set(c.controls.map((k) => k.year));
      $("ctl-start").hidden = !c.controls.some((k) => k.status === "error");
      if (c.comparison) render(c);
      if (c.comparison_error) { $("ctl-error").textContent = c.comparison_error; $("ctl-error").hidden = false; }
      if (c.status === "running") setTimeout(poll, 3000);
      else if (!years.size) renderYearChoices(c.default_years || []);
    }).catch(() => setTimeout(poll, 5000));
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  // botões "Tirar da média…" / "Voltar para a média" de cada controle
  function bindControlButtons(statusById) {
    document.querySelectorAll("#ctl-progress .ctl-item").forEach((item) => {
      const cid = item.dataset.id;
      const form = item.querySelector(".ctl-exclude-form");
      const err = form.querySelector(".err");
      const ex = item.querySelector(".ctl-exclude");
      const inc = item.querySelector(".ctl-include");
      if (ex) ex.addEventListener("click", () => { form.hidden = false; form.querySelector("input").focus(); });
      form.querySelector(".ctl-cancel").addEventListener("click", () => { form.hidden = true; });
      form.querySelector(".ctl-confirm").addEventListener("click", async () => {
        const reason = form.querySelector("input").value.trim();
        const flags = (statusById[cid] && statusById[cid].flags) || [];
        await send(`${api}/${cid}/excluir`, { reason, flags }, err);
      });
      if (inc) inc.addEventListener("click", () => send(`${api}/${cid}/incluir`, {}, err));
    });
  }

  async function send(url, body, errEl) {
    errEl.hidden = true;
    try {
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Não foi possível salvar.");
      poll();
    } catch (e) {
      errEl.textContent = e.message;
      errEl.hidden = false;
    }
  }

  function render(c) {
    const k = c.comparison, ag = k.aggregates, ev = k.event;
    $("ctl-results").hidden = false;
    const zTxt = (a) => (a && a.z != null ? `z = ${fmt(a.z, 1)}` : "z: precisa de ≥ 2 controles");
    const card = (title, a, cls) => `<div class="ctl-card ${cls}">
        <span>${title}</span><strong>${fmt(a.event, 2)}%</strong>
        <small>anos normais: ${fmt(a.controls_mean, 2)}% ± ${fmt(a.controls_std, 2)} · ${zTxt(a)}</small></div>`;
    $("ctl-cards").innerHTML =
      (ag.common_urban_percent ? card("% urbana acima do ruído do ano normal", ag.common_urban_percent, "event") : "") +
      card("% total acima do ruído do ano normal", ag.common_total_percent, "control");
    $("ctl-threshold-note").innerHTML = `Limiar comum (ruído do ano normal) = média dos limiares dos controles =
      <b>${fmt(k.common_threshold, 4)}</b> (limiar próprio do evento: ${fmt(ev.threshold, 4)}). Área comum, válida em
      todas as análises: ${fmt(k.common_valid_km2, 1)} km²${k.urban_common_km2 != null ? `, dos quais ${fmt(k.urban_common_km2, 1)} km² urbanos` : ""}.
      Intervalo do evento: ${c.event_gap_days} dias.
      <b>Média, desvio e z com ${k.n_included} de ${k.n_done} controles concluídos</b>${k.n_included < k.n_done ? " (os demais estão fora da média)" : ""}.`;

    const hasUrban = ev.own_urban_percent != null;
    const head = `<thead><tr><th>Análise</th><th>Par de cenas</th><th>Dias</th><th>Limiar</th>
      <th>Total próprio</th><th>Total comum</th>${hasUrban ? "<th>Urbana própria</th><th>Urbana comum</th>" : ""}
      <th>Evento c/ este limiar</th></tr></thead>`;
    const row = (r, isEvent) => `<tr class="${isEvent ? "current" : ""}"><td>${r.label}</td>
      <td>${r.dates ? `${br(r.dates[0])} → ${br(r.dates[1])}` : ""}</td><td>${r.gap_days != null ? r.gap_days : ""}</td>
      <td>${fmt(r.threshold, 4)}</td><td>${fmt(r.own_total_percent)}%</td><td>${fmt(r.common_total_percent)}%</td>
      ${hasUrban ? `<td>${fmt(r.own_urban_percent)}%</td><td>${fmt(r.common_urban_percent)}%</td>` : ""}
      <td>${isEvent ? "" : `${fmt(r.event_at_this_threshold_total_percent)}%${hasUrban ? ` · urb ${fmt(r.event_at_this_threshold_urban_percent)}%` : ""}`}</td></tr>`;
    const agg = (label, key) => (ag[key] ? `${fmt(ag[key].controls_mean)} ± ${fmt(ag[key].controls_std)}` : "—");
    const zcell = (key) => (ag[key] && ag[key].z != null ? fmt(ag[key].z, 1) : "—");
    $("ctl-table").innerHTML = head + "<tbody>" + row(ev, true) + k.controls.map((r) => row(r, false)).join("") +
      `<tr class="agg"><td>Controles: média ± dp</td><td></td><td></td><td></td>
        <td>${agg("", "own_total_percent")}</td><td>${agg("", "common_total_percent")}</td>
        ${hasUrban ? `<td>${agg("", "own_urban_percent")}</td><td>${agg("", "common_urban_percent")}</td>` : ""}<td></td></tr>
       <tr class="agg"><td>z do evento</td><td></td><td></td><td></td>
        <td>${zcell("own_total_percent")}</td><td>${zcell("common_total_percent")}</td>
        ${hasUrban ? `<td>${zcell("own_urban_percent")}</td><td>${zcell("common_urban_percent")}</td>` : ""}<td></td></tr></tbody>`;

    if (hasUrban) {
      const m = (r) => r.urban_magnitude || {};
      const mrow = (r) => `<tr><td>${r.label}</td><td>${fmt(m(r).mean, 4)}</td><td>${fmt(m(r).median, 4)}</td>
        <td>${fmt(m(r).p90, 4)}</td><td>${fmt(m(r).p95, 4)}</td></tr>`;
      const zq = (q) => (ag[`urban_magnitude_${q}`] && ag[`urban_magnitude_${q}`].z != null ? fmt(ag[`urban_magnitude_${q}`].z, 1) : "—");
      $("ctl-magnitude").innerHTML = `<thead><tr><th>Análise</th><th>Média</th><th>Mediana</th><th>p90</th><th>p95</th></tr></thead>
        <tbody>${mrow(ev)}${k.controls.map(mrow).join("")}
        <tr class="agg"><td>z do evento</td><td></td><td>${zq("median")}</td><td>${zq("p90")}</td><td>${zq("p95")}</td></tr></tbody>`;
    }
    $("ctl-summary").innerHTML = `<dt>Mudou no evento e em algum controle</dt>
      <dd>${fmt(k.both_changed_km2, 3)} km² (${fmt(k.both_percent_of_event_change, 1)}% da mudança do evento)</dd>`;
  }

  $("ctl-layer-toggle").addEventListener("change", async (e) => {
    const map = SismoS2.map;
    if (!e.target.checked) { if (overlay) map.removeLayer(overlay); return; }
    if (!overlay) {
      const a = await fetch(SismoS2.api(`/api/analises/${id}`)).then((r) => r.json());
      overlay = L.imageOverlay(SismoS2.file(`/api/analises/${id}/arquivos/control_overlap.png?t=${Date.now()}`),
        L.latLngBounds(a.result.bounds), { className: "pixelated", zIndex: 4 });
    }
    overlay.addTo(map);
  });

  poll();
})();
