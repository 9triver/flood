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
  workspaceId: null,
  activeStream: null,
  activeRunId: null,
  autonomyStream: null,
  autonomyPhase: "",
  eventMarkers: new Map(),
  hydrodynamicGridMeta: null,
  hydrodynamicResultMeta: null,
  lastTrace: null,
  playbackRunning: false,
  playbackPaused: false,
  playbackSpeed: 1,
  playbackAutoPauseArmed: false,
  playbackAutoPausePending: false,
  playbackTotalRows: 0,
  lastMockObservation: null,
  conclusionToasts: [],
  nextConclusionToastId: 1,
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
  impactMarkerLayer: null,
  impactMarkers: new Map(),
  selectedImpactKey: null,
  selectedImpactLayerKey: null,
  impactFocusSeq: 0,
  impactRefreshTimer: null,
  impactRefreshSeq: 0,
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
  await loadObject("River", {}, { fit: false });
  addMessage("agent", "基础对象已加载。");
  startAutonomyStream();
  await refreshPlaybackStatus();
  renderIcons();
});

function initMap() {
  state.map = L.map("map", {
    crs: AMAP_CRS,
    zoomControl: false,
    preferCanvas: true,
  }).setView([24.4, 111.35], 10);

  state.map.createPane("impactPane");
  state.map.getPane("impactPane").style.zIndex = "475";
  state.impactMarkerLayer = L.layerGroup().addTo(state.map);
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
  state.workspaceId = state.bootstrap.workspace_id || null;
  document.getElementById("contextPill").textContent = state.bootstrap.default_context;
  renderObjectList(state.bootstrap.mappable || []);
}

function renderObjectList(items) {
  const list = document.getElementById("objectList");
  const visible = ["River", "Watershed", "County", "Town", "HydrodynamicCell", "ForecastResult", "HydroStation", "Road", "Reservoir", "Sluice", "Bridge", "HydraulicStructure", "Risk", "Place", "Route"];
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
    btn.addEventListener("click", async () => {
      await toggleObject(objectType);
      if (window.matchMedia("(max-width: 900px)").matches) setLayerPanelOpen(false);
    });
    list.appendChild(btn);
  });
}

function bindEvents() {
  document.getElementById("fitAllBtn").addEventListener("click", fitAll);
  document.getElementById("layerPanelBtn").addEventListener("click", toggleLayerPanel);
  document.getElementById("telemetryPanelBtn").addEventListener("click", toggleTelemetryPanel);
  document.getElementById("telemetryCloseBtn").addEventListener("click", () => setTelemetryPanelOpen(false));
  document.getElementById("agentDrawerBtn").addEventListener("click", () => setAgentDrawerOpen(true));
  document.getElementById("agentCloseBtn").addEventListener("click", () => setAgentDrawerOpen(false));
  document.getElementById("playbackToggleBtn").addEventListener("click", toggleBoundaryFlowPlayback);
  document.getElementById("playbackSpeedSelect").addEventListener("change", updatePlaybackSpeed);
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
  const isOpen = !control.classList.contains("is-open");
  if (isOpen) setTelemetryPanelOpen(false);
  setLayerPanelOpen(isOpen);
}

function setLayerPanelOpen(isOpen) {
  const control = document.querySelector(".map-layer-control");
  const btn = document.getElementById("layerPanelBtn");
  control.classList.toggle("is-open", isOpen);
  btn.classList.toggle("is-active", isOpen);
  btn.setAttribute("aria-expanded", String(isOpen));
}

function toggleTelemetryPanel() {
  const control = document.getElementById("telemetryControl");
  const isOpen = !control.classList.contains("is-open");
  if (isOpen) setLayerPanelOpen(false);
  setTelemetryPanelOpen(isOpen);
}

function setTelemetryPanelOpen(isOpen) {
  const control = document.getElementById("telemetryControl");
  const btn = document.getElementById("telemetryPanelBtn");
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
  if (!hours.length) {
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
  slider.max = String(hours.length - 1);
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
  clearImpactAnalysisState();
}

function setHydrodynamicTimelineIndex(index) {
  const timeline = state.hydrodynamicTimeline;
  if (!timeline.layer || !timeline.hours.length) return;
  const nextIndex = Math.max(0, Math.min(timeline.hours.length - 1, Math.round(index)));
  timeline.index = nextIndex;
  const slider = document.getElementById("hydroTimeSlider");
  const label = document.getElementById("hydroTimeLabel");
  if (slider) slider.value = String(nextIndex);
  const filters = { ...(timeline.baseFilters || {}) };
  const hour = timeline.hours[nextIndex];
  filters.time_h = formatHydrodynamicHour(hour);
  label.textContent = `${formatHydrodynamicHour(hour)} h`;
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
    const next = timeline.index >= timeline.hours.length - 1 ? 0 : timeline.index + 1;
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
  for (const key of Array.from(state.layerGroups.keys())) {
    const meta = state.layerMeta.get(key);
    if (!["River", "Watershed"].includes(meta?.objectType)) {
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
  state.impactFocusSeq += 1;
  state.impactAnalysis = null;
  state.impactMarkerLayer?.clearLayers();
  state.impactMarkers.clear();
  clearImpactObjectSelection({ removeLayer: true });
  const panel = document.getElementById("impactPanel");
  panel?.classList.add("is-hidden");
  panel?.classList.remove("is-loading");
}

function clearEventMarkers() {
  state.eventMarkers.forEach((marker) => state.map.removeLayer(marker));
  state.eventMarkers.clear();
}

function startAutonomyStream() {
  if (state.autonomyStream) state.autonomyStream.close();
  const es = new EventSource("/api/autonomy/stream?interval=5");
  state.autonomyStream = es;

  es.addEventListener("runtime_status", (event) => {
    const data = parseEvent(event);
    acceptWorkspace(data.workspace_id);
    if (["等待水文事件", "等待边界流量事件", "等待启动边界流量回放"].includes(data.label)) return;
    if (data.status === "running") setPlaybackButtonState(true, false);
    if (["paused", "stopped", "finished"].includes(data.status)) {
      setPlaybackButtonState(false, data.status === "paused");
    }
    if (data.speed_multiplier) setPlaybackSpeedControl(data.speed_multiplier);
    updateTelemetryRuntimeStatus(data);
    addTrace("AUTO", data.label || "事件运行时", data.detail || "");
  });

  es.addEventListener("domain_event", (event) => {
    const data = parseEvent(event);
    acceptWorkspace(data.workspace_id);
    renderDomainEvent(data);
  });

  es.addEventListener("boundary_flow_data", (event) => {
    const data = parseEvent(event);
    acceptWorkspace(data.workspace_id);
    renderMockObservation(data.event || {});
  });

  es.addEventListener("agent_trace", (event) => {
    const data = parseEvent(event);
    acceptWorkspace(data.workspace_id);
    if (shouldHideAutonomyTrace(data)) return;
    addTrace(data.tag || "AGENT", data.label || "智能体事件处理", data.detail || "");
  });

  es.addEventListener("map_actions", async (event) => {
    const data = parseEvent(event);
    acceptWorkspace(data.workspace_id);
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

async function refreshPlaybackStatus() {
  try {
    const res = await fetch("/api/autonomy/status");
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    setPlaybackButtonState(Boolean(data.running), Boolean(data.paused));
    setPlaybackSpeedControl(data.speed_multiplier || 1);
    updateTelemetryRuntimeStatus(data);
  } catch (error) {
    console.warn("boundary flow playback status failed", error);
    setPlaybackButtonState(false);
  }
}

async function toggleBoundaryFlowPlayback() {
  const wasRunning = state.playbackRunning;
  const wasPaused = state.playbackPaused;
  const action = wasRunning ? "stop" : (wasPaused ? "resume" : "start");
  const btn = document.getElementById("playbackToggleBtn");
  btn.disabled = true;
  try {
    if (action === "start") {
      setPlaybackSpeedControl(10);
      state.playbackAutoPauseArmed = true;
      state.playbackAutoPausePending = false;
      resetMap();
      clearMockTelemetry();
      setLayerPanelOpen(false);
      setTelemetryPanelOpen(true);
    } else if (action === "resume") {
      state.playbackAutoPauseArmed = true;
      state.playbackAutoPausePending = false;
    } else {
      state.playbackAutoPauseArmed = false;
    }
    const res = await fetch(`/api/autonomy/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speed_multiplier: state.playbackSpeed }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    acceptWorkspace(data.workspace_id);
    setPlaybackButtonState(Boolean(data.running), Boolean(data.paused));
    if (!data.running) state.playbackAutoPauseArmed = false;
    updateTelemetryRuntimeStatus(data);
    const labels = {
      start: ["边界流量过程回放已启动", "后台开始按时间顺序回放四边界流量。"],
      resume: ["边界流量过程回放已继续", "后台从暂停位置继续回放四边界流量。"],
      stop: ["边界流量过程回放已停止", "后台已停止回放新的边界流量观测。"],
    };
    addTrace(
      "AUTO",
      labels[action][0],
      labels[action][1],
    );
  } catch (error) {
    if (action !== "stop") state.playbackAutoPauseArmed = false;
    addTrace("ERR", "边界流量回放切换失败", error.message || String(error));
    setPlaybackButtonState(wasRunning, wasPaused);
  } finally {
    btn.disabled = false;
  }
}

function acceptWorkspace(workspaceId) {
  const next = String(workspaceId || "");
  if (!next || next === state.workspaceId) return;
  state.workspaceId = next;
  clearRuntimeWorkspaceView();
}

function clearRuntimeWorkspaceView() {
  resetMap();
  clearMockTelemetry();
  state.lastTrace = null;
  const trace = document.getElementById("agentTrace");
  const chat = document.getElementById("chatLog");
  if (trace) trace.innerHTML = "";
  if (chat) chat.innerHTML = "";
  state.conclusionToasts.forEach((item) => item.element?.remove());
  state.conclusionToasts = [];
  document.getElementById("conclusionToastRegion")?.replaceChildren();
}

async function updatePlaybackSpeed(event) {
  const previousSpeed = state.playbackSpeed;
  const speed = Number(event.target.value || 1);
  setPlaybackSpeedControl(speed);
  if (!state.playbackRunning) return;
  event.target.disabled = true;
  try {
    const res = await fetch("/api/autonomy/speed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speed_multiplier: speed }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    setPlaybackSpeedControl(data.speed_multiplier || speed);
  } catch (error) {
    setPlaybackSpeedControl(previousSpeed);
    addTrace("ERR", "演进速率调整失败", error.message || String(error));
  } finally {
    event.target.disabled = false;
  }
}

function setPlaybackSpeedControl(speed) {
  const value = [1, 2, 5, 10].includes(Number(speed)) ? Number(speed) : 1;
  state.playbackSpeed = value;
  const select = document.getElementById("playbackSpeedSelect");
  if (select) select.value = String(value);
}

function setPlaybackButtonState(running, paused = false) {
  state.playbackRunning = running;
  state.playbackPaused = !running && paused;
  const btn = document.getElementById("playbackToggleBtn");
  if (!btn) return;
  btn.classList.toggle("is-running", running);
  btn.classList.toggle("is-paused", state.playbackPaused);
  btn.setAttribute("aria-pressed", String(running));
  btn.title = running
    ? "停止边界流量过程回放"
    : (state.playbackPaused ? "从暂停位置继续边界流量过程回放" : "启动边界流量过程回放");
  btn.innerHTML = running
    ? '<i data-lucide="pause"></i><span>停止演进</span>'
    : `<i data-lucide="play"></i><span>${state.playbackPaused ? "继续演进" : "开始演进"}</span>`;
  renderIcons();
}

function updateTelemetryRuntimeStatus(data) {
  if (Number(data.total_rows || 0) > 0) state.playbackTotalRows = Number(data.total_rows);
  if (data.running) {
    if (!state.lastMockObservation) setTelemetryState("等待", "normal");
    return;
  }
  if (data.status === "finished") {
    setTelemetryState("完成", "normal");
  } else if (data.status === "paused" || data.paused) {
    setTelemetryState("已暂停", "stopped");
  } else if (data.status === "stopped" || data.running === false) {
    setTelemetryState(state.lastMockObservation ? "已停止" : "待机", "stopped");
  }
}

function renderMockObservation(event) {
  const observation = event.payload?.observation;
  if (!observation) return;
  state.lastMockObservation = observation;
  document.getElementById("telemetryTime").textContent = formatMockTime(observation.observed_at);
  renderTelemetryWeather(observation.rainfall_mm);
  setMockField("rainfall_mm", observation.rainfall_mm, 1);
  setMockField("reservoir_level_m", observation.reservoir_level_m, 3);
  setMockField("reservoir_inflow_m3s", observation.reservoir_inflow_m3s, 2);
  setMockField("reservoir_release_m3s", observation.reservoir_release_m3s, 2);
  ["interval1", "interval2", "tonggu", "upstream"].forEach((key) => {
    const target = document.querySelector(`[data-mock-boundary="${key}"]`);
    const flow = observation.boundaries?.[key]?.flow_m3s;
    if (target) target.textContent = formatMockNumber(flow, 2);
  });

  const rainfall = Number(observation.rainfall_mm || 0);
  const totalFlow = Number(observation.total_flow_m3s || 0);
  const baseflowTotal = Number(observation.baseflow_total_m3s || 0);
  if (rainfall > 0) setTelemetryState("降雨", "raining");
  else if (baseflowTotal > 0 && totalFlow > baseflowTotal * 1.25) setTelemetryState("退水", "receding");
  else setTelemetryState("正常", "normal");

  const current = Number(observation.sequence || 0) + 1;
  const total = Math.max(state.playbackTotalRows, current);
  const ratio = total > 0 ? Math.min(100, current / total * 100) : 0;
  document.getElementById("telemetryProgressBar").style.width = `${ratio.toFixed(2)}%`;
  document.getElementById("telemetryProgressText").textContent = `${current} / ${total}`;
}

function clearMockTelemetry() {
  state.lastMockObservation = null;
  document.getElementById("telemetryTime").textContent = "--";
  renderTelemetryWeather(null);
  document.querySelectorAll("[data-mock-field], [data-mock-boundary]").forEach((element) => {
    element.textContent = "--";
  });
  document.getElementById("telemetryProgressBar").style.width = "0%";
  document.getElementById("telemetryProgressText").textContent = `0 / ${state.playbackTotalRows}`;
  setTelemetryState("等待", "normal");
}

function renderTelemetryWeather(value) {
  const parsed = value === null || value === undefined || value === "" ? null : Number(value);
  const rainfall = Number.isFinite(parsed) ? parsed : null;
  const weather = telemetryWeatherForRainfall(rainfall);
  const container = document.getElementById("telemetryWeather");
  container.dataset.weather = weather.key;
  document.getElementById("telemetryWeatherIcon").innerHTML = `<i data-lucide="${weather.icon}"></i>`;
  document.getElementById("telemetryWeatherLabel").textContent = weather.label;
  document.getElementById("telemetryWeatherDetail").textContent = rainfall === null
    ? "当前时段降雨 -- mm"
    : `当前时段降雨 ${rainfall.toFixed(1)} mm`;
  renderIcons();
}

function telemetryWeatherForRainfall(rainfall) {
  if (rainfall === null) return { key: "waiting", label: "等待数据", icon: "cloud-sun" };
  if (rainfall <= 0) return { key: "dry", label: "无降雨", icon: "cloud-sun" };
  if (rainfall < 2.5) return { key: "light", label: "小雨", icon: "cloud-drizzle" };
  if (rainfall < 8) return { key: "moderate", label: "中雨", icon: "cloud-rain" };
  if (rainfall < 16) return { key: "heavy", label: "大雨", icon: "cloud-rain-wind" };
  if (rainfall < 30) return { key: "storm", label: "暴雨", icon: "cloud-lightning" };
  return { key: "severe", label: "大暴雨", icon: "cloud-lightning" };
}

function setMockField(field, value, digits) {
  const target = document.querySelector(`[data-mock-field="${field}"]`);
  if (target) target.textContent = formatMockNumber(value, digits);
}

function formatMockNumber(value, digits) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "--";
}

function formatMockTime(value) {
  if (!value) return "--";
  return String(value).replace("T", " ").replace(/:00\+08:00$/, "");
}

function setTelemetryState(label, stateName) {
  const element = document.getElementById("telemetryState");
  element.textContent = label;
  element.className = `telemetry-state is-${stateName}`;
}

function renderDomainEvent(data) {
  if (!data || !data.event_type) return;
  if (data.event_type === "InundationGenerated") {
    void pausePlaybackAfterInundation();
  }
  if (data.event_type === "ImpactAnalyzed") {
    registerImpactAnalysisResult(data.payload || null);
  }
  const tag = data.event_type === "FloodForecastRequired" ? "ALERT" : "EVENT";
  const label = data.event_type === "FloodForecastRequired" ? "洪水预测请求进入智能体" : (data.title || data.event_type);
  addTrace(tag, label, eventDetail(data));
  setCyclePhase(eventPhase(data.event_type));
}

async function pausePlaybackAfterInundation() {
  if (!state.playbackRunning || !state.playbackAutoPauseArmed || state.playbackAutoPausePending) return;
  state.playbackAutoPauseArmed = false;
  state.playbackAutoPausePending = true;
  const btn = document.getElementById("playbackToggleBtn");
  btn.disabled = true;
  try {
    const res = await fetch("/api/autonomy/pause", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!res.ok) throw new Error(await res.text());
    const status = await res.json();
    setPlaybackButtonState(Boolean(status.running), true);
    updateTelemetryRuntimeStatus(status);
    addTrace("AUTO", "演进已自动暂停", "已收到 InundationGenerated，停止继续回放边界流量观测。");
  } catch (error) {
    state.playbackAutoPauseArmed = state.playbackRunning;
    addTrace("ERR", "演进自动暂停失败", error.message || String(error));
  } finally {
    state.playbackAutoPausePending = false;
    btn.disabled = false;
  }
}

function eventDetail(data) {
  const payload = data.payload || {};
  if (data.event_type === "FloodForecastRequired") {
    const trigger = payload.forecast_trigger || {};
    return trigger.reason || "领域策略要求运行洪水预测";
  }
  if (data.event_type === "FloodEpisodeEnded") {
    return `${payload.ended_at || ""}，预测输入 ${Number(payload.forecast_versions || 0)} 个版本`;
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

function eventPhase(eventType) {
  return {
    BoundaryFlowObserved: "observe",
    FloodForecastRequired: "analyze",
    FloodEpisodeEnded: "monitor",
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
      registerImpactAnalysisResult(parseToolJsonResult(data.result));
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
  if (String(label || "").trim() === "智能体结论") {
    enqueueConclusionToast(label, detail);
  }
  return item;
}

function enqueueConclusionToast(label, detail) {
  const item = {
    id: state.nextConclusionToastId++,
    label: String(label || "智能体结论"),
    detail: String(detail || ""),
    dragX: 0,
    dragY: 0,
    element: null,
  };
  item.element = createConclusionToastElement(item);
  state.conclusionToasts.push(item);
  document.getElementById("conclusionToastRegion").appendChild(item.element);
  bindConclusionToastDrag(item);
  updateConclusionToastStack();
  renderIcons();
  requestAnimationFrame(() => item.element?.classList.add("is-visible"));
}

function createConclusionToastElement(item) {
  const toast = document.createElement("article");
  toast.className = "conclusion-toast";
  toast.dataset.toastId = String(item.id);
  toast.setAttribute("role", "status");
  toast.innerHTML = `
    <header class="conclusion-toast-drag-handle" title="拖动">
      <div class="conclusion-toast-heading">
        <i data-lucide="sparkles"></i>
        <span>${escapeHtml(item.label)}</span>
      </div>
      <div class="conclusion-toast-header-actions">
        <span class="conclusion-toast-queue" hidden></span>
        <i class="conclusion-toast-grip" data-lucide="grip-horizontal" aria-hidden="true"></i>
      </div>
    </header>
    <div class="conclusion-toast-body markdown-body">${renderMarkdown(item.detail)}</div>
    <footer>
      <button class="conclusion-dismiss" type="button">
        <i data-lucide="x"></i>
        <span>Dismiss</span>
      </button>
    </footer>
  `;
  toast.querySelector(".conclusion-dismiss").addEventListener("click", () => {
    dismissConclusionToast(item.id);
  });
  return toast;
}

function dismissConclusionToast(id) {
  const index = state.conclusionToasts.findIndex((item) => item.id === id);
  if (index < 0) return;
  const [item] = state.conclusionToasts.splice(index, 1);
  item.element?.remove();
  updateConclusionToastStack();
}

function updateConclusionToastStack() {
  const total = state.conclusionToasts.length;
  state.conclusionToasts.forEach((item, index) => {
    const depth = Math.min(index, 4);
    item.element.style.setProperty("--stack-x", `${depth * 7}px`);
    item.element.style.setProperty("--stack-y", `${depth * -8}px`);
    item.element.style.setProperty("--drag-x", `${item.dragX}px`);
    item.element.style.setProperty("--drag-y", `${item.dragY}px`);
    item.element.style.zIndex = String(Math.max(1, 1000 - index));
    const count = item.element.querySelector(".conclusion-toast-queue");
    count.hidden = index !== 0 || total < 2;
    count.textContent = index === 0 && total > 1 ? `+${total - 1}` : "";
  });
}

function bindConclusionToastDrag(item) {
  const toast = item.element;
  const handle = toast.querySelector(".conclusion-toast-drag-handle");
  let drag = null;

  const finish = (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
    toast.classList.remove("is-dragging");
    drag = null;
  };

  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    drag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: item.dragX,
      originY: item.dragY,
      rect: toast.getBoundingClientRect(),
    };
    toast.classList.add("is-dragging");
    handle.setPointerCapture(event.pointerId);
    event.preventDefault();
  });

  handle.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const deltaX = event.clientX - drag.startX;
    const deltaY = event.clientY - drag.startY;
    const clampedX = Math.max(72 - drag.rect.right, Math.min(
      window.innerWidth - 72 - drag.rect.left,
      deltaX,
    ));
    const clampedY = Math.max(8 - drag.rect.top, Math.min(
      window.innerHeight - 48 - drag.rect.top,
      deltaY,
    ));
    item.dragX = drag.originX + clampedX;
    item.dragY = drag.originY + clampedY;
    toast.style.setProperty("--drag-x", `${item.dragX}px`);
    toast.style.setProperty("--drag-y", `${item.dragY}px`);
  });

  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", finish);
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
  if (!timeline.hours.length) {
    return {
      active: true,
      mode: "none",
      current_hydrodynamic_time_h: null,
    };
  }
  const hour = Number(timeline.hours[timeline.index]);
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
  const params = result.parameters || {};
  state.impactAnalysis = {
    forecastId: result.forecast_id || "latest",
    targetType: result.target_type || "all",
    minDepthM: Number(params.min_depth_m ?? 0.15),
    maxDistanceM: Number(params.max_distance_m ?? 10),
    lastResult: result,
  };
  if (options.render === false) return;
  const timeline = currentHydrodynamicTimelineContext();
  if (timeline.mode === "time_slice") {
    const resultHour = Number(result.time_h);
    const currentHour = Number(timeline.current_hydrodynamic_time_h);
    if (!Number.isFinite(resultHour) || Math.abs(resultHour - currentHour) > 0.001) {
      scheduleImpactAnalysisRefresh();
      return;
    }
  }
  renderImpactAnalysisResult(result);
}

function scheduleImpactAnalysisRefresh() {
  if (currentHydrodynamicTimelineContext().mode !== "time_slice") return;
  if (state.impactRefreshTimer) window.clearTimeout(state.impactRefreshTimer);
  state.impactRefreshTimer = window.setTimeout(refreshImpactAnalysisForTimeline, 260);
}

async function refreshImpactAnalysisForTimeline() {
  const timeline = currentHydrodynamicTimelineContext();
  if (timeline.mode !== "time_slice" || timeline.current_hydrodynamic_time_h == null) return;
  const forecastId = state.hydrodynamicTimeline.baseFilters?.forecast_id
    || state.hydrodynamicResultMeta?.forecast?.forecast_id
    || "latest";
  const params = new URLSearchParams({
    forecast_id: forecastId,
    target_type: "all",
    min_depth_m: "0.15",
    max_distance_m: "10",
    time_h: String(timeline.current_hydrodynamic_time_h),
  });
  const seq = ++state.impactRefreshSeq;
  setImpactAnalysisLoading(timeline.current_hydrodynamic_time_h);
  try {
    const res = await fetch(`/api/impact-analysis?${params.toString()}`);
    if (!res.ok) throw new Error(await res.text());
    const result = await res.json();
    if (seq !== state.impactRefreshSeq) return;
    registerImpactAnalysisResult(result, { render: false });
    renderImpactAnalysisResult(result);
  } catch (error) {
    if (seq !== state.impactRefreshSeq) return;
    setImpactAnalysisError(error);
    console.warn("impact analysis refresh failed", error);
  }
}

function setImpactAnalysisLoading(hour) {
  const panel = document.getElementById("impactPanel");
  panel?.classList.remove("is-hidden");
  panel?.classList.add("is-loading");
  const time = document.getElementById("impactTimeLabel");
  const status = document.getElementById("impactStatus");
  if (time) time.textContent = `${formatHydrodynamicHour(hour)} h`;
  if (status) status.textContent = "正在计算当前时刻的受影响对象...";
}

function setImpactAnalysisError(error) {
  document.getElementById("impactPanel")?.classList.remove("is-loading");
  const status = document.getElementById("impactStatus");
  if (status) status.textContent = `影响分析失败：${String(error?.message || error)}`;
}

function renderImpactAnalysisResult(result) {
  const impacts = (result?.impacts || []).filter((impact) => (
    impact?.object_type
    && impact?.object_id != null
    && Number.isFinite(Number(impact.longitude))
    && Number.isFinite(Number(impact.latitude))
  ));
  const currentKeys = new Set(impacts.map(impactObjectKey));
  if (state.selectedImpactKey && !currentKeys.has(state.selectedImpactKey)) {
    clearImpactObjectSelection({ removeLayer: true });
  }
  state.impactAnalysis = {
    ...(state.impactAnalysis || {}),
    lastResult: result,
  };
  renderImpactMarkers(impacts);
  renderImpactList(result, impacts);
  updateSelectedImpactDetails(impacts);
}

function renderImpactMarkers(impacts) {
  state.impactMarkerLayer?.clearLayers();
  state.impactMarkers.clear();
  impacts.forEach((impact) => {
    const key = impactObjectKey(impact);
    const selected = key === state.selectedImpactKey;
    const marker = L.marker([Number(impact.latitude), Number(impact.longitude)], {
      icon: impactMarkerIcon(impact, selected),
      pane: "impactPane",
      interactive: true,
      keyboard: true,
      riseOnHover: true,
      zIndexOffset: selected ? 900 : 0,
    });
    marker._impactData = impact;
    marker.bindTooltip(impactTooltipHtml(impact), {
      direction: "top",
      offset: [0, -8],
    });
    marker.on("click", () => void focusImpactObject(impact));
    marker.addTo(state.impactMarkerLayer);
    state.impactMarkers.set(key, marker);
  });
}

function impactMarkerIcon(impact, selected = false) {
  const riskLevel = ["critical", "high", "medium", "low"].includes(impact.risk_level)
    ? impact.risk_level
    : "unknown";
  return L.divIcon({
    className: `impact-point-marker is-${riskLevel}${selected ? " is-selected" : ""}`,
    html: '<span class="impact-point-core" aria-hidden="true"></span>',
    iconSize: [16, 16],
    iconAnchor: [8, 8],
    tooltipAnchor: [0, -8],
  });
}

function impactTooltipHtml(impact) {
  return `<strong>${escapeHtml(impact.name || impact.object_id)}</strong><br>${escapeHtml(impactTypeLabel(impact.object_type))} · 水深 ${formatImpactNumber(impact.depth_m, 2)} m`;
}

function renderImpactList(result, impacts) {
  const panel = document.getElementById("impactPanel");
  const count = document.getElementById("impactCount");
  const time = document.getElementById("impactTimeLabel");
  const status = document.getElementById("impactStatus");
  const list = document.getElementById("impactList");
  if (!panel || !count || !time || !status || !list) return;
  panel.classList.remove("is-hidden", "is-loading");
  count.textContent = String(impacts.length);
  time.textContent = result?.time_h == null ? "最大包络" : `${formatHydrodynamicHour(result.time_h)} h`;
  status.textContent = impacts.length
    ? `按水深与风险排序，共 ${impacts.length} 个对象`
    : "当前时刻未发现受影响对象";
  list.innerHTML = "";
  impacts.forEach((impact) => {
    const key = impactObjectKey(impact);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "impact-list-item";
    button.classList.toggle("is-selected", key === state.selectedImpactKey);
    button.dataset.impactKey = key;
    button.innerHTML = `
      <span class="impact-risk-dot is-${escapeHtml(impact.risk_level || "unknown")}"></span>
      <span class="impact-list-copy">
        <strong>${escapeHtml(impact.name || impact.object_id)}</strong>
        <small>${escapeHtml(impactTypeLabel(impact.object_type))} · ${escapeHtml(String(impact.object_id))}</small>
      </span>
      <span class="impact-list-depth">${formatImpactNumber(impact.depth_m, 2)}<small>m</small></span>
    `;
    button.addEventListener("click", () => void focusImpactObject(impact));
    list.appendChild(button);
  });
}

async function focusImpactObject(impact) {
  const objectType = impact?.object_type;
  const objectId = String(impact?.object_id || "");
  if (!objectType || !objectId) return;
  const key = impactObjectKey(impact);
  const focusSeq = ++state.impactFocusSeq;
  if (state.selectedImpactLayerKey && state.selectedImpactKey !== key) {
    removeLayer(state.selectedImpactLayerKey);
    state.selectedImpactLayerKey = null;
  }
  state.selectedImpactKey = key;
  updateImpactSelectionStyles();

  let entry = state.featureIndex.get(featureIndexKey(objectType, objectId));
  if (!entry) {
    const filters = filtersWithObjectIds(objectType, {}, [objectId]);
    const detailLayerKey = layerKey(objectType, filters);
    try {
      await loadObject(objectType, {}, {
        fit: false,
        label: `${impactTypeLabel(objectType)} ${objectId}`,
        objectIds: [objectId],
      });
    } catch (error) {
      addTrace("MISS", "受影响对象加载失败", String(error?.message || error));
      clearImpactObjectSelection({ removeLayer: true });
      updateImpactSelectionStyles();
      return;
    }
    if (focusSeq !== state.impactFocusSeq || state.selectedImpactKey !== key) {
      if (state.layerGroups.has(detailLayerKey)) removeLayer(detailLayerKey);
      return;
    }
    state.selectedImpactLayerKey = detailLayerKey;
    entry = state.featureIndex.get(featureIndexKey(objectType, objectId));
  }
  if (!entry) {
    addTrace("MISS", "未找到受影响对象", `${objectType} ${objectId}`);
    clearImpactObjectSelection({ removeLayer: true });
    updateImpactSelectionStyles();
    return;
  }
  selectFeature(objectType, entry.feature, entry.layer);
  entry.layer.setPopupContent?.(impactPopupHtml(impact));
  entry.layer.openPopup?.();
  document.getElementById("selectedObject").innerHTML = impactDetailHtml(impact, entry.feature?.properties || {});
  fitFeatureLayer(entry.layer);
}

function updateImpactSelectionStyles() {
  state.impactMarkers.forEach((marker, key) => {
    const impact = marker._impactData;
    if (!impact) return;
    const selected = key === state.selectedImpactKey;
    marker.setIcon(impactMarkerIcon(impact, selected));
    marker.setZIndexOffset(selected ? 900 : 0);
  });
  document.querySelectorAll(".impact-list-item").forEach((item) => {
    item.classList.toggle("is-selected", item.dataset.impactKey === state.selectedImpactKey);
  });
}

function updateSelectedImpactDetails(impacts) {
  if (!state.selectedImpactKey) return;
  const impact = impacts.find((item) => impactObjectKey(item) === state.selectedImpactKey);
  if (!impact) return;
  const entry = state.featureIndex.get(featureIndexKey(impact.object_type, impact.object_id));
  if (!entry) return;
  entry.layer.setPopupContent?.(impactPopupHtml(impact));
  document.getElementById("selectedObject").innerHTML = impactDetailHtml(impact, entry.feature?.properties || {});
}

function clearImpactObjectSelection(options = {}) {
  const selectedKey = state.selectedImpactKey;
  state.selectedImpactKey = null;
  state.impactFocusSeq += 1;
  state.map?.closePopup();
  if (options.removeLayer && state.selectedImpactLayerKey) {
    const key = state.selectedImpactLayerKey;
    state.selectedImpactLayerKey = null;
    if (state.layerGroups.has(key)) removeLayer(key);
  }
  if (selectedKey && state.selected && impactObjectKey(state.selected) === selectedKey) {
    clearFocus();
    state.selected = null;
    const selected = document.getElementById("selectedObject");
    if (selected) selected.innerHTML = '<span class="muted">未选中</span>';
  }
}

function impactObjectKey(impact) {
  return `${impact?.object_type || ""}:${String(impact?.object_id ?? impact?.id ?? "")}`;
}

function impactTypeLabel(objectType) {
  return OBJECT_CONFIG[objectType]?.label || objectType || "领域对象";
}

function impactRiskLabel(level) {
  return {
    critical: "极高风险",
    high: "高风险",
    medium: "中风险",
    low: "低风险",
  }[level] || "受影响";
}

function impactPopupHtml(impact) {
  return `
    <div class="popup-title">${escapeHtml(impact.name || impact.object_id)}</div>
    <div class="popup-meta">${escapeHtml(impactTypeLabel(impact.object_type))} ${escapeHtml(impact.object_id)}</div>
    <div class="popup-depth">${formatImpactNumber(impact.depth_m, 2)} <span>m 水深</span></div>
    <div class="popup-meta">${escapeHtml(impactRiskLabel(impact.risk_level))} · 流速 ${formatImpactNumber(impact.velocity_mps, 2)} m/s · 距网格 ${formatImpactNumber(impact.distance_m, 1)} m</div>
  `;
}

function impactDetailHtml(impact, props) {
  return `
    <div class="impact-selected-summary">
      <strong>${escapeHtml(impactRiskLabel(impact.risk_level))}</strong>
      <span>水深 ${formatImpactNumber(impact.depth_m, 2)} m</span>
      <span>流速 ${formatImpactNumber(impact.velocity_mps, 2)} m/s</span>
    </div>
    ${detailHtml(impact.object_type, props)}
  `;
}

function formatImpactNumber(value, digits) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "--";
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
