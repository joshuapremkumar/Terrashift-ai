/* TerraShift AI — dashboard logic (no build step, no framework). */

const state = {
  config: null,
  crops: [],
  cropMap: {},
  demoFarms: [],
  farms: [],
  farm: null,
  analysis: null,
  rotations: null,
  satellite: null,
  weather: null,
  report: null,
  map: null,
  marker: null,
  tileLayers: {},
  busy: 0,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* ------------------------------------------------------------------ utils */

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

const num = (value, digits = 0, fallback = "—") => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return fallback;
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
};

const pct = (value, digits = 0) => (value === null || value === undefined ? "—" : `${num(value, digits)}%`);

const cropName = (id) => state.cropMap[id]?.name || String(id || "").replace(/_/g, " ");

const severityClass = (severity) => (["high", "medium", "low"].includes(severity) ? severity : "low");

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : body.detail?.message || JSON.stringify(body.detail || body);
    } catch (_) { /* keep status text */ }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function showBanner(message, type = "info", sticky = false) {
  const banner = $("#banner");
  banner.textContent = message;
  banner.className = `banner ${type}`;
  if (!sticky) setTimeout(() => banner.classList.add("hidden"), 8000);
}

function hideBanner() { $("#banner").classList.add("hidden"); }

function setBusy(on, label = "Working…") {
  state.busy += on ? 1 : -1;
  state.busy = Math.max(0, state.busy);
  let overlay = $("#loadingOverlay");
  if (state.busy > 0) {
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "loadingOverlay";
      overlay.className = "loading-overlay";
      overlay.innerHTML = `<div class="loading-box"><span class="spinner"></span><span id="loadingLabel"></span></div>`;
      document.body.appendChild(overlay);
    }
    $("#loadingLabel").textContent = label;
  } else if (overlay) {
    overlay.remove();
  }
}

function emptyState(message) {
  return `<p class="muted">${esc(message)}</p>`;
}

/* --------------------------------------------------------------- bootstraps */

async function init() {
  bindTabs();
  bindForm();
  bindSimulation();
  bindReport();

  $("#newFarmBtn").addEventListener("click", newFarm);
  $("#reloadBtn").addEventListener("click", () => state.farm && loadFarmData());
  $("#farmSelect").addEventListener("change", (event) => event.target.value && selectFarm(event.target.value));
  $$("[data-goto]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.goto)));

  setBusy(true, "Connecting to TerraShift AI…");
  try {
    const [config, crops, demos, farms] = await Promise.all([
      api("/api/config"),
      api("/api/crops"),
      api("/api/demo-farms"),
      api("/api/farms"),
    ]);
    state.config = config;
    state.crops = crops.crops;
    crops.crops.forEach((crop) => { state.cropMap[crop.id] = crop; });
    state.demoFarms = demos.farms;
    state.farms = farms;

    renderDemoCards();
    renderCropCatalogue();
    populateCropSelect();
    renderFarmSelect();
    renderProviderChips();

    if (state.farms.length) {
      await selectFarm(state.farms[0].id, { silent: true });
    } else {
      showOnboarding(true);
    }
  } catch (error) {
    showBanner(`Could not reach the backend: ${error.message}`, "error", true);
  } finally {
    setBusy(false);
  }
}

/* ------------------------------------------------------------------ tabs */

function bindTabs() {
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
}

function switchView(view) {
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
  $$(".view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
  if (view === "map" && state.map) setTimeout(() => state.map.invalidateSize(), 60);
}

/* ------------------------------------------------------------------ farms */

function renderFarmSelect() {
  const select = $("#farmSelect");
  const options = ['<option value="">No farm selected</option>'];
  state.farms.forEach((farm) => {
    const label = `${farm.name}${farm.region ? ` · ${farm.region}` : ""}`;
    options.push(`<option value="${esc(farm.id)}"${state.farm?.id === farm.id ? " selected" : ""}>${esc(label)}</option>`);
  });
  select.innerHTML = options.join("");
}

function showOnboarding(show) {
  $("#onboard").classList.toggle("hidden", !show);
  $("#mapWrap").style.display = show ? "none" : "";
}

async function selectFarm(farmId, options = {}) {
  const farm = state.farms.find((item) => item.id === farmId);
  if (!farm) return;
  state.farm = farm;
  state.report = null;
  renderFarmSelect();
  fillForm(farm);
  $("#printReportLink").href = `/api/farms/${farm.id}/report`;
  showOnboarding(false);
  await loadFarmData(options);
}

async function loadFarmData(options = {}) {
  if (!state.farm) return;
  setBusy(true, "Reading NASA history and running the rotation engine…");
  try {
    const farmId = state.farm.id;
    const [rotations, satellite, weather] = await Promise.allSettled([
      api(`/api/farms/${farmId}/rotations`, { method: "POST", body: JSON.stringify({}) }),
      api(`/api/farms/${farmId}/satellite`),
      api(`/api/farms/${farmId}/weather`),
    ]);

    if (rotations.status === "rejected") throw new Error(rotations.reason.message);
    state.rotations = rotations.value;
    state.analysis = rotations.value.analysis_summary;
    state.satellite = satellite.status === "fulfilled" ? satellite.value : null;
    state.weather = weather.status === "fulfilled" ? weather.value : null;

    if (state.analysis?.synthetic) {
      showBanner("NASA POWER was unreachable, so this session uses the labelled synthetic fallback. Do not present it as observation.", "error", true);
    } else {
      hideBanner();
    }

    renderAll();
    if (!options.silent) switchView("rotations");
  } catch (error) {
    showBanner(`Analysis failed: ${error.message}`, "error", true);
  } finally {
    setBusy(false);
  }
}

async function newFarm() {
  state.farm = null;
  renderFarmSelect();
  $("#farmForm").reset();
  $("#profileMode").textContent = "new farm";
  $("#printReportLink").href = "#";
  showOnboarding(true);
  switchView("profile");
  $("#farmForm").elements.name.focus();
}

/* --------------------------------------------------------------- demo cards */

function renderDemoCards() {
  const grid = $("#demoGrid");
  grid.innerHTML = state.demoFarms
    .map(
      (farm) => `
      <button class="demo-card" type="button" data-demo="${esc(farm.key)}">
        <h4>${esc(farm.region || farm.country || "Demo")}</h4>
        <strong>${esc(farm.name)}</strong>
        <span>${esc(farm.description)}</span>
        <div class="meta">${esc(farm.irrigation)} · ${esc(farm.soil_texture)} · ${esc(cropName(farm.current_crop))} · priority ${esc(farm.priority)}</div>
      </button>`
    )
    .join("");

  $$("[data-demo]").forEach((button) =>
    button.addEventListener("click", async () => {
      const demo = state.demoFarms.find((farm) => farm.key === button.dataset.demo);
      if (!demo) return;
      setBusy(true, "Creating farm profile…");
      try {
        const { key, description, ...payload } = demo;
        const created = await api("/api/farms", { method: "POST", body: JSON.stringify(payload) });
        state.farms.unshift(created);
        await selectFarm(created.id);
      } catch (error) {
        showBanner(`Could not create the demo farm: ${error.message}`, "error", true);
      } finally {
        setBusy(false);
      }
    })
  );
}

/* ------------------------------------------------------------- provider chips */

function renderProviderChips() {
  if (!state.config) return;
  const providers = state.config.providers || {};
  const chips = [
    ["NASA POWER", providers.nasa_power?.enabled, "observed"],
    ["NASA GIBS", providers.nasa_gibs?.enabled, "imagery"],
    ["Meteomatics", providers.meteomatics?.enabled, "forecast"],
    ["Earthdata NDVI", providers.earthdata_ndvi?.enabled, "statistics"],
    ["Tavily", providers.tavily?.enabled, "research"],
    ["Vultr inference", providers.vultr_inference?.enabled, "explanation"],
  ];
  $("#mapSourceChips").innerHTML = chips
    .map(
      ([name, enabled, role]) =>
        `<span class="chip" title="${esc(role)}"><strong>${esc(name)}</strong> ${enabled ? "live" : "not configured"}</span>`
    )
    .join("");
}

/* --------------------------------------------------------------- map & GIS */

function renderMap() {
  if (!state.farm) return;
  const { latitude, longitude } = state.farm;

  if (!state.map) {
    state.map = L.map("map", { zoomControl: true, attributionControl: true }).setView([latitude, longitude], 11);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
      maxZoom: 18,
      attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    }).addTo(state.map);
  }
  state.map.setView([latitude, longitude], 11);

  if (state.marker) state.marker.remove();
  state.marker = L.circleMarker([latitude, longitude], {
    radius: 8, color: "#145c39", weight: 2, fillColor: "#1c7a4d", fillOpacity: 0.9,
  }).addTo(state.map).bindPopup(
    `<strong>${esc(state.farm.name)}</strong><br/>${num(latitude, 3)}, ${num(longitude, 3)}<br/>${esc(state.farm.soil_texture)} soil · ${esc(state.farm.irrigation)}`
  );

  const layers = state.satellite?.gibs?.layers || [];
  Object.values(state.tileLayers).forEach((layer) => layer.remove());
  state.tileLayers = {};

  const toggles = layers.map((layer, index) => {
    const tile = L.tileLayer(layer.tile_url_template, {
      maxZoom: layer.max_zoom || 9,
      opacity: 0.85,
      attribution: "NASA GIBS / EOSDIS",
    });
    state.tileLayers[layer.id] = tile;
    const checked = layer.id === "ndvi";
    if (checked) tile.addTo(state.map);
    return `
      <label class="layer-row">
        <input type="checkbox" data-layer="${esc(layer.id)}" ${checked ? "checked" : ""} />
        <span>${esc(layer.name)}<small>${esc(layer.description)}</small></span>
      </label>`;
  });

  $("#layerToggles").innerHTML = toggles.join("") || emptyState("NASA GIBS layer templates unavailable.");
  $$("[data-layer]").forEach((checkbox) =>
    checkbox.addEventListener("change", () => {
      const layer = state.tileLayers[checkbox.dataset.layer];
      if (!layer) return;
      if (checkbox.checked) layer.addTo(state.map);
      else layer.remove();
    })
  );

  const analysis = state.analysis || {};
  const elevation = analysis.location?.elevation_m;
  $("#mapStats").innerHTML = `
    <strong>${esc(state.farm.name)}</strong><br/>
    ${num(latitude, 3)}, ${num(longitude, 3)}${elevation !== null && elevation !== undefined ? ` · ${num(elevation, 0)} m` : ""}<br/>
    Baseline rainfall ${num(analysis.baseline?.annual_rain_mm, 0)} mm · mean ${num(analysis.baseline?.annual_mean_t2m_c, 1)} °C<br/>
    <span class="muted">${esc(analysis.data_source || "data source pending")}</span>`;
}

function renderMapKpis() {
  const analysis = state.analysis || {};
  const winner = state.rotations?.rotations?.[0];
  const cells = [
    ["Mean annual rainfall", `${num(analysis.baseline?.annual_rain_mm, 0)} mm`, `${analysis.baseline?.years ?? 0} complete years`],
    ["Mean temperature", `${num(analysis.baseline?.annual_mean_t2m_c, 1)} °C`, "NASA POWER baseline"],
    ["Climate stress score", num(analysis.stress?.score, 0), `${analysis.stress?.flags?.length || 0} signals detected`],
    ["Best rotation score", num(winner?.score, 1), winner ? winner.sequence_names.map(cropName).join(" → ") : "not computed"],
  ];
  $("#mapKpis").innerHTML = cells
    .map(([k, v, s]) => `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div></div>`)
    .join("");
}

/* ----------------------------------------------------------------- climate */

function renderClimate() {
  const analysis = state.analysis;
  if (!analysis) {
    $("#climateKpis").innerHTML = "";
    return;
  }
  const recent = analysis.windows?.recent_90d || {};
  const anomalies = analysis.anomalies || {};

  $("#climateConfidence").textContent = `data confidence: ${analysis.data_quality?.confidence || "unknown"}`;
  $("#climateConfidence").className = `badge ${severityClass(analysis.data_quality?.confidence)}`;

  $("#climateKpis").innerHTML = [
    ["Annual rainfall (baseline)", `${num(analysis.baseline?.annual_rain_mm, 0)} mm`, "Observed history"],
    ["Mean temperature", `${num(analysis.baseline?.annual_mean_t2m_c, 1)} °C`, "Observed history"],
    ["Rainfall anomaly (90 d)", `${pct(anomalies.rain_90d_pct, 0)}`, "vs same window historically"],
    ["Days above 35 °C (90 d)", num(recent.heat_days_35, 0), "Observed"],
  ]
    .map(([k, v, s]) => `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div></div>`)
    .join("");

  renderAnnualChart(analysis.annual_series || []);
  renderMonthlyChart(analysis.monthly_climatology || []);

  const trends = analysis.trends || {};
  $("#trendText").textContent =
    `Trend across ${trends.years_used || 0} complete years: rainfall ` +
    `${trends.rain_mm_per_decade === null ? "—" : num(trends.rain_mm_per_decade, 0)} mm/decade, temperature ` +
    `${trends.t2m_c_per_decade === null ? "—" : num(trends.t2m_c_per_decade, 2)} °C/decade. ` +
    `Observed gridded data, not an on-farm measurement.`;

  const flags = analysis.stress?.flags || [];
  $("#stressList").innerHTML = flags.length
    ? flags
        .map(
          (flag) => `
        <div class="stress-item ${severityClass(flag.severity)}">
          <div class="row"><strong>${esc(flag.type.replace(/_/g, " "))}</strong><span class="badge ${severityClass(flag.severity)}">${esc(flag.severity)}</span></div>
          <p>${esc(flag.message)}</p>
          <div class="facts">Observed: ${esc(flag.observed)} · Baseline: ${esc(flag.baseline)}</div>
        </div>`
        )
        .join("")
    : `<p class="muted">No stress signal exceeded the configured thresholds in the last 90 days.</p>`;

  const proxy = analysis.vegetation_proxy || {};
  $("#proxyDetail").innerHTML = `
    <div class="grid-3" style="margin-bottom:8px">
      <div class="stat"><div class="k">Recent</div><div class="v">${num(proxy.recent_mean, 2)}</div><div class="s">root-zone wetness</div></div>
      <div class="stat"><div class="k">Baseline</div><div class="v">${num(proxy.baseline_mean, 2)}</div><div class="s">same window</div></div>
      <div class="stat"><div class="k">Anomaly</div><div class="v">${pct(proxy.anomaly_pct, 0)}</div><div class="s">vs baseline</div></div>
    </div>`;
  $("#proxyNote").textContent = proxy.note || "";

  const forecast = analysis.forecast || {};
  const days = state.weather?.forecast?.days || [];
  $("#forecastProvider").textContent = state.weather?.forecast?.provider || forecast.provider || "unavailable";
  if (!days.length) {
    $("#forecastPanel").innerHTML = emptyState(state.weather?.forecast_error || "No forecast provider returned data.");
  } else {
    $("#forecastPanel").innerHTML = `
      <p class="small muted">${esc(state.weather.forecast.provenance || "Forecast")} · ${num(forecast.rain_mm_14d, 1)} mm expected over the forecast window.</p>
      <table class="crit-table">
        <tr><th>Date</th><th class="num">Max °C</th><th class="num">Min °C</th><th class="num">Rain mm</th></tr>
        ${days
          .slice(0, 10)
          .map(
            (day) => `<tr><td>${esc(day.date)}</td><td class="num">${num(day.tmax_c, 1)}</td><td class="num">${num(day.tmin_c, 1)}</td><td class="num">${num(day.precip_mm, 1)}</td></tr>`
          )
          .join("")}
      </table>`;
  }
}

/* ------------------------------------------------------------------ charts */

function renderAnnualChart(series) {
  const container = $("#annualChart");
  const complete = series.filter((entry) => entry.complete && entry.rain_mm !== null);
  if (complete.length < 2) {
    container.innerHTML = `<div class="chart-empty">Not enough complete years for a trend chart.</div>`;
    return;
  }
  const width = 640, height = 220, padL = 40, padR = 38, padT = 14, padB = 30;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;
  const maxRain = Math.max(...complete.map((entry) => entry.rain_mm)) * 1.15;
  const temps = complete.map((entry) => entry.mean_t2m_c).filter((value) => value !== null);
  const minT = Math.min(...temps) - 1;
  const maxT = Math.max(...temps) + 1;
  const barW = innerW / complete.length;

  const bars = complete
    .map((entry, index) => {
      const x = padL + index * barW + barW * 0.2;
      const h = (entry.rain_mm / maxRain) * innerH;
      const y = padT + innerH - h;
      return `<rect class="bar" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(barW * 0.6).toFixed(1)}" height="${h.toFixed(1)}" rx="2">
        <title>${entry.year}: ${num(entry.rain_mm, 0)} mm, ${num(entry.mean_t2m_c, 1)} °C</title></rect>
        <text x="${(padL + index * barW + barW / 2).toFixed(1)}" y="${height - 10}" text-anchor="middle">${entry.year}</text>`;
    })
    .join("");

  const points = complete
    .map((entry, index) => {
      if (entry.mean_t2m_c === null) return null;
      const x = padL + index * barW + barW / 2;
      const y = padT + innerH - ((entry.mean_t2m_c - minT) / (maxT - minT)) * innerH;
      return { x, y, value: entry.mean_t2m_c };
    })
    .filter(Boolean);

  const line = points.map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
  const dots = points
    .map((point) => `<circle class="dot" cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="2.6"><title>${num(point.value, 1)} °C</title></circle>`)
    .join("");

  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Annual rainfall bars with temperature line">
      <line class="axis" x1="${padL}" y1="${padT + innerH}" x2="${width - padR}" y2="${padT + innerH}" />
      <text x="${padL - 6}" y="${padT + 8}" text-anchor="end">${num(maxRain, 0)}</text>
      <text x="${padL - 6}" y="${padT + innerH}" text-anchor="end">0 mm</text>
      <text x="${width - padR + 6}" y="${padT + 8}" text-anchor="start">${num(maxT, 0)}°</text>
      <text x="${width - padR + 6}" y="${padT + innerH}" text-anchor="start">${num(minT, 0)}°</text>
      ${bars}
      <path class="line" d="${line}" />
      ${dots}
    </svg>
    <p class="small muted" style="margin:6px 0 0">Green bars: annual rainfall (mm). Orange line: mean temperature (°C, right axis).</p>`;
}

function renderMonthlyChart(monthly) {
  const container = $("#monthlyChart");
  if (!monthly.length) {
    container.innerHTML = `<div class="chart-empty">Monthly climatology unavailable.</div>`;
    return;
  }
  const labels = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];
  const width = 640, height = 220, padL = 40, padR = 12, padT = 14, padB = 30;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;
  const maxRain = Math.max(...monthly.map((entry) => entry.rain_mm || 0)) * 1.15 || 1;
  const barW = innerW / 12;

  const bars = monthly
    .map((entry, index) => {
      const value = entry.rain_mm || 0;
      const h = (value / maxRain) * innerH;
      const x = padL + index * barW + barW * 0.2;
      const y = padT + innerH - h;
      return `<rect class="bar ${value < maxRain * 0.25 ? "alt" : ""}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(barW * 0.6).toFixed(1)}" height="${h.toFixed(1)}" rx="2">
        <title>${labels[index]}: ${num(value, 0)} mm, ${num(entry.mean_t2m_c, 1)} °C</title></rect>
        <text x="${(padL + index * barW + barW / 2).toFixed(1)}" y="${height - 10}" text-anchor="middle">${labels[index]}</text>`;
    })
    .join("");

  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Monthly rainfall climatology">
      <line class="axis" x1="${padL}" y1="${padT + innerH}" x2="${width - padR}" y2="${padT + innerH}" />
      <text x="${padL - 6}" y="${padT + 8}" text-anchor="end">${num(maxRain, 0)}</text>
      <text x="${padL - 6}" y="${padT + innerH}" text-anchor="end">0 mm</text>
      ${bars}
    </svg>
    <p class="small muted" style="margin:6px 0 0">Mean monthly rainfall from the observed baseline. These windows drive the water accounting.</p>`;
}

/* -------------------------------------------------------------- rotations */

function renderRotations() {
  const result = state.rotations;
  if (!result) {
    $("#rotationWinner").innerHTML = emptyState("Run the engine to compare rotations.");
    $("#rotationList").innerHTML = "";
    return;
  }
  const winner = result.rotations[0];

  $("#weightChips").innerHTML = Object.entries(result.weights)
    .map(([key, value]) => `<span class="chip"><strong>${esc(key)}</strong> ${num(value * 100, 0)}%</span>`)
    .join("");

  $("#rotationWinner").innerHTML = `
    <div class="winner-card">
      <div class="winner-top">
        <div>
          <span class="badge">recommended sequence</span>
          <h2 style="margin-top:8px">${esc(winner.label)}</h2>
          <p class="sequence">${winner.sequence_names.map((name) => `<b>${esc(name)}</b>`).join(" → ")}</p>
          <p class="small muted">${esc(winner.rationale || "")} · confidence <span class="badge ${severityClass(winner.confidence)}">${esc(winner.confidence)}</span> · rank 1 of ${result.rotations.length}</p>
        </div>
        <div class="winner-score">${num(winner.score, 1)}<span>suitability / 100</span></div>
      </div>
      <div style="margin-top:12px; display:flex; gap:10px; flex-wrap:wrap">
        ${winner.benefits.map((item) => `<span class="chip">${esc(item)}</span>`).join("")}
      </div>
    </div>`;

  $("#rotationList").innerHTML = result.rotations
    .map((rotation, index) => rotationCard(rotation, index === 0))
    .join("");

  $$(".rotation-head").forEach((head) =>
    head.addEventListener("click", () => head.closest(".rotation-card").classList.toggle("open"))
  );

  $("#scoreExplainer").innerHTML = `
    <p>Each candidate is scored on five criteria. The weighted sum is
      <code>S = Σ wᵢ · sᵢ · (0.85 + 0.15 · qᵢ)</code>, where <code>wᵢ</code> is the criterion weight,
      <code>sᵢ</code> the criterion score and <code>qᵢ</code> its data quality (0–1).
      Missing inputs therefore lower both the criterion score and the penalty factor; they never become an assumed value.</p>
    <table class="crit-table">
      <tr><th>Criterion</th><th class="num">Weight</th><th>What it measures</th></tr>
      <tr><td>Water suitability</td><td class="num">${num(result.weights.water * 100, 0)}%</td><td>Expected rainfall in each crop's own planting window plus declared irrigation, against indicative seasonal demand.</td></tr>
      <tr><td>Climate suitability</td><td class="num">${num(result.weights.climate * 100, 0)}%</td><td>Temperature range fit, heat-day exposure and drought tolerance against the observed anomaly.</td></tr>
      <tr><td>Soil &amp; nutrient compatibility</td><td class="num">${num(result.weights.soil * 100, 0)}%</td><td>Texture and pH fit, organic-carbon adjustment and biological nitrogen credits.</td></tr>
      <tr><td>Pest &amp; disease break</td><td class="num">${num(result.weights.pest * 100, 0)}%</td><td>Consecutive same crop or same family, family diversity and recent field history.</td></tr>
      <tr><td>Farmer priorities</td><td class="num">${num(result.weights.preference * 100, 0)}%</td><td>Declared priority alignment, preferred crops and food-security weighting.</td></tr>
    </table>
    <p class="small muted">${esc(result.weight_note)}</p>
    <p class="small muted">Agronomic guard: a sequence is penalised when any crop would receive under 45% of its indicative water need, or when the same crop is repeated. The penalty points and their reason are reported with the result; the weighted criteria score is shown separately.</p>
    <p class="small muted">${esc(result.crop_parameter_provenance)}</p>`;
}

function rotationCard(rotation, open) {
  const criteriaRows = Object.entries(rotation.criteria)
    .map(([, criterion]) => {
      const barClass = criterion.score < 45 ? "bad" : criterion.score < 65 ? "low" : "";
      return `<tr>
        <td>${esc(criterion.label)}
          <div class="crit-bar ${barClass}"><i style="width:${Math.max(2, Math.min(100, criterion.score))}%"></i></div>
        </td>
        <td class="num">${num(criterion.score, 1)}</td>
        <td class="num">${num(criterion.weight * 100, 0)}%</td>
        <td class="num">${num(criterion.data_quality * 100, 0)}%</td>
        <td class="small">${esc(criterion.explanation)}</td>
      </tr>`;
    })
    .join("");

  const schedule = rotation.schedule
    .map(
      (slot) => `<div class="slot"><b>${esc(slot.name)}</b>${esc(slot.planting)} → ${esc(slot.harvest)}<br/>
        <span class="muted">${num(slot.window_rain_mm, 0)} mm rain in window</span></div>`
    )
    .join("");

  const waterRows = (rotation.water_budget.rows || [])
    .map(
      (row) => `<tr>
        <td>${esc(cropName(row.crop))}</td>
        <td class="num">${num(row.need_mm, 0)}</td>
        <td class="num">${num(row.supply_mm, 0)}</td>
        <td class="num">${row.ratio === null ? "—" : num(row.ratio, 2)}</td>
        <td class="num">${num(row.score, 1)}</td>
        <td class="small">${esc(row.window || "")}</td>
      </tr>`
    )
    .join("");

  const evidence = (rotation.evidence || [])
    .map(
      (item) => `<div class="evidence-item"><b>${esc(item.type)} · ${esc(item.source)}</b>${esc(item.detail)}</div>`
    )
    .join("");

  return `
    <div class="rotation-card ${open ? "open" : ""} ${rotation.rank === 1 ? "top" : ""}">
      <div class="rotation-head">
        <div class="rank">${rotation.rank}</div>
        <div>
          <strong>${esc(rotation.label)}</strong>
          <div class="meta">${rotation.sequence_names.map(esc).join(" → ")} · confidence ${esc(rotation.confidence)}</div>
        </div>
        <div class="rotation-score"><strong>${num(rotation.score, 1)}</strong><span>of 100</span></div>
      </div>
      <div class="rotation-body">
        <p class="small muted" style="margin-top:10px">${esc(rotation.rationale || "")}</p>
        ${rotation.constraints?.length ? `<div class="flag medium">Agronomic guard: weighted criteria score ${num(rotation.score_before_constraints, 1)} − ${num(rotation.score_penalty, 1)} penalty = ${num(rotation.score, 1)}. ${esc(rotation.constraints[0].message)}</div>` : ""}
        <div class="schedule">${schedule}</div>

        <table class="crit-table">
          <tr><th>Criterion</th><th class="num">Score</th><th class="num">Weight</th><th class="num">Quality</th><th>Why</th></tr>
          ${criteriaRows}
        </table>

        <div class="two-col" style="margin:6px 0 10px">
          <div><h4>Benefits</h4><ul class="small">${rotation.benefits.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>
          <div><h4>Concerns</h4><ul class="small">${(rotation.concerns.length ? rotation.concerns : ["No specific concerns detected from available inputs."]).map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>
        </div>

        <h4>Water accounting (indicative)</h4>
        <table class="crit-table">
          <tr><th>Crop</th><th class="num">Need mm</th><th class="num">Supply mm</th><th class="num">Ratio</th><th class="num">Score</th><th>Window</th></tr>
          ${waterRows}
        </table>
        <p class="small muted">${esc(rotation.water_budget.assumptions || "")}</p>

        <h4>Evidence used</h4>
        ${evidence}
      </div>
    </div>`;
}

/* ------------------------------------------------------------- what-if sim */

function bindSimulation() {
  const slider = $("#rainSlider");
  slider.addEventListener("input", () => {
    $("#rainValue").textContent = `${slider.value > 0 ? "+" : ""}${slider.value}%`;
  });
  $("#simulateBtn").addEventListener("click", runSimulation);
}

async function runSimulation() {
  if (!state.farm) return;
  const changes = {};
  const rain = Number($("#rainSlider").value);
  if (rain !== 0) changes.rainfall_change_pct = rain;
  if ($("#simIrrigation").value) changes.irrigation = $("#simIrrigation").value;
  if ($("#simPriority").value) changes.priority = $("#simPriority").value;

  if (!Object.keys(changes).length) {
    showBanner("Change at least one scenario input first.", "info");
    return;
  }

  setBusy(true, "Re-running the deterministic engine…");
  try {
    const result = await api(`/api/farms/${state.farm.id}/simulate`, {
      method: "POST",
      body: JSON.stringify(changes),
    });
    renderSimulation(result);
  } catch (error) {
    showBanner(`Scenario failed: ${error.message}`, "error", true);
  } finally {
    setBusy(false);
  }
}

function renderSimulation(result) {
  $("#simInterpretation").textContent = result.interpretation;
  const maxScore = Math.max(...result.deltas.map((item) => Math.max(item.before ?? 0, item.after)), 100);

  $("#simResults").innerHTML = result.deltas
    .map((item) => {
      const delta = item.delta ?? 0;
      const cls = delta > 0.05 ? "delta-up" : delta < -0.05 ? "delta-down" : "delta-flat";
      const sign = delta > 0 ? "+" : "";
      const beforePct = ((item.before ?? 0) / maxScore) * 100;
      return `<div class="sim-row">
        <span class="name">${esc(item.label)}<br/><span class="small muted">${item.sequence_names.map(esc).join(" → ")}</span></span>
        <span class="num">${num(item.before, 1)}</span>
        <span class="num">${num(item.after, 1)} <span class="${cls}">(${sign}${num(delta, 1)})</span></span>
        <div class="sim-track"><i style="width:${Math.max(2, Math.min(100, ((item.after ?? 0) / maxScore) * 100))}%"></i><b style="left:${Math.max(0, Math.min(100, beforePct))}%"></b></div>
      </div>`;
    })
    .join("");
  $("#simResults").insertAdjacentHTML(
    "beforeend",
    `<p class="small muted" style="margin-top:10px">The grey marker is the pre-scenario score. Only the declared inputs changed; the scoring rules are identical between runs.</p>`
  );
}

/* ----------------------------------------------------------------- report */

function bindReport() {
  $("#buildReportBtn").addEventListener("click", buildReport);
}

async function buildReport() {
  if (!state.farm) return;
  setBusy(true, "Assembling the adaptation report…");
  try {
    const report = await api(`/api/farms/${state.farm.id}/report?format=json`);
    state.report = report;
    renderReport(report);
  } catch (error) {
    showBanner(`Report failed: ${error.message}`, "error", true);
  } finally {
    setBusy(false);
  }
}

function renderReport(report) {
  const explanation = report.explanation || {};
  $("#explanationProvider").textContent = explanation.provider || "unknown";
  $("#reportNarrative").innerHTML =
    `<div class="narrative">${(explanation.text || "").split("\n\n").map((paragraph) => `<p>${esc(paragraph)}</p>`).join("")}</div>` +
    (explanation.note ? `<p class="small muted">${esc(explanation.note)}</p>` : "");

  const analysis = report.analysis || {};
  $("#reportEvidence").innerHTML = `
    <div class="evidence-item"><b>Observed</b>${esc(analysis.evidence?.observed?.source || "—")} · ${esc(analysis.evidence?.observed?.kind || "")}<br/>
      <span class="small muted">${esc(analysis.evidence?.observed?.window || "")}</span></div>
    <div class="evidence-item"><b>Forecast</b>${esc(analysis.evidence?.forecast?.source || "—")} · ${num(analysis.evidence?.forecast?.days, 0)} days<br/>
      <span class="small muted">Forecasts carry uncertainty that grows with lead time.</span></div>
    <div class="evidence-item"><b>Research-backed</b>Crop parameter reference table<br/>
      <span class="small muted">${esc(report.rotation.crop_parameter_provenance)}</span></div>
    <div class="evidence-item"><b>Data quality</b>${esc(analysis.data_quality?.confidence || "unknown")} confidence<br/>
      <span class="small muted">${esc((analysis.data_quality?.notes || []).join(" "))}</span></div>`;

  const research = report.research || {};
  $("#researchProvider").textContent = research.available ? research.provider : "not configured";
  $("#researchResults").innerHTML = research.available && research.results?.length
    ? research.results
        .map(
          (item) => `<div class="research-item">
            <a href="${esc(item.url)}" target="_blank" rel="noopener">${esc(item.title)}</a>
            ${item.trusted_domain ? '<span class="badge">authoritative</span>' : ""}
            <div class="small muted">${esc(item.snippet)}</div>
          </div>`
        )
        .join("")
    : `<p class="small muted">${esc(research.reason || "No research provider configured.")} ${esc(research.note || "")}</p>` +
      `<div class="chips">${(research.suggested_sources || []).map((source) => `<span class="chip"><strong>${esc(source.name)}</strong> ${esc(source.domain)}</span>`).join("")}</div>`;
}

/* ------------------------------------------------------------------ form */

function populateCropSelect() {
  const select = $("#currentCropSelect");
  const groups = {};
  state.crops
    .filter((crop) => crop.id !== "fallow")
    .forEach((crop) => {
      (groups[crop.category] = groups[crop.category] || []).push(crop);
    });
  select.innerHTML = Object.entries(groups)
    .map(
      ([category, crops]) =>
        `<optgroup label="${esc(category)}">${crops
          .map((crop) => `<option value="${esc(crop.id)}">${esc(crop.name)}</option>`)
          .join("")}</optgroup>`
    )
    .join("");
}

function renderCropCatalogue() {
  const shownCrops = state.crops.filter((crop) => crop.id !== "fallow");
  $("#cropCount").textContent = `${shownCrops.length} crops + fallow`;
  $("#cropChips").innerHTML = state.crops
    .filter((crop) => crop.id !== "fallow")
    .map((crop) => `<span class="chip" title="${esc(crop.family)} · ${num(crop.water_need_mm, 0)} mm · pH ${num(crop.ph_min, 1)}–${num(crop.ph_max, 1)}"><strong>${esc(crop.name)}</strong></span>`)
    .join("");
}

function fillForm(farm) {
  const form = $("#farmForm");
  form.reset();
  const fields = ["name", "country", "region", "area_ha", "latitude", "longitude", "soil_texture", "soil_ph", "organic_carbon_pct", "irrigation", "current_crop", "priority"];
  fields.forEach((field) => {
    if (form.elements[field] && farm[field] !== null && farm[field] !== undefined) {
      form.elements[field].value = farm[field];
    }
  });
  form.elements.previous_crops.value = (farm.previous_crops || []).join(", ");
  form.elements.preferred_crops.value = (farm.preferred_crops || []).join(", ");
  $("#profileMode").textContent = farm.id ? "editing farm" : "new farm";
}

function bindForm() {
  $("#farmForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const data = Object.fromEntries(new FormData(form).entries());
    const toList = (value) =>
      String(value || "")
        .split(",")
        .map((item) => item.trim().toLowerCase().replace(/\s+/g, "_"))
        .filter(Boolean);

    const payload = {
      name: data.name.trim(),
      latitude: Number(data.latitude),
      longitude: Number(data.longitude),
      area_ha: data.area_ha ? Number(data.area_ha) : null,
      country: data.country || null,
      region: data.region || null,
      soil_texture: data.soil_texture,
      soil_ph: data.soil_ph ? Number(data.soil_ph) : null,
      organic_carbon_pct: data.organic_carbon_pct ? Number(data.organic_carbon_pct) : null,
      irrigation: data.irrigation,
      current_crop: data.current_crop,
      previous_crops: toList(data.previous_crops),
      preferred_crops: toList(data.preferred_crops),
      priority: data.priority,
    };

    setBusy(true, "Saving farm profile…");
    try {
      let farm;
      if (state.farm?.id) {
        farm = await api(`/api/farms/${state.farm.id}`, { method: "PATCH", body: JSON.stringify(payload) });
        state.farms = state.farms.map((item) => (item.id === farm.id ? farm : item));
      } else {
        farm = await api("/api/farms", { method: "POST", body: JSON.stringify(payload) });
        state.farms.unshift(farm);
        showBanner("Farm profile created. Running the engine…", "info");
      }
      await selectFarm(farm.id);
    } catch (error) {
      showBanner(`Could not save the profile: ${error.message}`, "error", true);
    } finally {
      setBusy(false);
    }
  });
}

/* --------------------------------------------------------------- rendering */

function renderAll() {
  renderMap();
  renderMapKpis();
  renderClimate();
  renderRotations();
  $("#simulateBtn").disabled = !state.farm;
  $("#printReportLink").href = state.farm ? `/api/farms/${state.farm.id}/report` : "#";
}

document.addEventListener("DOMContentLoaded", init);
