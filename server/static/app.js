const state = {
  map: null,
  baseLayer: null,
  basemapKey: "standard",
  basemapLayers: new Map(),
  basemapSwitchToken: 0,
  basemapSwitchTimer: null,
  layerGroups: new Map(),
  layerMeta: new Map(),
  featureIndex: new Map(),
  focusedLayer: null,
  focusedOriginalStyle: null,
  highlightedLayers: [],
  selected: null,
  bootstrap: null,
  baseBounds: null,
  sessionId: getSessionId(),
  activeStream: null,
  activeRunId: null,
  autonomyStream: null,
  autonomyPhase: "",
  eventMarkers: new Map(),
  hydrodynamicGridMeta: null,
  hydrodynamicResultMeta: null,
  lastTrace: null,
  boundaryFlowLayer: null,
  boundaryFlowFeatures: null,
  mockRunning: false,
  hydrodynamicTimeline: {
    hours: [],
    index: 0,
    layer: null,
    key: null,
    baseFilters: null,
    timer: null,
    playing: false,
  },
  impactAnalysis: null,
  pendingImpactObjectTypes: new Set(),
  impactRefreshTimer: null,
  impactRefreshSeq: 0,
  applyingImpactRefresh: false,
};

const BASEMAP_STORAGE_KEY = "flood-basemap";
const AMAP_PROJECTION = {
  bounds: L.Projection.SphericalMercator.bounds,
  project(latlng) {
    const shifted = wgs84ToGcj02(latlng.lng, latlng.lat);
    return L.Projection.SphericalMercator.project(L.latLng(shifted.lat, shifted.lng));
  },
  unproject(point) {
    const shifted = L.Projection.SphericalMercator.unproject(point);
    const original = gcj02ToWgs84(shifted.lng, shifted.lat);
    return L.latLng(original.lat, original.lng);
  },
};
const AMAP_CRS = L.Util.extend({}, L.CRS.EPSG3857, {
  code: "GCJ02:3857",
  projection: AMAP_PROJECTION,
});

const BASEMAPS = {
  standard: {
    layers: [{
      url: "https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={z}",
      options: {
        subdomains: "1234",
        maxZoom: 20,
        maxNativeZoom: 18,
        attribution: "&copy; 高德地图",
      },
    }],
  },
  satellite: {
    layers: [{
      url: "https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}",
      options: {
        subdomains: "1234",
        maxZoom: 20,
        maxNativeZoom: 18,
        attribution: "&copy; 高德地图",
      },
    }],
  },
  hybrid: {
    layers: [
      {
        url: "https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}",
        options: {
          subdomains: "1234",
          maxZoom: 20,
          maxNativeZoom: 18,
          attribution: "&copy; 高德地图",
        },
      },
      {
        url: "https://webst0{s}.is.autonavi.com/appmaptile?style=8&x={x}&y={y}&z={z}",
        options: {
          subdomains: "1234",
          maxZoom: 20,
          maxNativeZoom: 18,
          attribution: "&copy; 高德地图",
        },
      },
    ],
  },
};

const OBJECT_CONFIG = {
  River: { label: "珊瑚河", color: "#0e7490", swatch: "line" },
  Watershed: { label: "珊瑚河流域", color: "#1f2937", swatch: "fill" },
  HydrodynamicBoundary: { label: "水动力边界", color: "#e11d48", swatch: "line" },
  County: { label: "县级边界", color: "#7b8794", swatch: "line" },
  Town: { label: "乡镇边界", color: "#7a6a22", swatch: "fill" },
  Road: { label: "道路", color: "#5f6772", swatch: "line" },
  Reservoir: { label: "水库", color: "#2f80c9", swatch: "point" },
  Sluice: { label: "水闸", color: "#158a8a", swatch: "point" },
  Bridge: { label: "桥梁", color: "#202833", swatch: "point" },
  HydraulicStructure: { label: "水利工程", color: "#0f766e", swatch: "line" },
  Facility: { label: "重要设施", color: "#d44a3a", swatch: "point" },
  Place: { label: "安置地点", color: "#24895d", swatch: "point" },
  Transfer: { label: "转移对象", color: "#c97a12", swatch: "point" },
  Route: { label: "转移路线", color: "#d44a3a", swatch: "line" },
  Risk: { label: "危险区", color: "#b91c1c", swatch: "point" },
  HydroStation: { label: "水文测站", color: "#0284c7", swatch: "point" },
  ForecastCell: { label: "预测淹没", color: "#dc2626", swatch: "fill" },
  HydrodynamicCell: { label: "水动力网格", color: "#64748b", swatch: "fill" },
  ForecastResult: { label: "预测淹没结果", color: "#dc2626", swatch: "fill" },
};

const ID_FIELDS = {
  River: "river_id",
  Watershed: "watershed_id",
  HydrodynamicBoundary: "boundary_id",
  County: "county_id",
  Town: "town_id",
  Road: "road_id",
  Reservoir: "reservoir_id",
  Sluice: "sluice_id",
  Bridge: "bridge_id",
  HydraulicStructure: "structure_id",
  Facility: "facility_id",
  Place: "place_id",
  Transfer: "transfer_id",
  Route: "route_id",
  Risk: "risk_id",
  HydroStation: "station_id",
  ForecastCell: "forecast_cell_id",
  HydrodynamicCell: "hydrodynamic_cell_id",
};

const MAP_NON_SELECTABLE_OBJECTS = new Set(["Watershed", "County", "Town"]);
const ICON_OBJECT_TYPES = new Set([
  "Reservoir",
  "Sluice",
  "Bridge",
  "Facility",
  "Place",
  "Transfer",
  "Risk",
  "HydroStation",
]);

document.addEventListener("DOMContentLoaded", async () => {
  initMap();
  bindEvents();
  await bootstrap();
  await loadObject("Watershed", {}, { fit: true });
  await loadObject("County", {}, { fit: false });
  await loadObject("HydrodynamicBoundary", {}, { fit: false });
  addMessage("agent", "基础对象已加载。");
  startAutonomyStream();
  await refreshMockStatus();
  renderIcons();
});

function initMap() {
  state.map = L.map("map", {
    crs: AMAP_CRS,
    zoomControl: false,
    preferCanvas: true,
  }).setView([24.4, 111.35], 10);

  L.control.zoom({ position: "bottomleft" }).addTo(state.map);
  setBasemap(readStoredBasemap(), { persist: false });
}

function setBasemap(key, options = {}) {
  const nextKey = BASEMAPS[key] ? key : "standard";
  let nextLayer = state.basemapLayers.get(nextKey);
  if (!nextLayer) {
    const config = BASEMAPS[nextKey];
    const tileLayers = config.layers.map((item) => L.tileLayer(item.url, {
      ...item.options,
      pane: "tilePane",
      updateWhenIdle: true,
      keepBuffer: 3,
    }));
    nextLayer = tileLayers.length === 1 ? tileLayers[0] : L.layerGroup(tileLayers);
    nextLayer._basemapTileLayers = tileLayers;
    state.basemapLayers.set(nextKey, nextLayer);
  }
  state.basemapKey = nextKey;
  if (state.baseLayer !== nextLayer) {
    const previousLayer = state.baseLayer;
    const switchToken = state.basemapSwitchToken + 1;
    state.basemapSwitchToken = switchToken;
    if (state.basemapSwitchTimer) window.clearTimeout(state.basemapSwitchTimer);
    state.basemapSwitchTimer = null;
    if (previousLayer) {
      setBasemapOpacity(nextLayer, 0);
      whenBasemapLoaded(nextLayer, () => finishBasemapSwitch(nextKey, nextLayer, switchToken));
      state.basemapSwitchTimer = window.setTimeout(
        () => finishBasemapSwitch(nextKey, nextLayer, switchToken),
        15000,
      );
    }
    nextLayer.addTo(state.map);
    if (!previousLayer) {
      setBasemapOpacity(nextLayer, 1);
      bringBasemapToBack(nextLayer);
    }
    state.baseLayer = nextLayer;
  }
  setBasemapButtonActive(nextKey);
  if (options.persist !== false) storeBasemap(nextKey);
}

function finishBasemapSwitch(key, layer, switchToken) {
  if (state.basemapSwitchToken !== switchToken || state.basemapKey !== key) {
    window.setTimeout(() => {
      if (state.map.hasLayer(layer) && state.baseLayer !== layer) {
        state.map.removeLayer(layer);
      }
    }, 0);
    return;
  }
  if (state.basemapSwitchTimer) window.clearTimeout(state.basemapSwitchTimer);
  state.basemapSwitchTimer = null;
  setBasemapOpacity(layer, 1);
  bringBasemapToBack(layer);
  state.basemapLayers.forEach((candidate) => {
    if (candidate !== layer && state.map.hasLayer(candidate)) {
      state.map.removeLayer(candidate);
    }
  });
}

function basemapTileLayers(layer) {
  return layer?._basemapTileLayers || [layer];
}

function setBasemapOpacity(layer, opacity) {
  basemapTileLayers(layer).forEach((tileLayer) => tileLayer?.setOpacity?.(opacity));
}

function bringBasemapToBack(layer) {
  basemapTileLayers(layer).slice().reverse().forEach((tileLayer) => tileLayer?.bringToBack?.());
}

function whenBasemapLoaded(layer, callback) {
  const pending = new Set(basemapTileLayers(layer));
  pending.forEach((tileLayer) => {
    tileLayer.once("load", () => {
      pending.delete(tileLayer);
      if (!pending.size) callback();
    });
  });
}

function setBasemapButtonActive(key) {
  document.querySelectorAll("[data-basemap]").forEach((button) => {
    const active = button.dataset.basemap === key;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-checked", String(active));
  });
}

function readStoredBasemap() {
  try {
    const value = window.localStorage.getItem(BASEMAP_STORAGE_KEY) || "standard";
    const migrated = { light: "standard", terrain: "hybrid" }[value] || value;
    return BASEMAPS[migrated] ? migrated : "standard";
  } catch {
    return "standard";
  }
}

function storeBasemap(key) {
  try {
    window.localStorage.setItem(BASEMAP_STORAGE_KEY, key);
  } catch {
    // Browsers with restricted storage still keep the current in-memory selection.
  }
}

function wgs84ToGcj02(lng, lat) {
  if (outsideChina(lng, lat)) return { lng, lat };
  const axis = 6378245.0;
  const eccentricity = 0.006693421622965943;
  let deltaLat = gcjTransformLat(lng - 105.0, lat - 35.0);
  let deltaLng = gcjTransformLng(lng - 105.0, lat - 35.0);
  const radians = (lat / 180.0) * Math.PI;
  let magic = Math.sin(radians);
  magic = 1 - eccentricity * magic * magic;
  const rootMagic = Math.sqrt(magic);
  deltaLat = (deltaLat * 180.0) / (((axis * (1 - eccentricity)) / (magic * rootMagic)) * Math.PI);
  deltaLng = (deltaLng * 180.0) / ((axis / rootMagic) * Math.cos(radians) * Math.PI);
  return { lng: lng + deltaLng, lat: lat + deltaLat };
}

function gcj02ToWgs84(lng, lat) {
  if (outsideChina(lng, lat)) return { lng, lat };
  let originalLng = lng;
  let originalLat = lat;
  for (let index = 0; index < 4; index += 1) {
    const shifted = wgs84ToGcj02(originalLng, originalLat);
    originalLng += lng - shifted.lng;
    originalLat += lat - shifted.lat;
  }
  return { lng: originalLng, lat: originalLat };
}

function outsideChina(lng, lat) {
  return lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271;
}

function gcjTransformLat(x, y) {
  let value = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
  value += ((20 * Math.sin(6 * x * Math.PI) + 20 * Math.sin(2 * x * Math.PI)) * 2) / 3;
  value += ((20 * Math.sin(y * Math.PI) + 40 * Math.sin((y / 3) * Math.PI)) * 2) / 3;
  value += ((160 * Math.sin((y / 12) * Math.PI) + 320 * Math.sin((y * Math.PI) / 30)) * 2) / 3;
  return value;
}

function gcjTransformLng(x, y) {
  let value = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
  value += ((20 * Math.sin(6 * x * Math.PI) + 20 * Math.sin(2 * x * Math.PI)) * 2) / 3;
  value += ((20 * Math.sin(x * Math.PI) + 40 * Math.sin((x / 3) * Math.PI)) * 2) / 3;
  value += ((150 * Math.sin((x / 12) * Math.PI) + 300 * Math.sin((x / 30) * Math.PI)) * 2) / 3;
  return value;
}

async function bootstrap() {
  const res = await fetch("/api/bootstrap");
  state.bootstrap = await res.json();
  document.getElementById("contextPill").textContent = state.bootstrap.default_context;
  renderObjectList(state.bootstrap.mappable || []);
}

function renderObjectList(items) {
  const list = document.getElementById("objectList");
  const visible = ["River", "Watershed", "HydrodynamicBoundary", "County", "Town", "HydrodynamicCell", "ForecastResult", "HydroStation", "Road", "Reservoir", "Sluice", "Bridge", "HydraulicStructure", "Risk", "Place", "Route"];
  list.innerHTML = "";

  visible.forEach((objectType) => {
    const item = items.find((entry) => entry.object_type === objectType) || {};
    const config = OBJECT_CONFIG[objectType];
    if (!config) return;
    const btn = document.createElement("button");
    btn.className = "object-row";
    btn.dataset.objectType = objectType;
    btn.innerHTML = `
      <span class="swatch ${config.swatch === "line" ? "line" : config.swatch === "fill" ? "fill" : ""}" style="color:${config.color}"></span>
      <span>${config.label}</span>
      <span class="count">${item.feature_count ?? ""}</span>
    `;
    btn.addEventListener("click", () => toggleObject(objectType));
    list.appendChild(btn);
  });
}

function bindEvents() {
  document.getElementById("fitAllBtn").addEventListener("click", fitAll);
  document.getElementById("layerPanelBtn").addEventListener("click", toggleLayerPanel);
  document.getElementById("agentDrawerBtn").addEventListener("click", () => setAgentDrawerOpen(true));
  document.getElementById("agentCloseBtn").addEventListener("click", () => setAgentDrawerOpen(false));
  document.getElementById("mockToggleBtn").addEventListener("click", toggleMockService);
  document.getElementById("hydroPlayBtn").addEventListener("click", toggleHydrodynamicTimelinePlayback);
  document.getElementById("hydroTimeSlider").addEventListener("input", (event) => {
    setHydrodynamicTimelineIndex(Number(event.target.value || 0));
  });
  document.querySelectorAll("[data-basemap]").forEach((button) => {
    button.addEventListener("click", () => setBasemap(button.dataset.basemap));
  });
  document.querySelectorAll("[data-panel-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => activateAgentPane(btn.dataset.panelToggle));
  });
  document.getElementById("chatInput").addEventListener("focus", () => {
    setAgentDrawerOpen(true);
    activateAgentPane("chat");
  });
  document.querySelectorAll("[data-facility]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const type = btn.dataset.facility;
      const labels = { school: "学校", hospital: "医院", government: "政府机构" };
      loadObject("Facility", { facility_type: type }, { fit: true, label: labels[type] });
    });
  });
  document.getElementById("chatForm").addEventListener("submit", onChatSubmit);
}

function toggleLayerPanel() {
  const control = document.querySelector(".map-layer-control");
  const btn = document.getElementById("layerPanelBtn");
  const isOpen = !control.classList.contains("is-open");
  control.classList.toggle("is-open", isOpen);
  btn.classList.toggle("is-active", isOpen);
  btn.setAttribute("aria-expanded", String(isOpen));
}

function setAgentDrawerOpen(isOpen) {
  const panel = document.querySelector(".agent-panel");
  const btn = document.getElementById("agentDrawerBtn");
  panel.classList.toggle("is-open", isOpen);
  btn.classList.toggle("is-open", isOpen);
  btn.setAttribute("aria-expanded", String(isOpen));
}

async function toggleObject(objectType) {
  if (objectType === "HydrodynamicCell") {
    const key = layerKey("HydrodynamicCell", { result: "mesh" });
    if (state.layerGroups.has(key)) {
      removeLayer(key);
      return;
    }
    clearHydrodynamicResults();
    await showHydrodynamicMesh({
      fit: true,
    });
    return;
  }
  if (objectType === "ForecastResult") {
    const key = layerKey("HydrodynamicResult", { forecast_id: "latest" });
    if (state.layerGroups.has(key)) {
      removeLayer(key);
      return;
    }
    await showHydrodynamicMesh({ fit: false });
    await applyHydrodynamicResult({
      filters: { forecast_id: "latest" },
      label: OBJECT_CONFIG[objectType].label,
      buttonType: objectType,
    });
    return;
  }
  const filters = defaultObjectFilters(objectType);
  const key = layerKey(objectType, filters);
  if (state.layerGroups.has(key)) {
    removeLayer(key);
    return;
  }
  await loadObject(objectType, filters, { fit: false });
}

function defaultObjectFilters(objectType) {
  if (objectType === "ForecastCell") return { forecast_id: "latest" };
  return {};
}

async function loadObject(objectType, filters = {}, options = {}) {
  if (objectType === "HydrodynamicCell") {
    if (filters && Object.keys(filters).some((key) => ["forecast_id"].includes(key))) {
      throw new Error("Hydrodynamic results must use apply_hydrodynamic_result.");
    }
    return showHydrodynamicMesh(options);
  }
  const resolvedFilters = filtersWithObjectIds(objectType, filters, options.objectIds || options.object_ids || []);
  if (options.replaceObjectType || options.replace_object_type) {
    removeObjectTypeLayers(objectType);
  }
  const key = layerKey(objectType, resolvedFilters);
  if (options.refresh && state.layerGroups.has(key)) {
    removeLayer(key);
  }
  if (state.layerGroups.has(key)) {
    const existing = state.layerGroups.get(key);
    if (!state.map.hasLayer(existing)) existing.addTo(state.map);
    setObjectButtonActive(objectType, true);
    if (options.fit) fitLayer(existing);
    return existing;
  }

  const params = new URLSearchParams({ object_type: objectType });
  params.set("filters", JSON.stringify(resolvedFilters));
  if (options.simplify_tolerance) params.set("simplify_tolerance", options.simplify_tolerance);

  const res = await fetch(`/api/geojson?${params.toString()}`);
  if (!res.ok) throw new Error(await res.text());
  const geojson = await res.json();
  const mapSelectable = !MAP_NON_SELECTABLE_OBJECTS.has(objectType);
  const layer = L.geoJSON(geojson, {
    interactive: mapSelectable,
    style: (feature) => featureStyle(objectType, feature),
    pointToLayer: (feature, latlng) => pointLayer(objectType, feature, latlng),
    onEachFeature: (feature, layerItem) => {
      if (!mapSelectable) return;
      indexFeature(objectType, feature, layerItem);
      layerItem.bindPopup(popupHtml(objectType, feature));
      layerItem.on("click", () => selectFeature(objectType, feature, layerItem));
    },
  }).addTo(state.map);
  renderIcons();

  state.layerGroups.set(key, layer);
  state.layerMeta.set(key, { objectType, filters: resolvedFilters, label: options.label || OBJECT_CONFIG[objectType]?.label || objectType });
  setObjectButtonActive(objectType, true);
  if (objectType === "Watershed") state.baseBounds = layer.getBounds();
  if (options.fit) fitLayer(layer);
  return layer;
}

async function showHydrodynamicMesh(options = {}) {
  const objectType = "HydrodynamicCell";
  const resultFilters = { result: "mesh" };
  const key = layerKey(objectType, resultFilters);
  if (options.meshOnly) clearHydrodynamicResults();
  if (options.refresh && state.layerGroups.has(key)) removeLayer(key);
  if (state.layerGroups.has(key)) {
    const existing = state.layerGroups.get(key);
    if (!state.map.hasLayer(existing)) existing.addTo(state.map);
    setObjectButtonActive(objectType, true);
    if (options.fit) fitHydrodynamicGrid();
    return existing;
  }

  const metaParams = new URLSearchParams(resultFilters);
  const metaRes = await fetch(`/api/hydrodynamic-grid/meta?${metaParams.toString()}`);
  if (!metaRes.ok) throw new Error(await metaRes.text());
  state.hydrodynamicGridMeta = await metaRes.json();
  const layer = L.gridLayer.hydrodynamicGrid({
    tileSize: 256,
    opacity: 1,
    pane: "overlayPane",
    resultFilters,
    renderMode: "mesh",
    minTileZoom: Math.max(state.hydrodynamicGridMeta?.min_tile_zoom || 13, 15),
  }).addTo(state.map);
  state.layerGroups.set(key, layer);
  state.layerMeta.set(key, {
    objectType,
    buttonType: "HydrodynamicCell",
    filters: resultFilters,
    label: options.label || OBJECT_CONFIG[objectType].label,
  });
  setObjectButtonActive(objectType, true);
  if (options.fit) fitHydrodynamicGrid();
  return layer;
}

async function applyHydrodynamicResult(options = {}) {
  const filters = options.filters || {};
  if (!Object.keys(filters).length) throw new Error("apply_hydrodynamic_result requires filters.");
  const key = layerKey("HydrodynamicResult", filters);
  if (options.refresh && state.layerGroups.has(key)) removeLayer(key);
  if (state.layerGroups.has(key)) {
    const existing = state.layerGroups.get(key);
    if (!state.map.hasLayer(existing)) existing.addTo(state.map);
    showHydrodynamicTimeline(state.hydrodynamicResultMeta, existing, key, filters);
    setObjectButtonActive(options.buttonType || "ForecastResult", true);
    return existing;
  }

  const metaParams = new URLSearchParams(filters);
  const metaRes = await fetch(`/api/hydrodynamic-grid/meta?${metaParams.toString()}`);
  if (!metaRes.ok) throw new Error(await metaRes.text());
  state.hydrodynamicResultMeta = await metaRes.json();
  const layer = L.gridLayer.hydrodynamicGrid({
    tileSize: 256,
    opacity: 1,
    pane: "overlayPane",
    resultFilters: filters,
    renderMode: "result",
    wetOnly: true,
    interactiveCells: true,
    minTileZoom: state.hydrodynamicResultMeta?.min_tile_zoom || 13,
  }).addTo(state.map);
  state.layerGroups.set(key, layer);
  state.layerMeta.set(key, {
    objectType: "HydrodynamicResult",
    buttonType: options.buttonType || "ForecastResult",
    filters,
    label: options.label || "水动力结果",
  });
  showHydrodynamicTimeline(state.hydrodynamicResultMeta, layer, key, filters);
  setObjectButtonActive(options.buttonType || "ForecastResult", true);
  return layer;
}

function showHydrodynamicTimeline(meta, layer, key, filters) {
  const hours = (((meta || {}).forecast || {}).time_steps_h || [])
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value));
  if (hours.length < 2) {
    hideHydrodynamicTimeline();
    return;
  }
  stopHydrodynamicTimelinePlayback();
  state.hydrodynamicTimeline = {
    ...state.hydrodynamicTimeline,
    hours,
    index: 0,
    layer,
    key,
    baseFilters: { ...(filters || {}) },
  };
  const control = document.getElementById("hydroTimeline");
  const slider = document.getElementById("hydroTimeSlider");
  slider.min = "0";
  slider.max = String(hours.length);
  slider.value = "0";
  control.classList.remove("is-hidden");
  setHydrodynamicTimelineIndex(0);
}

function hideHydrodynamicTimeline() {
  stopHydrodynamicTimelinePlayback();
  state.hydrodynamicTimeline.hours = [];
  state.hydrodynamicTimeline.index = 0;
  state.hydrodynamicTimeline.layer = null;
  state.hydrodynamicTimeline.key = null;
  state.hydrodynamicTimeline.baseFilters = null;
  document.getElementById("hydroTimeline")?.classList.add("is-hidden");
}

function setHydrodynamicTimelineIndex(index) {
  const timeline = state.hydrodynamicTimeline;
  if (!timeline.layer || !timeline.hours.length) return;
  const nextIndex = Math.max(0, Math.min(timeline.hours.length, Math.round(index)));
  timeline.index = nextIndex;
  const slider = document.getElementById("hydroTimeSlider");
  const label = document.getElementById("hydroTimeLabel");
  if (slider) slider.value = String(nextIndex);
  const filters = { ...(timeline.baseFilters || {}) };
  if (nextIndex === 0) {
    label.textContent = "最大水深";
  } else {
    const hour = timeline.hours[nextIndex - 1];
    filters.time_h = formatHydrodynamicHour(hour);
    label.textContent = `${formatHydrodynamicHour(hour)} h`;
  }
  timeline.layer.options.resultFilters = filters;
  timeline.layer.clearSelection?.();
  timeline.layer.redraw();
  scheduleImpactAnalysisRefresh();
}

function toggleHydrodynamicTimelinePlayback() {
  const timeline = state.hydrodynamicTimeline;
  if (!timeline.layer || !timeline.hours.length) return;
  if (timeline.playing) {
    stopHydrodynamicTimelinePlayback();
    return;
  }
  timeline.playing = true;
  setHydrodynamicPlayIcon(true);
  timeline.timer = window.setInterval(() => {
    const next = timeline.index >= timeline.hours.length ? 1 : timeline.index + 1;
    setHydrodynamicTimelineIndex(next);
  }, 850);
}

function stopHydrodynamicTimelinePlayback() {
  const timeline = state.hydrodynamicTimeline;
  if (timeline.timer) window.clearInterval(timeline.timer);
  timeline.timer = null;
  timeline.playing = false;
  setHydrodynamicPlayIcon(false);
}

function setHydrodynamicPlayIcon(playing) {
  const btn = document.getElementById("hydroPlayBtn");
  if (!btn) return;
  btn.innerHTML = `<i data-lucide="${playing ? "pause" : "play"}"></i>`;
  btn.title = playing ? "暂停预测过程" : "播放预测过程";
  btn.setAttribute("aria-label", btn.title);
  renderIcons();
}

function formatHydrodynamicHour(hour) {
  return Number(hour).toFixed(2).replace(/\.?0+$/, "");
}

function fitHydrodynamicGrid() {
  const bbox = state.hydrodynamicGridMeta?.bbox;
  if (!bbox) return;
  const bounds = L.latLngBounds(
    [bbox.min_lat, bbox.min_lon],
    [bbox.max_lat, bbox.max_lon],
  );
  state.map.flyToBounds(bounds.pad(0.06), {
    animate: true,
    duration: 0.85,
    easeLinearity: 0.22,
    maxZoom: 13,
  });
}

function fitHydrodynamicResult() {
  const bbox = state.hydrodynamicResultMeta?.bbox || state.hydrodynamicGridMeta?.bbox;
  if (!bbox) return;
  const bounds = L.latLngBounds(
    [bbox.min_lat, bbox.min_lon],
    [bbox.max_lat, bbox.max_lon],
  );
  state.map.flyToBounds(bounds.pad(0.06), {
    animate: true,
    duration: 0.85,
    easeLinearity: 0.22,
    maxZoom: 13,
  });
}

function removeLayer(key) {
  const layer = state.layerGroups.get(key);
  const meta = state.layerMeta.get(key);
  if (meta?.objectType === "HydrodynamicResult" && state.hydrodynamicTimeline.key === key) {
    hideHydrodynamicTimeline();
  }
  if (meta && !["HydrodynamicCell", "HydrodynamicResult"].includes(meta.objectType)) unindexLayer(meta.objectType, layer);
  if (layer) state.map.removeLayer(layer);
  state.layerGroups.delete(key);
  state.layerMeta.delete(key);
  if (meta) setObjectButtonActive(meta.buttonType || meta.objectType, hasLayerButtonType(meta.buttonType || meta.objectType));
}

function removeObjectTypeLayers(objectType) {
  Array.from(state.layerMeta.entries()).forEach(([key, meta]) => {
    if (meta?.objectType === objectType) removeLayer(key);
  });
}

function filtersWithObjectIds(objectType, filters = {}, objectIds = []) {
  const ids = (objectIds || []).map(String).filter(Boolean);
  if (!ids.length) return { ...(filters || {}) };
  const idField = ID_FIELDS[objectType];
  if (!idField) return { ...(filters || {}) };
  return {
    ...(filters || {}),
    [`${idField}__in`]: Array.from(new Set(ids)),
  };
}

function resetMap() {
  clearFocus();
  clearHighlights();
  clearImpactAnalysisState();
  clearEventMarkers();
  clearBoundaryFlowLayer();
  for (const key of Array.from(state.layerGroups.keys())) {
    const meta = state.layerMeta.get(key);
    if (!["Watershed", "County"].includes(meta?.objectType)) {
      removeLayer(key);
    }
  }
  document.getElementById("contextPill").textContent = "基础态 · 领域对象地图";
  fitAll();
}

function clearHydrodynamicResults() {
  hideHydrodynamicTimeline();
  clearImpactAnalysisState();
  for (const [key, meta] of Array.from(state.layerMeta.entries())) {
    if (meta?.objectType === "HydrodynamicResult") {
      removeLayer(key);
    }
  }
  document.getElementById("contextPill").textContent = "淹没结果 · 已隐藏";
}

function clearImpactAnalysisState() {
  if (state.impactRefreshTimer) window.clearTimeout(state.impactRefreshTimer);
  state.impactRefreshTimer = null;
  state.impactRefreshSeq += 1;
  state.impactAnalysis = null;
  state.pendingImpactObjectTypes.clear();
}

function clearEventMarkers() {
  state.eventMarkers.forEach((marker) => state.map.removeLayer(marker));
  state.eventMarkers.clear();
}

function clearBoundaryFlowLayer() {
  if (state.boundaryFlowLayer) {
    state.map.removeLayer(state.boundaryFlowLayer);
    state.boundaryFlowLayer = null;
  }
}

function startAutonomyStream() {
  if (state.autonomyStream) state.autonomyStream.close();
  const es = new EventSource("/api/autonomy/stream?interval=5");
  state.autonomyStream = es;

  es.addEventListener("runtime_status", (event) => {
    const data = parseEvent(event);
    if (["等待水文事件", "等待边界流量事件", "等待启动 mock 服务"].includes(data.label)) return;
    if (data.status === "running" || data.label === "边界流量 mock 服务已启动") setMockButtonState(true);
    if (["stopped", "finished"].includes(data.status) || data.label === "边界流量 mock 服务已停止") {
      setMockButtonState(false);
    }
    addTrace("AUTO", data.label || "事件运行时", data.detail || "");
  });

  es.addEventListener("domain_event", (event) => {
    const data = parseEvent(event);
    renderDomainEvent(data);
  });

  es.addEventListener("boundary_flow_data", (event) => {
    const data = parseEvent(event);
    const domainEvent = data.event || {};
    addTrace("FLOW", data.label || "四边界流量数据", data.detail || eventDetail(domainEvent));
    if (domainEvent.event_type === "BoundaryFlowSeriesGenerated") {
      renderBoundaryFlowLayer(domainEvent).catch((error) => console.warn("boundary flow render failed", error));
    }
  });

  es.addEventListener("agent_trace", (event) => {
    const data = parseEvent(event);
    if (shouldHideAutonomyTrace(data)) return;
    addTrace(data.tag || "AGENT", data.label || "智能体事件处理", data.detail || "");
  });

  es.addEventListener("map_actions", async (event) => {
    const data = parseEvent(event);
    try {
      if (data.context) document.getElementById("contextPill").textContent = data.context;
      await executeActions(data.map_actions || []);
      renderMetrics(data.result_cards || []);
      addTrace("MAP", "地图动作", (data.map_actions || []).map((item) => item.object_type || item.type).join(", "));
    } catch (error) {
      addTrace("ERR", "地图动作执行失败", error.message || String(error));
    }
  });

  es.onerror = () => {
    addTrace("AUTO", "闭环流断开", "5 秒后尝试重连。");
    es.close();
    state.autonomyStream = null;
    window.setTimeout(startAutonomyStream, 5000);
  };
}

async function refreshMockStatus() {
  try {
    const res = await fetch("/api/autonomy/status");
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    setMockButtonState(Boolean(data.running));
  } catch (error) {
    console.warn("mock status failed", error);
    setMockButtonState(false);
  }
}

async function toggleMockService() {
  const nextRunning = !state.mockRunning;
  const btn = document.getElementById("mockToggleBtn");
  btn.disabled = true;
  try {
    if (nextRunning) resetMap();
    const res = await fetch(nextRunning ? "/api/autonomy/start" : "/api/autonomy/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    setMockButtonState(Boolean(data.running));
    addTrace(
      "AUTO",
      data.running ? "边界流量 mock 服务已启动" : "边界流量 mock 服务已停止",
      data.running ? "后台开始生成四边界流量数据。" : "后台已停止生成新的边界流量数据。",
    );
  } catch (error) {
    addTrace("ERR", "mock 服务切换失败", error.message || String(error));
    setMockButtonState(state.mockRunning);
  } finally {
    btn.disabled = false;
  }
}

function setMockButtonState(running) {
  state.mockRunning = running;
  const btn = document.getElementById("mockToggleBtn");
  if (!btn) return;
  btn.classList.toggle("is-running", running);
  btn.setAttribute("aria-pressed", String(running));
  btn.title = running ? "停止边界流量 mock 服务" : "启动边界流量 mock 服务";
  btn.innerHTML = running
    ? '<i data-lucide="pause"></i><span>停止 Mock</span>'
    : '<i data-lucide="play"></i><span>启动 Mock</span>';
  renderIcons();
}

function renderDomainEvent(data) {
  if (!data || !data.event_type) return;
  if (data.event_type === "ImpactAnalyzed") {
    registerImpactAnalysisResult(data.payload || null, { preserveVisibleTypes: false });
  }
  const tag = data.event_type === "BoundaryFlowSeriesGenerated" ? "ALERT" : (data.event_type === "HydroThresholdExceeded" ? "HYDRO" : "EVENT");
  const label = data.event_type === "BoundaryFlowSeriesGenerated" ? "警戒事件进入智能体" : (data.title || data.event_type);
  addTrace(tag, label, eventDetail(data));
  setCyclePhase(eventPhase(data.event_type));
}

function eventDetail(data) {
  const payload = data.payload || {};
  if (data.event_type === "BoundaryFlowSeriesGenerated") {
    const flow = payload.boundary_flow || {};
    return boundaryFlowSummary(flow);
  }
  if (data.event_type === "HydroThresholdExceeded") {
    return `${payload.station_name || data.source_id}: ${payload.metric_label || payload.metric} ${payload.value} ${payload.unit} / 阈值 ${payload.threshold} ${payload.unit}`;
  }
  if (data.event_type === "InundationGenerated") {
    return `预测单元 ${payload.forecast_cell_count || 0} 个，淹没面积 ${(Number(payload.inundated_area_km2 || 0)).toFixed(2)} km²`;
  }
  if (data.event_type === "ImpactAnalyzed") {
    const summary = payload.summary || {};
    const labels = { Facility: "设施", Bridge: "桥梁", Road: "道路", Route: "路线", Transfer: "转移单元", Place: "安置点" };
    const parts = Object.keys(labels).map((key) => {
      const count = Number((summary[key] || {}).count || 0);
      return count ? `${labels[key]} ${count} 个` : "";
    }).filter(Boolean);
    return parts.length ? parts.join("，") : "未识别到受预测淹没影响的对象";
  }
  return data.severity || "";
}

function boundaryFlowSummary(flow) {
  const boundaries = flow.boundaries || {};
  const labels = ["interval1", "interval2", "tonggu", "upstream"].map((key) => {
    const item = boundaries[key] || {};
    if (!item.label) return "";
    return `${item.label}${Number(item.peak_flow_m3s || 0).toFixed(1)}m³/s`;
  }).filter(Boolean);
  return `${flow.boundary_flow_id || "boundary_flow"} ${labels.join("，")}`;
}

async function renderBoundaryFlowLayer(event) {
  const payload = event.payload || {};
  const flow = payload.boundary_flow || {};
  const boundaries = flow.boundaries || {};
  const features = await getModelBoundaryFeatures();
  clearBoundaryFlowLayer();

  const layer = L.layerGroup();
  const maxFlow = Math.max(
    1,
    ...Object.values(boundaries).flatMap((item) => (item.series || []).map((point) => Number(point.flow_m3s || 0))),
  );

  ["interval1", "interval2", "tonggu", "upstream"].forEach((key) => {
    const item = boundaries[key];
    const feature = features.find((entry) => entry.properties?.boundary_group === key);
    if (!item || !feature) return;
    const center = featureCenter(feature);
    if (!center) return;

    const color = boundaryFlowColor(key);
    const marker = L.circleMarker(center, {
      radius: 6,
      color: "#ffffff",
      weight: 2,
      fillColor: color,
      fillOpacity: 0.96,
      className: "boundary-flow-marker",
    });
    marker.bindTooltip(`${item.label} 峰值 ${Number(item.peak_flow_m3s || 0).toFixed(1)} m³/s`, {
      direction: "top",
      offset: [0, -8],
    });
    marker.bindPopup(boundaryFlowPopupHtml(item, flow));
    marker.addTo(layer);

    L.marker(center, {
      interactive: true,
      icon: L.divIcon({
        className: "boundary-flow-chart-icon",
        html: boundaryFlowChartHtml(key, item, maxFlow),
        iconSize: [178, 86],
        iconAnchor: [-12, 72],
      }),
    }).addTo(layer);
  });

  layer.addTo(state.map);
  state.boundaryFlowLayer = layer;
}

async function getModelBoundaryFeatures() {
  if (state.boundaryFlowFeatures) return state.boundaryFlowFeatures;
  const params = new URLSearchParams({
    object_type: "HydrodynamicBoundary",
    is_model_input_boundary: "true",
  });
  const res = await fetch(`/api/geojson?${params.toString()}`);
  if (!res.ok) throw new Error(await res.text());
  const geojson = await res.json();
  state.boundaryFlowFeatures = geojson.features || [];
  return state.boundaryFlowFeatures;
}

function featureCenter(feature) {
  const coords = collectCoordinates(feature.geometry?.coordinates || []);
  if (!coords.length) return null;
  const mid = coords[Math.floor(coords.length / 2)];
  return [mid[1], mid[0]];
}

function collectCoordinates(value) {
  if (!Array.isArray(value)) return [];
  if (value.length >= 2 && typeof value[0] === "number" && typeof value[1] === "number") {
    return [[value[0], value[1]]];
  }
  return value.flatMap((item) => collectCoordinates(item));
}

function boundaryFlowChartHtml(key, item, maxFlow) {
  const series = item.series || [];
  const color = boundaryFlowColor(key);
  const points = sparklinePoints(series, maxFlow, 142, 40);
  const peak = Number(item.peak_flow_m3s || 0).toFixed(1);
  const label = escapeHtml(item.label || key);
  return `
    <div class="boundary-flow-chart" style="--flow-color:${color}">
      <div class="flow-chart-head">
        <span>${label}</span>
        <strong>${peak}</strong>
      </div>
      <svg viewBox="0 0 154 48" aria-hidden="true">
        <path class="flow-chart-grid" d="M6 8 H148 M6 24 H148 M6 40 H148" />
        <polyline class="flow-chart-line" points="${points}" />
      </svg>
      <div class="flow-chart-unit">m³/s</div>
    </div>
  `;
}

function sparklinePoints(series, maxFlow, width, height) {
  if (!series.length) return "";
  const maxTime = Math.max(...series.map((point) => Number(point.time_h || 0)), 1);
  return series.map((point) => {
    const x = 6 + (Number(point.time_h || 0) / maxTime) * width;
    const y = 6 + height - (Number(point.flow_m3s || 0) / maxFlow) * height;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function boundaryFlowColor(key) {
  return {
    interval1: "#b7791f",
    interval2: "#c05621",
    tonggu: "#047481",
    upstream: "#b42318",
  }[key] || "#475569";
}

function boundaryFlowPopupHtml(item, flow) {
  return `
    <div class="popup-title">${escapeHtml(item.label || "边界流量")}</div>
    <div class="popup-meta">数据编号: ${escapeHtml(flow.boundary_flow_id || "")}</div>
    <div class="popup-meta">峰值: ${escapeHtml(Number(item.peak_flow_m3s || 0).toFixed(2))} m³/s</div>
    <div class="popup-meta">均值: ${escapeHtml(Number(item.mean_flow_m3s || 0).toFixed(2))} m³/s</div>
  `;
}

function eventPhase(eventType) {
  return {
    BoundaryFlowSeriesGenerated: "observe",
    HydroThresholdExceeded: "observe",
    InundationGenerated: "compute",
    ImpactAnalyzed: "analyze",
    ExposureAnalyzed: "decide",
  }[eventType] || "analyze";
}

function renderAutonomyEvent(data) {
  if (!data || !data.phase) return;
  state.autonomyPhase = data.phase;
  document.getElementById("contextPill").textContent = phaseContext(data.phase);
  setCyclePhase(data.phase);
  addTrace(data.tag || "AUTO", data.label || "自动闭环", data.detail || "");
  if (Array.isArray(data.metrics)) renderMetrics(data.metrics);
}

function setCyclePhase(phase) {
  if (!document.getElementById("cycleStrip")) return;
  const order = ["observe", "analyze", "compute", "decide", "monitor"];
  const current = order.indexOf(phase);
  document.querySelectorAll("#cycleStrip [data-phase]").forEach((item) => {
    const index = order.indexOf(item.dataset.phase);
    item.classList.toggle("active", index === current);
    item.classList.toggle("done", current >= 0 && index < current);
  });
}

function renderMetrics(items) {
  const grid = document.getElementById("metricGrid");
  if (!grid) return;
  grid.innerHTML = "";
  (items || []).slice(0, 6).forEach((item) => {
    const card = document.createElement("div");
    card.className = "metric-card";
    card.innerHTML = `
      <div class="metric-label">${escapeHtml(item.label || item.title || "")}</div>
      <div class="metric-value">${escapeHtml(String(item.value ?? ""))}</div>
    `;
    if (item.detail) card.title = item.detail;
    grid.appendChild(card);
  });
}

function phaseContext(phase) {
  return {
    observe: "自动闭环 · 感知水文",
    analyze: "自动闭环 · 态势分析",
    compute: "自动闭环 · 水动力计算",
    decide: "自动闭环 · 预警决策",
    monitor: "自动闭环 · 持续监测",
  }[phase] || "自动闭环 · 珊瑚河流域";
}

function fitAll() {
  if (state.baseBounds && state.baseBounds.isValid()) {
    state.map.fitBounds(state.baseBounds.pad(0.08));
    return;
  }
  const bounds = [];
  state.layerGroups.forEach((layer) => {
    const b = layer.getBounds?.();
    if (b?.isValid()) bounds.push(b);
  });
  if (bounds.length) state.map.fitBounds(bounds.reduce((acc, b) => acc.extend(b), bounds[0]).pad(0.08));
}

function fitLayer(layer) {
  const bounds = layer.getBounds?.();
  if (bounds?.isValid()) state.map.fitBounds(bounds.pad(0.08));
}

function fitFeatureLayer(layer) {
  const bounds = layer.getBounds?.();
  if (bounds?.isValid()) {
    state.map.flyToBounds(bounds.pad(0.35), {
      animate: true,
      duration: 0.85,
      easeLinearity: 0.22,
      maxZoom: 16,
    });
    return;
  }
  const latlng = layer.getLatLng?.();
  if (latlng) {
    state.map.flyTo(latlng, Math.max(state.map.getZoom(), 15), {
      animate: true,
      duration: 0.85,
      easeLinearity: 0.22,
    });
  }
}

function featureStyle(objectType, feature) {
  if (objectType === "ForecastCell") {
    const depth = Number(feature.properties?.depth_m || feature.properties?.YMSS || 0);
    const color = depth > 1.2 ? "#7f1d1d" : depth > 0.6 ? "#dc2626" : "#fecaca";
    return { color, weight: 0.5, fillColor: color, fillOpacity: 0.34 };
  }
  if (objectType === "HydrodynamicCell") {
    const depth = Number(feature.properties?.depth_m || 0);
    return hydrodynamicCellStyle(depth);
  }
  if (objectType === "Watershed") return { color: "#1f2937", weight: 1.3, fillColor: "#9bc4df", fillOpacity: 0.1 };
  if (objectType === "River") return { color: "#0e7490", weight: 4, opacity: 0.95 };
  if (objectType === "HydrodynamicBoundary") return {
    color: boundaryColor(feature),
    weight: boundaryWeight(feature),
    opacity: boundaryOpacity(feature),
    dashArray: boundaryDash(feature),
    lineCap: "round",
    lineJoin: "round",
  };
  if (objectType === "County") return { color: "#7b8794", weight: 1.2, fillOpacity: 0 };
  if (objectType === "Town") return { color: "#7a6a22", weight: 1, fillColor: "#facc15", fillOpacity: 0.08 };
  if (objectType === "Road") return { color: "#5f6772", weight: 2, opacity: 0.82 };
  if (objectType === "Route") return { color: "#d44a3a", weight: 3, opacity: 0.92 };
  if (objectType === "HydraulicStructure") return { color: "#0f766e", weight: 2, opacity: 0.9 };
  return { color: OBJECT_CONFIG[objectType]?.color || "#334155", weight: 2 };
}

function pointStyle(objectType, feature) {
  let color = OBJECT_CONFIG[objectType]?.color || "#334155";
  if (objectType === "Facility") {
    const type = feature.properties?.facility_type;
    color = type === "school" ? "#d44a3a" : type === "hospital" ? "#b91c1c" : "#7c3aed";
  }
  if (objectType === "Place") color = "#24895d";
  if (objectType === "Transfer") color = "#c97a12";
  if (objectType === "Risk") color = "#b91c1c";
  return {
    radius: pointRadius(objectType),
    color: "#ffffff",
    weight: pointStrokeWeight(objectType),
    fillColor: color,
    fillOpacity: objectType === "Risk" ? 0.78 : 0.88,
  };
}

function pointLayer(objectType, feature, latlng) {
  if (!ICON_OBJECT_TYPES.has(objectType)) {
    return L.circleMarker(latlng, pointStyle(objectType, feature));
  }
  const marker = L.marker(latlng, {
    icon: objectDivIcon(objectType, feature),
    interactive: true,
    riseOnHover: true,
  });
  marker.isObjectIconMarker = true;
  return marker;
}

function objectDivIcon(objectType, feature) {
  const info = objectIconInfo(objectType, feature);
  return L.divIcon({
    className: `object-symbol-marker object-symbol-${info.key}`,
    html: `<span class="object-symbol-inner" title="${escapeHtml(info.label)}" aria-label="${escapeHtml(info.label)}">${escapeHtml(info.emoji)}</span>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
    popupAnchor: [0, -14],
  });
}

function objectIconInfo(objectType, feature) {
  const props = feature?.properties || {};
  if (objectType === "Facility") {
    const type = props.facility_type || "facility";
    return {
      school: { key: "school", emoji: "🏫", label: "学校" },
      hospital: { key: "hospital", emoji: "🏥", label: "医院" },
      government: { key: "government", emoji: "🏛️", label: "政府机构" },
    }[type] || { key: "facility", emoji: "🏢", label: "重要设施" };
  }
  return {
    Reservoir: { key: "reservoir", emoji: "🌊", label: "水库" },
    Sluice: { key: "sluice", emoji: "🚪", label: "水闸" },
    Bridge: { key: "bridge", emoji: "🌉", label: "桥梁" },
    Place: { key: "place", emoji: "🏠", label: "安置地点" },
    Transfer: { key: "transfer", emoji: "👥", label: "转移对象" },
    Risk: { key: "risk", emoji: "⚠️", label: "危险区" },
    HydroStation: { key: "station", emoji: "📡", label: "水文测站" },
  }[objectType] || { key: "default", emoji: "📍", label: OBJECT_CONFIG[objectType]?.label || objectType };
}

function boundaryColor(feature) {
  if (!isModelInputBoundary(feature)) return "#64748b";
  const role = feature?.properties?.boundary_role || "";
  return {
    upstream_inflow: "#b42318",
    lateral_inflow: "#b7791f",
    tributary_inflow: "#047481",
    downstream_water_level: "#1d4ed8",
  }[role] || "#475569";
}

function boundaryWeight(feature) {
  return isModelInputBoundary(feature) ? 4.2 : 1.4;
}

function boundaryOpacity(feature) {
  return isModelInputBoundary(feature) ? 0.95 : 0.48;
}

function boundaryDash(feature) {
  return isModelInputBoundary(feature) ? "" : "5 6";
}

function isModelInputBoundary(feature) {
  return feature?.properties?.is_model_input_boundary === true;
}

function pointRadius(objectType) {
  return {
    Risk: 2.2,
    Place: 2.6,
    Transfer: 3.0,
    Bridge: 3.4,
    Sluice: 3.6,
    Reservoir: 3.8,
    Facility: 3.6,
    HydroStation: 4.0,
  }[objectType] || 3.4;
}

function pointStrokeWeight(objectType) {
  return ["Risk", "Place", "Transfer"].includes(objectType) ? 0.9 : 1.2;
}

function popupHtml(objectType, feature) {
  const props = feature.properties || {};
  const name = props.name || props[ID_FIELDS[objectType]] || OBJECT_CONFIG[objectType]?.label || objectType;
  const id = props[ID_FIELDS[objectType]] || "";
  return `
    <div class="popup-title">${escapeHtml(name)}</div>
    <div class="popup-meta">${escapeHtml(OBJECT_CONFIG[objectType]?.label || objectType)} ${escapeHtml(id)}</div>
  `;
}

function selectFeature(objectType, feature, layerItem) {
  const props = feature.properties || {};
  const idField = ID_FIELDS[objectType];
  state.selected = {
    object_type: objectType,
    id: props[idField],
    name: props.name || props[idField],
  };
  document.getElementById("selectedObject").innerHTML = detailHtml(objectType, props);
  applyFocus(layerItem, objectType);
  layerItem.openPopup();
}

function indexFeature(objectType, feature, layerItem) {
  const idField = ID_FIELDS[objectType];
  const objectId = feature.properties?.[idField];
  if (!objectId) return;
  state.featureIndex.set(featureIndexKey(objectType, objectId), { objectType, objectId, feature, layer: layerItem });
}

function unindexLayer(objectType, group) {
  if (!group) return;
  group.eachLayer?.((layerItem) => {
    const idField = ID_FIELDS[objectType];
    const objectId = layerItem.feature?.properties?.[idField];
    if (objectId) state.featureIndex.delete(featureIndexKey(objectType, objectId));
    if (state.focusedLayer === layerItem) clearFocus();
  });
}

async function focusObject(action = {}) {
  const selected = state.selected || {};
  const objectType = action.object_type || selected.object_type;
  const objectId = action.object_id || action.id || selected.id;
  if (MAP_NON_SELECTABLE_OBJECTS.has(objectType)) {
    await loadObject(objectType, action.filters || {}, { fit: false, label: action.label });
    return false;
  }
  if (!objectType || !objectId) {
    fitAll();
    return false;
  }

  await loadObject(objectType, action.filters || {}, { fit: false, label: action.label });
  let entry = state.featureIndex.get(featureIndexKey(objectType, objectId));
  if (!entry) {
    for (const [key, value] of state.featureIndex.entries()) {
      if (key.startsWith(`${objectType}:`) && String(value.feature?.properties?.name || "") === String(objectId)) {
        entry = value;
        break;
      }
    }
  }
  if (!entry) {
    addTrace("MISS", "未找到对象", `${objectType} ${objectId}`);
    return false;
  }

  selectFeature(objectType, entry.feature, entry.layer);
  fitFeatureLayer(entry.layer);
  return true;
}

function applyFocus(layerItem, objectType) {
  clearFocus();
  state.focusedLayer = layerItem;
  if (isObjectIconMarker(layerItem)) {
    layerItem.getElement()?.classList.add("is-focused");
    layerItem.setZIndexOffset?.(1000);
    layerItem.bringToFront?.();
    state.focusedOriginalStyle = { objectType, iconMarker: true };
    return;
  }
  const isPoint = Boolean(layerItem.setRadius);
  const radius = pointRadius(objectType) + 1.6;
  const style = isPoint
    ? { radius, color: "#f8fafc", weight: 1.8, fillColor: "#f59e0b", fillOpacity: 0.96 }
    : { color: "#f59e0b", weight: 4, fillColor: "#f59e0b", fillOpacity: 0.28 };
  layerItem.setStyle?.(style);
  layerItem.bringToFront?.();
  state.focusedOriginalStyle = { objectType };
}

function clearFocus() {
  if (!state.focusedLayer) return;
  const objectType = state.focusedOriginalStyle?.objectType;
  if (state.focusedOriginalStyle?.iconMarker) {
    state.focusedLayer.getElement()?.classList.remove("is-focused");
    state.focusedLayer.setZIndexOffset?.(0);
    state.focusedLayer = null;
    state.focusedOriginalStyle = null;
    return;
  }
  const feature = state.focusedLayer.feature || {};
  if (state.focusedLayer.setStyle && objectType) {
    if (state.focusedLayer.setRadius) state.focusedLayer.setRadius(pointStyle(objectType, feature).radius);
    state.focusedLayer.setStyle(state.focusedLayer.setRadius ? pointStyle(objectType, feature) : featureStyle(objectType, feature));
  }
  state.focusedLayer = null;
  state.focusedOriginalStyle = null;
}

function applyHighlight(layerItem, objectType) {
  if (!layerItem) return;
  if (isObjectIconMarker(layerItem)) {
    layerItem.getElement()?.classList.add("is-highlighted");
    layerItem.setZIndexOffset?.(800);
    layerItem.bringToFront?.();
    state.highlightedLayers.push({ layer: layerItem, objectType });
    return;
  }
  const isPoint = Boolean(layerItem.setRadius);
  const radius = pointRadius(objectType) + 1.4;
  if (isPoint) layerItem.setRadius(radius);
  layerItem.setStyle?.(isPoint
    ? { radius, color: "#fff7ed", weight: 1.8, fillColor: "#ea580c", fillOpacity: 0.96 }
    : { color: "#ea580c", weight: 5, fillColor: "#ea580c", fillOpacity: 0.32 });
  layerItem.bringToFront?.();
  state.highlightedLayers.push({ layer: layerItem, objectType });
}

function clearHighlights() {
  state.highlightedLayers.forEach(({ layer, objectType }) => {
    if (isObjectIconMarker(layer)) {
      layer.getElement()?.classList.remove("is-highlighted");
      layer.setZIndexOffset?.(0);
      return;
    }
    const feature = layer.feature || {};
    if (layer.setStyle && objectType) {
      if (layer.setRadius) layer.setRadius(pointStyle(objectType, feature).radius);
      layer.setStyle(layer.setRadius ? pointStyle(objectType, feature) : featureStyle(objectType, feature));
    }
  });
  state.highlightedLayers = [];
}

function isObjectIconMarker(layerItem) {
  return Boolean(layerItem?.isObjectIconMarker);
}

async function highlightObjects(action = {}) {
  const objectType = action.object_type;
  const objectIds = (action.object_ids || []).map(String).filter(Boolean);
  if (MAP_NON_SELECTABLE_OBJECTS.has(objectType)) {
    await loadObject(objectType, action.filters || {}, { fit: false, label: action.label });
    return false;
  }
  if (!objectType || !objectIds.length) return false;
  await loadObject(objectType, action.filters || {}, {
    fit: false,
    label: action.label,
    objectIds,
  });
  objectIds.forEach((objectId) => {
    const entry = state.featureIndex.get(featureIndexKey(objectType, objectId));
    if (entry) applyHighlight(entry.layer, objectType);
  });
  rememberImpactObjectLayer(objectType);
  if (action.fit) fitHighlighted();
  return true;
}

function fitHighlighted() {
  const bounds = [];
  state.highlightedLayers.forEach(({ layer }) => {
    const b = layer.getBounds?.();
    if (b?.isValid()) bounds.push(b);
    const latlng = layer.getLatLng?.();
    if (latlng) bounds.push(L.latLngBounds([latlng]));
  });
  if (bounds.length) {
    state.map.flyToBounds(bounds.reduce((acc, b) => acc.extend(b), bounds[0]).pad(0.35), {
      animate: true,
      duration: 0.85,
      easeLinearity: 0.22,
      maxZoom: 15,
    });
  }
}

function detailHtml(objectType, props) {
  const keys = Object.keys(props).filter((key) => props[key] !== "" && props[key] !== null && key !== "geometry");
  const rows = keys.slice(0, 8).map((key) => `<div><strong>${escapeHtml(key)}</strong>: ${escapeHtml(String(props[key]))}</div>`);
  return `<div class="muted"><strong>${escapeHtml(OBJECT_CONFIG[objectType]?.label || objectType)}</strong>${rows.join("")}</div>`;
}

async function onChatSubmit(event) {
  event.preventDefault();
  activateAgentPane("chat");
  if (state.activeStream) {
    stopActiveRun();
    return;
  }
  const input = document.getElementById("chatInput");
  const message = input.value.trim();
  if (!message) return;

  input.value = "";
  addMessage("user", message);
  const assistant = addMessage("agent", "");
  addTrace("RUN", "Agent 执行中", message);
  connectChatStream({ message, assistant });
}

function connectChatStream({ message = "", assistant, runId = "", since = 0 }) {
  const params = new URLSearchParams({
    session_id: state.sessionId,
    since: String(since || 0),
  });
  if (runId) {
    params.set("run_id", runId);
  } else {
    params.set("message", message);
    params.set("selected", JSON.stringify(frontendAgentContext()));
  }
  const es = new EventSource(`/api/agent/chat/stream?${params.toString()}`);
  state.activeStream = es;
  setSending(true);

  es.addEventListener("run", (event) => {
    const data = parseEvent(event);
    state.activeRunId = data.run_id;
  });

  es.addEventListener("map_actions", async (event) => {
    const data = parseEvent(event);
    if (data.context) document.getElementById("contextPill").textContent = data.context;
    await executeActions(data.map_actions || []);
    addTrace("MAP", "地图动作", (data.map_actions || []).map((item) => item.object_type || item.type).join(", "));
  });

  es.addEventListener("text", (event) => {
    const data = parseEvent(event);
    appendMessageMarkdown(assistant, data.content || "");
    scrollChat();
  });

  es.addEventListener("tool_call", (event) => {
    const data = parseEvent(event);
    addTrace("CALL", readableTool(data.name, data.args || {}), JSON.stringify(data.args || {}, null, 2));
  });

  es.addEventListener("tool_result", (event) => {
    const data = parseEvent(event);
    addTrace(data.blocked ? "BLOCK" : "RESULT", data.name || "tool result", compactText(data.result || ""));
    if (!data.blocked && data.name === "analyze_inundation_impacts") {
      registerImpactAnalysisResult(parseToolJsonResult(data.result), { preserveVisibleTypes: false });
    }
  });

  es.addEventListener("reasoning", () => {});

  es.addEventListener("debug", () => {});

  es.addEventListener("confirmation_required", (event) => {
    const data = parseEvent(event);
    addTrace("ASK", `需要确认: ${data.tool_name}`, JSON.stringify(data.args || {}, null, 2));
    appendConfirmation(data);
    finishStream(false);
  });

  es.addEventListener("question", (event) => {
    const data = parseEvent(event);
    addTrace("ASK", "等待用户输入", data.question || "");
    appendQuestion(data);
    finishStream(false);
  });

  es.addEventListener("done", () => {
    if (!assistant.dataset.rawMarkdown?.trim()) setMessageMarkdown(assistant, "已完成。");
    finishStream(true);
  });

  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) {
      finishStream(false);
      if (!assistant.dataset.rawMarkdown?.trim()) setMessageMarkdown(assistant, "连接已关闭。");
    }
  };
}

function finishStream(clearRun) {
  if (state.activeStream) state.activeStream.close();
  state.activeStream = null;
  if (clearRun) state.activeRunId = null;
  setSending(false);
}

function stopActiveRun() {
  if (state.activeRunId) {
    fetch(`/api/agent/runs/${encodeURIComponent(state.activeRunId)}/cancel`, { method: "POST" }).catch(() => {});
  }
  addTrace("STOP", "已停止", "");
  finishStream(true);
}

async function executeActions(actions) {
  for (const action of actions) {
    if (action.type === "reset") {
      resetMap();
    }
    if (action.type === "clear_hydrodynamic_result") {
      clearHydrodynamicResults();
    }
    if (action.type === "load_object") {
      await loadObject(action.object_type, action.filters || {}, {
        fit: action.fit,
        label: action.label,
        simplify_tolerance: action.simplify_tolerance,
        refresh: action.refresh,
        objectIds: action.object_ids,
        replaceObjectType: action.replace_object_type || action.replaceObjectType,
      });
    }
    if (action.type === "show_hydrodynamic_mesh") {
      await showHydrodynamicMesh({
        fit: action.fit,
        refresh: action.refresh,
        label: action.label,
        meshOnly: action.mesh_only || action.meshOnly,
      });
    }
    if (action.type === "apply_hydrodynamic_result") {
      await applyHydrodynamicResult({
        filters: action.filters || {},
        fit: action.fit,
        refresh: action.refresh,
        label: action.label,
        buttonType: action.button_type || action.buttonType || "ForecastResult",
      });
    }
    if (action.type === "clear_highlights") {
      clearHighlights();
    }
    if (action.type === "highlight_objects") {
      await highlightObjects(action);
    }
    if (action.type === "focus_object") {
      await focusObject(action);
    }
    if (action.type === "focus_selected") {
      await focusObject(action);
    }
    if (action.type === "show_event_marker") {
      showEventMarker(action.event || {}, action);
    }
  }
}

L.GridLayer.HydrodynamicGrid = L.GridLayer.extend({
  onAdd(map) {
    L.GridLayer.prototype.onAdd.call(this, map);
    if (this.options.interactiveCells) map.on("click", this._handleCellClick, this);
  },

  onRemove(map) {
    map.off("click", this._handleCellClick, this);
    this.clearSelection();
    L.GridLayer.prototype.onRemove.call(this, map);
  },

  createTile(coords, done) {
    const tile = document.createElement("canvas");
    const size = this.getTileSize();
    tile.width = size.x;
    tile.height = size.y;
    const minZoom = this.options.minTileZoom || 13;
    if (coords.z < minZoom) {
      window.setTimeout(() => done(null, tile), 0);
      return tile;
    }
    const ctx = tile.getContext("2d");
    const params = new URLSearchParams({
      z: String(coords.z),
      x: String(coords.x),
      y: String(coords.y),
      tile_crs: "gcj02",
    });
    Object.entries(this.options.resultFilters || { result: "mesh" }).forEach(([name, value]) => {
      params.set(name, value);
    });
    if (this.options.wetOnly) {
      params.set("wet_only", "1");
    }
    fetch(`/api/hydrodynamic-grid/tile?${params.toString()}`)
      .then((res) => {
        if (!res.ok) throw new Error(`tile ${res.status}`);
        return res.json();
      })
      .then((data) => {
        tile._hydrodynamicData = data;
        tile._hydrodynamicCoords = { ...coords };
        drawHydrodynamicTile(ctx, size, coords, data, this.options.renderMode || "mesh");
        done(null, tile);
      })
      .catch((error) => {
        console.warn("hydrodynamic grid tile failed", error);
        done(null, tile);
      });
    return tile;
  },

  clearSelection() {
    if (this._selectedCellLayer) {
      this._selectedCellLayer.remove();
      this._selectedCellLayer = null;
    }
    if (this._cellPopup) {
      this._cellPopup.remove();
      this._cellPopup = null;
    }
  },

  _handleCellClick(event) {
    if (hydrodynamicClickHitsAnotherObject(event)) return;
    const cell = this._cellAtLatLng(event.latlng);
    if (!cell || Number(cell.depth || 0) <= 0) {
      this.clearSelection();
      return;
    }
    this.clearSelection();
    this._selectedCellLayer = L.polygon(cell.latlngs, {
      color: "#111827",
      weight: 2,
      opacity: 0.95,
      fill: false,
      interactive: false,
    }).addTo(this._map);
    this._cellPopup = L.popup({
      className: "hydrodynamic-cell-popup",
      closeButton: false,
      offset: [0, -4],
    })
      .setLatLng(event.latlng)
      .setContent(hydrodynamicCellPopupHtml(cell))
      .openOn(this._map);
  },

  _cellAtLatLng(latlng) {
    if (!this._map) return null;
    const zoom = this._map.getZoom();
    const tileSize = this.getTileSize();
    const projected = this._map.project(latlng, zoom);
    const tileCoords = {
      x: Math.floor(projected.x / tileSize.x),
      y: Math.floor(projected.y / tileSize.y),
      z: zoom,
    };
    const tileEntry = this._tiles?.[this._tileCoordsToKey(tileCoords)];
    const data = tileEntry?.el?._hydrodynamicData;
    if (!Array.isArray(data?.cells)) return null;
    for (let index = data.cells.length - 1; index >= 0; index -= 1) {
      const raw = data.cells[index];
      const vertices = [
        { lat: Number(raw[3]), lng: Number(raw[2]) },
        { lat: Number(raw[5]), lng: Number(raw[4]) },
        { lat: Number(raw[7]), lng: Number(raw[6]) },
      ];
      if (!pointInHydrodynamicTriangle(latlng, vertices)) continue;
      return {
        cellId: raw[0],
        depth: Number(raw[1] || 0),
        forecastId: data.forecast_id || this.options.resultFilters?.forecast_id || "latest",
        timeH: data.time_h,
        latlngs: vertices.map((point) => [point.lat, point.lng]),
      };
    }
    return null;
  },
});

L.gridLayer.hydrodynamicGrid = function hydrodynamicGrid(options) {
  return new L.GridLayer.HydrodynamicGrid(options);
};

function drawHydrodynamicTile(ctx, size, coords, data, renderMode = "mesh") {
  ctx.clearRect(0, 0, size.x, size.y);
  if (!data || data.too_coarse || !Array.isArray(data.cells)) return;
  const origin = tilePoint(coords.x, coords.y, coords.z);
  data.cells.forEach((cell) => {
    const depth = Number(cell[1] || 0);
    const p1 = latLngToTilePixel(Number(cell[3]), Number(cell[2]), coords.z, origin);
    const p2 = latLngToTilePixel(Number(cell[5]), Number(cell[4]), coords.z, origin);
    const p3 = latLngToTilePixel(Number(cell[7]), Number(cell[6]), coords.z, origin);
    const style = renderMode === "result" ? hydrodynamicCellStyle(depth) : hydrodynamicMeshStyle();
    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.lineTo(p3.x, p3.y);
    ctx.closePath();
    ctx.fillStyle = style.fillColor;
    ctx.globalAlpha = style.fillOpacity;
    ctx.fill();
    ctx.globalAlpha = style.opacity || 1;
    ctx.strokeStyle = style.color;
    ctx.lineWidth = style.weight;
    ctx.stroke();
  });
  ctx.globalAlpha = 1;
}

function hydrodynamicClickHitsAnotherObject(event) {
  const target = event.originalEvent?.target;
  if (!target?.closest) return false;
  return Boolean(target.closest(".leaflet-interactive, .leaflet-marker-icon, .leaflet-control"));
}

function pointInHydrodynamicTriangle(point, vertices) {
  const [a, b, c] = vertices;
  const d1 = hydrodynamicTriangleSign(point, a, b);
  const d2 = hydrodynamicTriangleSign(point, b, c);
  const d3 = hydrodynamicTriangleSign(point, c, a);
  const epsilon = 1e-12;
  const hasNegative = d1 < -epsilon || d2 < -epsilon || d3 < -epsilon;
  const hasPositive = d1 > epsilon || d2 > epsilon || d3 > epsilon;
  return !(hasNegative && hasPositive);
}

function hydrodynamicTriangleSign(point, first, second) {
  return (point.lng - second.lng) * (first.lat - second.lat)
    - (first.lng - second.lng) * (point.lat - second.lat);
}

function hydrodynamicCellPopupHtml(cell) {
  const depth = Number(cell.depth || 0);
  const depthText = depth < 0.01 ? depth.toFixed(4) : depth.toFixed(3);
  const timeText = cell.timeH == null
    ? "最大水深"
    : `${formatHydrodynamicHour(Number(cell.timeH))} h`;
  return `
    <div class="popup-title">淹水网格 ${escapeHtml(String(cell.cellId))}</div>
    <div class="popup-depth">${escapeHtml(depthText)} <span>m</span></div>
    <div class="popup-meta">${escapeHtml(timeText)} · ${escapeHtml(String(cell.forecastId || "latest"))}</div>
  `;
}

function hydrodynamicMeshStyle() {
  return {
    color: "rgba(100, 116, 139, 0.34)",
    weight: 0.35,
    fillColor: "rgba(255, 255, 255, 0)",
    fillOpacity: 0,
    opacity: 0.65,
  };
}

function hydrodynamicCellStyle(depth) {
  if (!depth || depth <= 0.0001) {
    return {
      color: "rgba(100, 116, 139, 0.34)",
      weight: 0.35,
      fillColor: "rgba(255, 255, 255, 0)",
      fillOpacity: 0,
      opacity: 0.65,
    };
  }
  const t = Math.max(0, Math.min(1, depth / 4.2));
  const color = interpolateColor([254, 226, 226], [127, 29, 29], Math.pow(t, 0.58));
  return {
    color: "rgba(127, 29, 29, 0.46)",
    weight: 0.35,
    fillColor: color,
    fillOpacity: 0.24 + t * 0.55,
    opacity: 0.82,
  };
}

function interpolateColor(start, end, t) {
  const rgb = start.map((value, index) => Math.round(value + (end[index] - value) * t));
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

function tilePoint(x, y, z) {
  return {
    x: x * 256,
    y: y * 256,
    scale: 256 * 2 ** z,
  };
}

function latLngToTilePixel(lat, lon, z, origin) {
  const world = state.map.options.crs.latLngToPoint(L.latLng(lat, lon), z);
  return {
    x: world.x - origin.x,
    y: world.y - origin.y,
  };
}

function showEventMarker(event, action = {}) {
  const lon = Number(event.longitude);
  const lat = Number(event.latitude);
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return null;
  const eventId = event.event_id || `${event.event_type}:${lon}:${lat}`;
  if (state.eventMarkers.has(eventId)) {
    const existing = state.eventMarkers.get(eventId);
    if (action.fit) state.map.flyTo(existing.getLatLng(), Math.max(state.map.getZoom(), 13), { animate: true, duration: 0.75 });
    return existing;
  }
  const marker = L.circleMarker([lat, lon], {
    radius: event.severity === "watch" ? 7 : 8,
    color: "#ffffff",
    weight: 2,
    fillColor: event.severity === "watch" ? "#f59e0b" : "#dc2626",
    fillOpacity: 0.92,
    className: "event-marker",
  }).addTo(state.map);
  marker.bindPopup(eventPopupHtml(event));
  marker.on("click", () => marker.openPopup());
  state.eventMarkers.set(eventId, marker);
  if (action.fit) {
    state.map.flyTo([lat, lon], Math.max(state.map.getZoom(), 13), {
      animate: true,
      duration: 0.75,
      easeLinearity: 0.22,
    });
    marker.openPopup();
  }
  return marker;
}

function eventPopupHtml(event) {
  const payload = event.payload || {};
  return `
    <div class="popup-title">${escapeHtml(event.title || event.event_type || "水文事件")}</div>
    <div class="popup-meta">${escapeHtml(payload.station_name || event.source_id || "")}</div>
    <div class="popup-meta">${escapeHtml(payload.metric_label || payload.metric || "")}: ${escapeHtml(String(payload.value ?? ""))} ${escapeHtml(payload.unit || "")}</div>
    <div class="popup-meta">阈值: ${escapeHtml(String(payload.threshold ?? ""))} ${escapeHtml(payload.unit || "")}</div>
  `;
}

function addMessage(role, content) {
  const log = document.getElementById("chatLog");
  const item = document.createElement("div");
  item.className = `message ${role}`;
  setMessageMarkdown(item, content);
  log.appendChild(item);
  scrollChat();
  return item;
}

function appendMessageMarkdown(item, content) {
  setMessageMarkdown(item, `${item.dataset.rawMarkdown || ""}${content || ""}`);
}

function setMessageMarkdown(item, content) {
  item.dataset.rawMarkdown = content || "";
  item.innerHTML = renderMarkdown(item.dataset.rawMarkdown);
}

function renderMarkdown(content) {
  if (!window.marked) return escapeHtml(content || "");
  const html = window.marked.parse(content || "", {
    breaks: true,
    gfm: true,
    mangle: false,
    headerIds: false,
  });
  return sanitizeMarkdownHtml(html);
}

function sanitizeMarkdownHtml(html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  const allowedTags = new Set([
    "A", "P", "BR", "STRONG", "EM", "CODE", "PRE", "UL", "OL", "LI",
    "BLOCKQUOTE", "H1", "H2", "H3", "H4", "TABLE", "THEAD", "TBODY",
    "TR", "TH", "TD", "HR",
  ]);
  const allowedAttrs = {
    A: new Set(["href", "title", "target", "rel"]),
    CODE: new Set(["class"]),
  };
  template.content.querySelectorAll("*").forEach((node) => {
    if (!allowedTags.has(node.tagName)) {
      node.replaceWith(...node.childNodes);
      return;
    }
    Array.from(node.attributes).forEach((attr) => {
      const allowed = allowedAttrs[node.tagName]?.has(attr.name);
      if (!allowed) node.removeAttribute(attr.name);
    });
    if (node.tagName === "A") {
      const href = node.getAttribute("href") || "";
      if (!/^(https?:|mailto:|#|\/)/i.test(href)) node.removeAttribute("href");
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
  return template.innerHTML;
}

function scrollChat() {
  const log = document.getElementById("chatLog");
  log.scrollTop = log.scrollHeight;
}

function addTrace(tag, label, detail) {
  const wrap = document.getElementById("agentTrace");
  const key = JSON.stringify([tag || "", label || "", detail || ""]);
  if (state.lastTrace?.key === key && state.lastTrace.item?.isConnected) {
    state.lastTrace.count += 1;
    const count = state.lastTrace.item.querySelector(".trace-count");
    if (count) {
      count.hidden = false;
      count.textContent = `x${state.lastTrace.count}`;
    }
    state.lastTrace.item.classList.add("is-repeated");
    wrap.scrollTop = wrap.scrollHeight;
    return state.lastTrace.item;
  }
  const item = document.createElement("div");
  item.className = `trace-item ${traceTagClass(tag)}`;
  item.innerHTML = `
    <div class="trace-label">
      <span>${escapeHtml(label || "")}</span>
      <span class="trace-badges"><span class="trace-count" hidden></span><span class="trace-tag">${escapeHtml(tag)}</span></span>
    </div>
    ${detail ? `<div class="trace-detail markdown-body">${renderMarkdown(String(detail))}</div>` : ""}
  `;
  wrap.appendChild(item);
  state.lastTrace = { key, item, count: 1 };
  wrap.scrollTop = wrap.scrollHeight;
  return item;
}

function traceTagClass(tag) {
  const value = String(tag || "agent").toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
  return value ? `trace-${value}` : "trace-agent";
}

function shouldHideAutonomyTrace(data = {}) {
  return new Set(["CUT"]).has(data.tag);
}

function activateAgentPane(name) {
  const active = name === "chat" ? "chat" : "trace";
  document.querySelectorAll("[data-agent-pane]").forEach((section) => {
    const isActive = section.dataset.agentPane === active;
    section.classList.toggle("is-active", isActive);
    const toggle = section.querySelector("[data-panel-toggle]");
    if (toggle) toggle.setAttribute("aria-expanded", String(isActive));
  });
  if (active === "chat") {
    scrollChat();
  }
}

function frontendAgentContext() {
  const selected = state.selected || {};
  return {
    ...selected,
    hydrodynamic_timeline: currentHydrodynamicTimelineContext(),
  };
}

function currentHydrodynamicTimelineContext() {
  const timeline = state.hydrodynamicTimeline;
  if (!timeline.layer || !timeline.key) {
    return {
      active: false,
      mode: "none",
      current_hydrodynamic_time_h: null,
    };
  }
  if (!timeline.hours.length || timeline.index === 0) {
    return {
      active: true,
      mode: "max_depth",
      current_hydrodynamic_time_h: null,
    };
  }
  const hour = Number(timeline.hours[timeline.index - 1]);
  return {
    active: true,
    mode: "time_slice",
    current_hydrodynamic_time_h: Number.isFinite(hour) ? Number(formatHydrodynamicHour(hour)) : null,
  };
}

function parseToolJsonResult(value) {
  if (!value) return null;
  if (typeof value === "object") return value;
  try {
    const parsed = JSON.parse(String(value));
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function registerImpactAnalysisResult(result, options = {}) {
  if (!result || typeof result !== "object") return;
  if (!["completed", "no_forecast_cells"].includes(result.status)) return;
  const previous = state.impactAnalysis;
  const visibleTypes = options.preserveVisibleTypes && previous?.visibleTypes
    ? previous.visibleTypes
    : visibleImpactObjectTypes(result);
  const params = result.parameters || {};
  state.impactAnalysis = {
    forecastId: result.forecast_id || "latest",
    targetType: result.target_type || "all",
    minDepthM: Number(params.min_depth_m ?? 0.15),
    maxDistanceM: Number(params.max_distance_m ?? 120),
    visibleTypes,
    lastResult: result,
  };
  state.pendingImpactObjectTypes.clear();
  if (!options.preserveVisibleTypes) scheduleImpactAnalysisRefresh();
}

function rememberImpactObjectLayer(objectType) {
  if (!objectType || state.applyingImpactRefresh) return;
  if (!state.impactAnalysis) {
    state.pendingImpactObjectTypes.add(objectType);
    return;
  }
  state.impactAnalysis.visibleTypes.add(objectType);
  scheduleImpactAnalysisRefresh();
}

function visibleImpactObjectTypes(result) {
  const affected = result?.affected_object_ids || {};
  const types = Object.keys(affected).filter((objectType) => Array.isArray(affected[objectType]) && affected[objectType].length);
  const visible = types.filter((objectType) => state.pendingImpactObjectTypes.has(objectType) || hasObjectType(objectType));
  return new Set(visible);
}

function scheduleImpactAnalysisRefresh() {
  if (!state.impactAnalysis || !state.impactAnalysis.visibleTypes?.size) return;
  if (state.impactRefreshTimer) window.clearTimeout(state.impactRefreshTimer);
  state.impactRefreshTimer = window.setTimeout(refreshImpactAnalysisForTimeline, 260);
}

async function refreshImpactAnalysisForTimeline() {
  const analysis = state.impactAnalysis;
  if (!analysis || !analysis.visibleTypes?.size) return;
  const timeline = currentHydrodynamicTimelineContext();
  const params = new URLSearchParams({
    forecast_id: analysis.forecastId || "latest",
    target_type: analysis.targetType || "all",
    min_depth_m: String(Number.isFinite(analysis.minDepthM) ? analysis.minDepthM : 0.15),
    max_distance_m: String(Number.isFinite(analysis.maxDistanceM) ? analysis.maxDistanceM : 120),
  });
  if (timeline.mode === "time_slice" && timeline.current_hydrodynamic_time_h != null) {
    params.set("time_h", String(timeline.current_hydrodynamic_time_h));
  }
  const seq = ++state.impactRefreshSeq;
  try {
    const res = await fetch(`/api/impact-analysis?${params.toString()}`);
    if (!res.ok) throw new Error(await res.text());
    const result = await res.json();
    if (seq !== state.impactRefreshSeq) return;
    registerImpactAnalysisResult(result, { preserveVisibleTypes: true });
    await renderImpactAnalysisObjects(result, Array.from(analysis.visibleTypes));
  } catch (error) {
    console.warn("impact analysis refresh failed", error);
  }
}

async function renderImpactAnalysisObjects(result, objectTypes) {
  const affected = result?.affected_object_ids || {};
  state.applyingImpactRefresh = true;
  clearHighlights();
  try {
    for (const objectType of objectTypes) {
      removeObjectTypeLayers(objectType);
      const objectIds = (affected[objectType] || []).map(String).filter(Boolean);
      if (!objectIds.length) continue;
      const label = `${OBJECT_CONFIG[objectType]?.label || objectType}受影响对象`;
      await loadObject(objectType, {}, {
        fit: false,
        label,
        objectIds,
      });
      await highlightObjects({
        object_type: objectType,
        object_ids: objectIds,
        filters: {},
        label,
        fit: false,
      });
    }
  } finally {
    state.applyingImpactRefresh = false;
  }
}

function parseEvent(event) {
  try {
    return JSON.parse(event.data || "{}");
  } catch (_err) {
    return {};
  }
}

function compactText(text) {
  const value = String(text || "");
  return value.length > 420 ? `${value.slice(0, 420)}...` : value;
}

function readableTool(name, args) {
  const labels = {
    query: "查询对象",
    count: "统计数量",
    inspect: "查看定义",
    run_flood_forecast: "运行洪水预测",
    run_emergency_cycle: "运行闭环预警",
    analyze_inundation_impacts: "分析淹没影响",
    ui_show_objects: "地图显示",
    ui_show_event_marker: "地图标记事件",
    ui_clear_map: "清空地图",
    ui_focus_object: "地图定位",
  };
  const parts = [];
  if (args.object_type) parts.push(args.object_type);
  if (Array.isArray(args.objects)) parts.push(args.objects.map((item) => item.object_type).filter(Boolean).join(", "));
  if (args.target) parts.push(args.target);
  return `${labels[name] || name}${parts.length ? ` (${parts.join(", ")})` : ""}`;
}

function setSending(active) {
  const btn = document.querySelector(".send-button");
  const input = document.getElementById("chatInput");
  btn.innerHTML = active ? '<i data-lucide="square"></i>' : '<i data-lucide="send-horizontal"></i>';
  input.disabled = false;
  renderIcons();
}

function appendConfirmation(data) {
  const item = addMessage("agent", `需要确认：${data.tool_name || ""}`);
  const approve = document.createElement("button");
  approve.textContent = "确认";
  approve.className = "inline-action";
  const deny = document.createElement("button");
  deny.textContent = "拒绝";
  deny.className = "inline-action";
  item.append(" ");
  item.appendChild(approve);
  item.appendChild(deny);
  approve.addEventListener("click", () => runConfirm(true));
  deny.addEventListener("click", () => runConfirm(false));
}

function appendQuestion(data) {
  addMessage("agent", data.question || "需要补充信息。");
}

async function runConfirm(approved) {
  const assistant = addMessage("agent", "");
  const res = await fetch("/api/agent/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.sessionId, approved }),
  });
  if (!res.ok) {
    setMessageMarkdown(assistant, await res.text());
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const dataLine = part.split("\n").find((line) => line.startsWith("data: "));
      if (!dataLine) continue;
      const data = JSON.parse(dataLine.slice(6));
      if (data.type === "text") appendMessageMarkdown(assistant, data.content || "");
      if (data.type === "tool_call") addTrace("CALL", readableTool(data.name, data.args || {}), JSON.stringify(data.args || {}, null, 2));
      if (data.type === "tool_result") addTrace("RESULT", data.name || "tool result", compactText(data.result || ""));
    }
  }
  scrollChat();
}

function setObjectButtonActive(objectType, active) {
  document.querySelectorAll(`[data-object-type="${objectType}"]`).forEach((btn) => {
    btn.classList.toggle("active", active);
  });
}

function hasObjectType(objectType) {
  return Array.from(state.layerMeta.values()).some((meta) => meta.objectType === objectType);
}

function hasLayerButtonType(buttonType) {
  return Array.from(state.layerMeta.values()).some((meta) => (meta.buttonType || meta.objectType) === buttonType);
}

function layerKey(objectType, filters) {
  return `${objectType}:${JSON.stringify(filters || {})}`;
}

function featureIndexKey(objectType, objectId) {
  return `${objectType}:${String(objectId)}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderIcons() {
  if (window.lucide) window.lucide.createIcons();
}

function getSessionId() {
  const key = "flood-agent-session-id";
  let value = window.localStorage.getItem(key);
  if (!value) {
    value = `frontend-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    window.localStorage.setItem(key, value);
  }
  return value;
}
