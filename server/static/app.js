const BOUNDARY_FLOW_KEYS = ["interval1", "interval2", "tonggu", "upstream"];
const BOUNDARY_FLOW_HISTORY_LIMIT = 48;
const STATION_RAINFALL_HISTORY_LIMIT = 24;
const RESERVOIR_TELEMETRY_HISTORY_LIMIT = 24;
const LONGTAN_RESERVOIR_STATION_ID = "HP0014511220000128";
const DEFAULT_BASEMAP_KEY = "satellite";
const BOUNDARY_FLOW_COLORS = {
  interval1: "#1f7a5c",
  interval2: "#2878b9",
  tonggu: "#a15f13",
  upstream: "#b43e52",
};
const BOUNDARY_FLOW_LABELS = {
  interval1: "区间 1",
  interval2: "区间 2",
  tonggu: "同古河",
  upstream: "坝址",
};

const state = {
  map: null,
  watershedRenderer: null,
  baseLayer: null,
  basemapKey: DEFAULT_BASEMAP_KEY,
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
  mapSelections: new Map(),
  mapActionReceipts: new Map(),
  mapActionQueue: Promise.resolve(),
  mapGeneration: 0,
  bootstrap: null,
  baseBounds: null,
  launchCoverVisible: true,
  launchCoverLayoutTimer: null,
  sessionId: getSessionId(),
  workspaceId: null,
  runtimeStatus: {},
  directives: [],
  directiveDraft: null,
  directiveToast: null,
  activeStream: null,
  activeRunId: null,
  pendingQuestion: null,
  inundationAlertActive: false,
  autonomyStream: null,
  eventMarkers: new Map(),
  hydrodynamicGridMeta: null,
  hydrodynamicResultMeta: null,
  hydrodynamicResultLoadToken: 0,
  lastTrace: null,
  playbackRunning: false,
  playbackPaused: false,
  playbackProcessing: false,
  playbackAutoPauseEnabled: true,
  playbackPhase: "ready",
  playbackSpeed: 20,
  playbackStepPending: false,
  playbackTotalRows: 0,
  playbackSource: null,
  playbackSources: [],
  playbackSourceMenuOpen: false,
  playbackLongPressTriggered: false,
  lastMockObservation: null,
  rainfallForecast: [],
  basinRainfallHistory: [],
  basinTab: "rain",
  boundaryFlowForecast: null,
  mapTimeContext: {
    mode: "current",
    currentAt: null,
    validAt: null,
    hour: null,
  },
  stationRainfall: new Map(),
  stationRainfallObservedAt: null,
  stationRainfallHistory: new Map(),
  stationRainfallForecast: new Map(),
  stationRainfallLayerInitialized: false,
  reservoirTelemetry: null,
  reservoirDispatchTime: null,
  reservoirDispatchChart: "flow",
  reservoirTelemetryObservedAt: null,
  reservoirTelemetryHistory: [],
  reservoirForecast: null,
  reservoirAssessment: null,
  reservoirStationLayerInitialized: false,
  rainEffect: {
    canvas: null,
    context: null,
    width: 0,
    height: 0,
    dpr: 1,
    particles: [],
    intensity: 0,
    targetIntensity: 0,
    frame: null,
    lastFrameAt: 0,
    resizeObserver: null,
    motionQuery: null,
    reducedMotion: false,
  },
  boundaryFlowHistory: {
    interval1: [],
    interval2: [],
    tonggu: [],
    upstream: [],
  },
  boundaryFlowHistoryTimes: [],
  boundaryFlowChartObserver: null,
  boundaryFlowChartFrame: null,
  agentNotification: null,
  autonomyTraceIds: new Set(),
  notificationChains: new Set(),
  hydrodynamicTimeline: {
    mode: "time_slice",
    hours: [],
    validTimes: [],
    rainfallSeries: [],
    index: 0,
    layer: null,
    key: null,
    baseFilters: null,
    resultVersion: null,
    forecastId: null,
    forecastVersion: null,
    forecastTime: null,
    validFrom: null,
    validTo: null,
    seekTimer: null,
    timer: null,
    playing: false,
  },
  impactAnalysis: null,
  impactRequested: false,
  impactListView: {
    tab: "affected",
    type: "all",
    expanded: new Map(),
    positions: new Map(),
    renderedKey: null,
  },
  impactMarkerLayer: null,
  impactMarkers: new Map(),
  roadNameLayers: new Set(),
  selectedImpactKey: null,
  selectedImpactTab: "affected",
  selectedImpactLayerKey: null,
  impactRoadFocusLayer: null,
  impactRoadFocusSeq: 0,
  impactFocusSeq: 0,
  impactRefreshTimer: null,
  impactRefreshController: null,
  impactRefreshSeq: 0,
  mapLayoutFrame: null,
  mapLayoutTimer: null,
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
  River: { label: "珊瑚河", color: "#0284c7" },
  Watershed: { label: "珊瑚河流域", color: "#1f2937" },
  County: { label: "县级边界", color: "#7b8794" },
  Town: { label: "乡镇边界", color: "#7a6a22" },
  Road: { label: "全部路段", color: "#facc15" },
  RoadRoute: { label: "编号道路", color: "#fb923c" },
  Reservoir: { label: "水库", color: "#0284c7" },
  Sluice: { label: "水闸", color: "#158a8a" },
  Bridge: { label: "桥梁", color: "#202833" },
  HydraulicStructure: { label: "其他水利工程", color: "#0f766e" },
  Facility: { label: "重要设施", color: "#d44a3a" },
  EvacuationSite: { label: "安置地点", color: "#24895d" },
  EvacuationUnit: { label: "转移单元", color: "#c97a12" },
  EvacuationRoute: { label: "转移路线", color: "#d44a3a" },
  DangerArea: { label: "危险区", color: "#b91c1c" },
  Station: { label: "测站", color: "#0284c7" },
  InundationForecastCell: { label: "淹没预测单元", color: "#dc2626" },
  HydrodynamicGridCell: { label: "水动力网格单元", color: "#64748b" },
  ForecastResult: { label: "预测淹没范围", color: "#dc2626" },
};

const ROAD_STYLES = {
  expressway: { label: "高速（含匝道）", color: "#fb923c", outline: "#7c2d12", weight: 4.5 },
  other: { label: "非高速", color: "#facc15", outline: "#422006", weight: 2.8 },
};

const OBJECT_LAYER_GROUPS = [
  {
    label: "水系与监测",
    objectTypes: ["River", "Watershed", "Reservoir", "Sluice"],
    filterControl: "station",
  },
  {
    label: "风险与应急",
    objectTypes: ["DangerArea", "Road", "RoadRoute", "Bridge", "EvacuationSite"],
    filterControl: "facility",
  },
  {
    label: "洪水预测",
    objectTypes: ["ForecastResult", "HydrodynamicGridCell"],
  },
  {
    label: "行政边界",
    objectTypes: ["County", "Town"],
  },
];

const ID_FIELDS = {
  River: "river_id",
  Watershed: "watershed_id",
  County: "county_id",
  Town: "town_id",
  Road: "road_id",
  RoadRoute: "road_route_id",
  Reservoir: "reservoir_id",
  Sluice: "sluice_id",
  Bridge: "bridge_id",
  HydraulicStructure: "structure_id",
  Facility: "facility_id",
  EvacuationSite: "evacuation_site_id",
  EvacuationUnit: "evacuation_unit_id",
  EvacuationRoute: "evacuation_route_id",
  DangerArea: "danger_area_id",
  Station: "station_id",
  InundationForecastCell: "forecast_cell_id",
  HydrodynamicGridCell: "hydrodynamic_cell_id",
};

const MAP_NON_SELECTABLE_OBJECTS = new Set(["Watershed", "County", "Town"]);
const ICON_OBJECT_TYPES = new Set([
  "Reservoir",
  "Sluice",
  "Bridge",
  "Facility",
  "EvacuationSite",
  "EvacuationUnit",
  "DangerArea",
  "Station",
]);
const MAP_CONTEXT_BASE_OBJECT_TYPES = new Set(["River", "Watershed"]);
const MAP_CONTEXT_HYDRODYNAMIC_TYPES = new Set([
  "HydrodynamicGridCell",
  "HydrodynamicResult",
]);
const DEFAULT_OBJECT_LAYERS = [
  { objectType: "Watershed", fit: true },
  { objectType: "River", fit: false },
  { objectType: "Reservoir", fit: false },
];

document.addEventListener("DOMContentLoaded", async () => {
  initLaunchCover();
  initMap();
  bindEvents();
  initDraggableMapPanels();
  initAgentResize();
  initBoundaryFlowHistoryChart();
  renderIcons();
  await bootstrap();
  await refreshDirectiveHistory();
  await loadDefaultObjectLayers();
  startAutonomyStream();
  await refreshPlaybackStatus();
  renderIcons();
});

function initLaunchCover() {
  const cover = document.getElementById("launchCover");
  const appShell = document.getElementById("appShell");
  const enterButton = document.getElementById("enterWorkbenchBtn");
  const returnButton = document.getElementById("coverReturnBtn");
  if (!cover || !appShell || !enterButton || !returnButton) return;

  const setVisible = (visible, { focus = true } = {}) => {
    state.launchCoverVisible = visible;
    cover.classList.toggle("is-dismissed", !visible);
    cover.setAttribute("aria-hidden", String(!visible));
    document.body.classList.toggle("is-cover-visible", visible);
    appShell.inert = visible;
    if (visible) {
      collapseWorkbenchForLaunchCover();
      window.requestAnimationFrame(() => {
        fitWatershedForLaunchCover(true);
        if (focus) cover.focus({ preventScroll: true });
      });
      return;
    }
    window.requestAnimationFrame(() => {
      state.map?.invalidateSize({ animate: false });
      fitWatershedForLaunchCover(false);
      if (focus) returnButton.focus({ preventScroll: true });
    });
  };

  enterButton.addEventListener("click", () => setVisible(false));
  returnButton.addEventListener("click", () => setVisible(true));
  window.addEventListener("resize", () => {
    if (!state.launchCoverVisible) return;
    if (state.launchCoverLayoutTimer) window.clearTimeout(state.launchCoverLayoutTimer);
    state.launchCoverLayoutTimer = window.setTimeout(() => {
      state.launchCoverLayoutTimer = null;
      fitWatershedForLaunchCover(true, { animate: false });
    }, 120);
  });
  setVisible(true, { focus: true });
}

function collapseWorkbenchForLaunchCover() {
  const workbench = document.getElementById("situationWorkbench");
  if (workbench && !workbench.classList.contains("is-collapsed")) {
    setTelemetryPanelOpen(false);
  }
  const appShell = document.getElementById("appShell");
  if (appShell?.classList.contains("is-agent-open")) {
    setAgentDrawerOpen(false);
  }
}

function fitWatershedForLaunchCover(visible, { animate = true } = {}) {
  if (!state.map || !state.baseBounds?.isValid()) return;
  const mapElement = document.getElementById("map");
  const mapRect = mapElement?.getBoundingClientRect();
  if (!mapRect?.width || !mapRect?.height) return;

  state.map.invalidateSize({ pan: false, animate: false });
  const fitOptions = {
    animate,
    duration: animate ? 0.65 : 0,
    easeLinearity: 0.22,
  };
  if (!visible) {
    const workbenchPadding = L.point(56, 56);
    const fittedZoom = Math.floor(state.map.getBoundsZoom(
      state.baseBounds.pad(0.04),
      false,
      workbenchPadding,
    ));
    state.map.setView(
      state.baseBounds.getCenter(),
      fittedZoom + 0.25,
      fitOptions,
    );
    return;
  }

  if (mapRect.width < 861) {
    state.map.fitBounds(state.baseBounds.pad(0.08), {
      ...fitOptions,
      padding: [34, 34],
    });
    return;
  }

  const copyRect = document.querySelector(".launch-cover-copy")?.getBoundingClientRect();
  const copyRight = copyRect?.right || mapRect.left + mapRect.width * 0.5;
  const leftPadding = Math.round(Math.min(
    mapRect.width * 0.58,
    Math.max(mapRect.width * 0.48, copyRight - mapRect.left + 44),
  ));
  state.map.fitBounds(state.baseBounds.pad(0.04), {
    ...fitOptions,
    paddingTopLeft: [leftPadding, 64],
    paddingBottomRight: [48, 48],
  });
}

async function loadDefaultObjectLayers() {
  for (const layer of DEFAULT_OBJECT_LAYERS) {
    await loadObject(layer.objectType, defaultObjectFilters(layer.objectType), { fit: layer.fit });
  }
}

function initMap() {
  state.map = L.map("map", {
    crs: AMAP_CRS,
    zoomControl: false,
    zoomSnap: 0.25,
    preferCanvas: true,
  }).setView([24.4, 111.35], 10);
  state.map.attributionControl.setPrefix(false);
  state.watershedRenderer = L.svg({ padding: 0.25 });

  state.map.createPane("impactPane");
  state.map.getPane("impactPane").style.zIndex = "475";
  state.map.createPane("riverPane");
  state.map.getPane("riverPane").style.zIndex = "410";
  state.map.createPane("riverMarkerPane");
  state.map.getPane("riverMarkerPane").style.zIndex = "430";
  state.impactMarkerLayer = L.layerGroup().addTo(state.map);
  L.control.zoom({ position: "bottomleft" }).addTo(state.map);
  state.map.on("popupopen", (event) => {
    const isStationRainfall = Boolean(
      event.popup.getElement()?.querySelector(
        ".station-rainfall-panel, .station-reservoir-panel",
      ),
    );
    window.requestAnimationFrame(syncStationPopupOpenState);
    if (isStationRainfall) {
      window.requestAnimationFrame(() => keepPopupInsideMap(event.popup));
    }
  });
  state.map.on("popupclose", () => {
    window.requestAnimationFrame(syncStationPopupOpenState);
  });
  state.map.on("zoomend moveend resize", refreshRoadNameLabels);
  state.map.on("resize", () => window.requestAnimationFrame(() => {
    let lastPopup = null;
    state.map.eachLayer((layer) => {
      if (layer instanceof L.Popup && layer.isOpen()) {
        layer.update();
        lastPopup = layer;
      }
    });
    if (lastPopup) keepPopupInsideMap(lastPopup);
  }));
  setBasemap(readStoredBasemap(), { persist: false });
  initRainEffect();
}

function syncStationPopupOpenState() {
  const hasStationPopup = Boolean(
    state.map?.getContainer()?.querySelector(
      ".leaflet-popup .station-rainfall-panel, .leaflet-popup .station-reservoir-panel",
    ),
  );
  document.querySelector(".map-stage")
    ?.classList.toggle("is-station-popup-open", hasStationPopup);
}

function keepPopupInsideMap(popup) {
  const element = popup.getElement();
  const mapElement = state.map?.getContainer();
  if (!element || !mapElement) return;
  const popupRect = element.getBoundingClientRect();
  const mapRect = mapElement.getBoundingClientRect();
  const padding = 12;
  const topPadding = element.querySelector(".dispatch-panel") ? 64 : padding;
  let offsetX = 0;
  let offsetY = 0;
  if (popupRect.left < mapRect.left + padding) {
    offsetX = popupRect.left - mapRect.left - padding;
  } else if (popupRect.right > mapRect.right - padding) {
    offsetX = popupRect.right - mapRect.right + padding;
  }
  if (popupRect.top < mapRect.top + topPadding) {
    offsetY = popupRect.top - mapRect.top - topPadding;
  } else if (popupRect.bottom > mapRect.bottom - padding) {
    offsetY = popupRect.bottom - mapRect.bottom + padding;
  }
  if (offsetX || offsetY) state.map.panBy([offsetX, offsetY], { animate: true });
}

function initRainEffect() {
  const rain = state.rainEffect;
  const canvas = document.getElementById("rainEffectCanvas");
  if (!canvas) return;
  rain.canvas = canvas;
  rain.context = canvas.getContext("2d", { alpha: true });
  rain.motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  rain.reducedMotion = rain.motionQuery.matches;
  const handleMotionPreference = (event) => {
    rain.reducedMotion = event.matches;
    if (rain.reducedMotion) {
      stopRainAnimation({ clear: true });
    } else if (rain.targetIntensity > 0) {
      startRainAnimation();
    }
  };
  rain.motionQuery.addEventListener?.("change", handleMotionPreference);
  rain.resizeObserver = new ResizeObserver(resizeRainCanvas);
  rain.resizeObserver.observe(state.map.getContainer());
  document.addEventListener("visibilitychange", handleRainVisibilityChange);
  resizeRainCanvas();
}

function resizeRainCanvas() {
  const rain = state.rainEffect;
  if (!rain.canvas || !rain.context) return;
  const rect = state.map.getContainer().getBoundingClientRect();
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  const dpr = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
  if (rain.width === width && rain.height === height && rain.dpr === dpr) return;
  rain.width = width;
  rain.height = height;
  rain.dpr = dpr;
  rain.canvas.width = Math.round(width * dpr);
  rain.canvas.height = Math.round(height * dpr);
  rain.context.setTransform(dpr, 0, 0, dpr, 0, 0);
  rain.particles = [];
  ensureRainParticles();
}

function setRainfallEffect(rainfallMm) {
  const rainfall = Math.max(0, Number(rainfallMm) || 0);
  const rain = state.rainEffect;
  rain.targetIntensity = rainfall > 0
    ? Math.min(1, Math.max(0.12, Math.sqrt(rainfall / 25)))
    : 0;
  ensureRainParticles();
  if (rain.targetIntensity > 0 && !rain.reducedMotion && !document.hidden) {
    startRainAnimation();
  } else if (rain.targetIntensity === 0 && !rain.reducedMotion && !document.hidden && rain.intensity > 0.005) {
    startRainAnimation();
  } else if (rain.targetIntensity === 0 || rain.reducedMotion) {
    stopRainAnimation({ clear: true });
  }
}

function ensureRainParticles() {
  const rain = state.rainEffect;
  if (!rain.width || !rain.height) return;
  const targetCount = Math.min(
    520,
    Math.max(120, Math.round(rain.width * rain.height / 1800)),
  );
  while (rain.particles.length < targetCount) {
    rain.particles.push(createRainParticle(true));
  }
  if (rain.particles.length > targetCount) {
    rain.particles.length = targetCount;
  }
}

function createRainParticle(anywhere = false) {
  const rain = state.rainEffect;
  return {
    x: Math.random() * rain.width,
    y: anywhere ? Math.random() * rain.height : -20 - Math.random() * rain.height * 0.2,
    speed: 360 + Math.random() * 360,
    length: 7 + Math.random() * 13,
    drift: -45 - Math.random() * 55,
    opacity: 0.24 + Math.random() * 0.46,
  };
}

function startRainAnimation() {
  const rain = state.rainEffect;
  if (rain.frame || rain.reducedMotion || document.hidden || !rain.context) return;
  rain.canvas?.classList.add("is-active");
  rain.lastFrameAt = performance.now();
  rain.frame = window.requestAnimationFrame(drawRainFrame);
}

function drawRainFrame(now) {
  const rain = state.rainEffect;
  rain.frame = null;
  if (!rain.context || rain.reducedMotion || document.hidden) return;
  const deltaMs = Math.min(40, Math.max(0, now - rain.lastFrameAt));
  const deltaSeconds = deltaMs / 1000;
  rain.lastFrameAt = now;
  const smoothing = Math.min(1, deltaMs / 650);
  rain.intensity += (rain.targetIntensity - rain.intensity) * smoothing;
  if (Math.abs(rain.targetIntensity - rain.intensity) < 0.003) {
    rain.intensity = rain.targetIntensity;
  }

  const context = rain.context;
  context.clearRect(0, 0, rain.width, rain.height);
  if (rain.intensity > 0.005) {
    context.fillStyle = `rgba(72, 98, 120, ${0.055 * rain.intensity})`;
    context.fillRect(0, 0, rain.width, rain.height);
    const activeCount = Math.max(
      1,
      Math.round(rain.particles.length * (0.12 + rain.intensity * 0.88)),
    );
    context.lineWidth = 0.7 + rain.intensity * 0.55;
    context.lineCap = "round";
    for (let index = 0; index < activeCount; index += 1) {
      let particle = rain.particles[index];
      const speedFactor = 0.78 + rain.intensity * 0.52;
      particle.x += particle.drift * speedFactor * deltaSeconds;
      particle.y += particle.speed * speedFactor * deltaSeconds;
      if (
        particle.y > rain.height + particle.length
        || particle.x < -particle.length * 3
      ) {
        particle = createRainParticle(false);
        particle.x = Math.random() * (rain.width + 80);
        rain.particles[index] = particle;
      }
      const trailSeconds = 0.024 + rain.intensity * 0.012;
      context.strokeStyle = `rgba(188, 222, 242, ${particle.opacity * (0.45 + rain.intensity * 0.55)})`;
      context.beginPath();
      context.moveTo(particle.x, particle.y);
      context.lineTo(
        particle.x - particle.drift * trailSeconds,
        particle.y - Math.max(particle.length, particle.speed * trailSeconds),
      );
      context.stroke();
    }
  }

  if (rain.targetIntensity > 0 || rain.intensity > 0.005) {
    rain.frame = window.requestAnimationFrame(drawRainFrame);
  } else {
    stopRainAnimation({ clear: true });
  }
}

function stopRainAnimation({ clear = false } = {}) {
  const rain = state.rainEffect;
  if (rain.frame) window.cancelAnimationFrame(rain.frame);
  rain.frame = null;
  rain.lastFrameAt = 0;
  if (!clear) return;
  rain.intensity = 0;
  rain.context?.clearRect(0, 0, rain.width, rain.height);
  rain.canvas?.classList.remove("is-active");
}

function handleRainVisibilityChange() {
  if (document.hidden) {
    stopRainAnimation();
  } else if (state.rainEffect.targetIntensity > 0) {
    startRainAnimation();
  }
}

function setBasemap(key, options = {}) {
  const nextKey = BASEMAPS[key] ? key : DEFAULT_BASEMAP_KEY;
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
    const value = window.localStorage.getItem(BASEMAP_STORAGE_KEY) || DEFAULT_BASEMAP_KEY;
    const migrated = { light: "standard", terrain: "hybrid" }[value] || value;
    return BASEMAPS[migrated] ? migrated : DEFAULT_BASEMAP_KEY;
  } catch {
    return DEFAULT_BASEMAP_KEY;
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
  if (state.bootstrap.id_fields && typeof state.bootstrap.id_fields === "object") {
    Object.assign(ID_FIELDS, state.bootstrap.id_fields);
  }
  if (state.bootstrap.title) document.title = state.bootstrap.title;
  state.workspaceId = state.bootstrap.workspace_id || null;
  updateMapContentContext();
  renderObjectList();
}

function deriveMapContentContext() {
  const temporalLabel = mapTemporalContextLabel();
  if (!state.map) return `${temporalLabel} · 领域对象地图`;

  const visibleTypes = new Set();
  state.layerMeta.forEach((meta, key) => {
    const layer = state.layerGroups.get(key);
    if (layer && state.map.hasLayer(layer) && meta?.objectType) {
      visibleTypes.add(meta.objectType);
    }
  });

  const labels = [];
  const addLabel = (label) => {
    if (label && !labels.includes(label)) labels.push(label);
  };

  if (state.inundationAlertActive) addLabel("24小时淹没警戒");

  if (visibleTypes.has("HydrodynamicResult")) {
    addLabel("预测淹没");
  } else if (visibleTypes.has("HydrodynamicGridCell")) {
    addLabel("水动力网格");
  }
  const hasVisibleEventMarker = Array.from(state.eventMarkers.values())
    .some((marker) => state.map.hasLayer(marker));
  const hasVisibleImpactMarker = Array.from(state.impactMarkers.values())
    .some((marker) => state.map.hasLayer(marker));
  if (hasVisibleEventMarker) addLabel("事件告警");
  if (hasVisibleImpactMarker) addLabel("影响对象");

  Object.keys(OBJECT_CONFIG).forEach((objectType) => {
    if (
      !visibleTypes.has(objectType)
      || MAP_CONTEXT_BASE_OBJECT_TYPES.has(objectType)
      || MAP_CONTEXT_HYDRODYNAMIC_TYPES.has(objectType)
    ) return;
    addLabel(OBJECT_CONFIG[objectType]?.label || objectType);
  });

  const knownTypes = new Set(Object.keys(OBJECT_CONFIG));
  Array.from(visibleTypes)
    .filter((objectType) => (
      !knownTypes.has(objectType)
      && !MAP_CONTEXT_BASE_OBJECT_TYPES.has(objectType)
      && !MAP_CONTEXT_HYDRODYNAMIC_TYPES.has(objectType)
    ))
    .sort()
    .forEach(addLabel);

  if (!labels.length) return `${temporalLabel} · 领域对象地图`;
  const visibleLabels = labels.slice(0, 2).join("、");
  const overflow = labels.length > 2 ? `等${labels.length}类` : "";
  return `${temporalLabel} · ${visibleLabels}${overflow}`;
}

function mapTemporalContextLabel() {
  const context = state.mapTimeContext || {};
  if (context.mode === "envelope") return "未来24小时 · 最大淹没包络";
  if (context.mode === "time_slice") {
    const actual = formatForecastActualTime(context.validAt, true);
    const offset = Number.isFinite(Number(context.hour))
      ? `预测 +${formatHydrodynamicHour(context.hour)}h`
      : "预测时刻";
    return actual ? `${offset} · ${actual}` : offset;
  }
  const current = formatForecastActualTime(context.currentAt, true);
  return current ? `当前模拟 · ${current}` : "基础态";
}

function setMapTimeContext(context = {}) {
  const hasHour = context.hour !== null
    && context.hour !== undefined
    && Number.isFinite(Number(context.hour));
  state.mapTimeContext = {
    mode: context.mode || "current",
    currentAt: context.currentAt ?? state.lastMockObservation?.simulation_time
      ?? state.lastMockObservation?.observed_at
      ?? null,
    validAt: context.validAt ?? null,
    hour: hasHour ? Number(context.hour) : null,
  };
  applyMapTimeContext();
}

function applyMapTimeContext() {
  setRainfallEffect(mapRainfallForContext());
  refreshStationTelemetryMarkers();
  updateMapContentContext();
}

function updateMapContentContext() {
  const pill = document.getElementById("contextPill");
  if (pill) pill.textContent = deriveMapContentContext();
}

function renderObjectList() {
  const list = document.getElementById("objectList");
  list.innerHTML = "";

  OBJECT_LAYER_GROUPS.forEach((groupConfig) => {
    const group = document.createElement("section");
    group.className = "object-group";
    group.innerHTML = `<div class="object-group-title">${groupConfig.label}</div>`;
    const items = document.createElement("div");
    items.className = "object-group-items";
    groupConfig.objectTypes.forEach((objectType) => {
      items.appendChild(createObjectLayerButton(objectType));
    });
    if (groupConfig.filterControl === "station") items.appendChild(createStationFilterControl());
    if (groupConfig.filterControl === "facility") items.appendChild(createFacilityFilterControl());
    group.appendChild(items);
    list.appendChild(group);
  });
  syncFilteredLayerButtons();
}

function createObjectLayerButton(objectType) {
  const config = OBJECT_CONFIG[objectType];
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "object-row";
  btn.dataset.objectType = objectType;
  btn.innerHTML = `${layerObjectIcon(objectType)}<span>${config.label}</span>`;
  if (objectType === "Road" || objectType === "RoadRoute") {
    const legend = document.createElement("span");
    legend.className = "road-style-legend";
    legend.innerHTML = Object.values(ROAD_STYLES).map((style) => `
      <span><i aria-hidden="true" style="--road-color: ${style.color}; --road-outline: ${style.outline}; --road-width: ${style.weight}px"></i>${style.label}</span>
    `).join("");
    btn.appendChild(legend);
  }
  const active = hasLayerButtonType(objectType);
  btn.classList.toggle("active", active);
  btn.setAttribute("aria-pressed", String(active));
  btn.title = `显示或隐藏${config.label}`;
  btn.addEventListener("click", async () => {
    await toggleObject(objectType);
    if (window.matchMedia("(max-width: 900px)").matches) setLayerPanelOpen(false);
  });
  return btn;
}

function createStationFilterControl() {
  return createObjectFilterControl({
    label: "测站",
    objectType: "Station",
    dataKey: "station",
    filterKey: "station_type",
    options: [
      ["flash_flood", "山洪"],
      ["meteorological", "气象"],
      ["hydrological", "水文"],
      ["reservoir", "水库"],
    ],
    onToggle: toggleStation,
  });
}

function createFacilityFilterControl() {
  return createObjectFilterControl({
    label: "设施",
    objectType: "Facility",
    dataKey: "facility",
    filterKey: "facility_type",
    options: [["school", "学校"], ["hospital", "医院"], ["government", "政府"]],
    onToggle: toggleFacility,
  });
}

function createObjectFilterControl({ label, objectType, dataKey, filterKey, options, onToggle }) {
  const control = document.createElement("div");
  control.className = "object-filter-control";
  control.innerHTML = `<div class="object-filter-label">${layerObjectIcon(objectType)}<span>${label}</span></div>`;
  const segmented = document.createElement("div");
  segmented.className = "segmented";
  segmented.setAttribute("role", "group");
  segmented.setAttribute("aria-label", `${label}类型`);
  options.forEach(([value, optionLabel]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset[dataKey] = value;
    button.setAttribute("aria-pressed", "false");
    button.innerHTML = `${layerObjectIcon(objectType, { [filterKey]: value }, "layer-filter-icon")}<span>${optionLabel}</span>`;
    button.addEventListener("click", () => onToggle(value));
    segmented.appendChild(button);
  });
  control.appendChild(segmented);
  return control;
}

function layerObjectIcon(objectType, feature = {}, className = "layer-list-icon") {
  const info = objectIconInfo(objectType, feature);
  const symbol = window.FloodMapSymbols?.render(info.icon) || "";
  return `
    <span class="${className} object-symbol-${info.key}" title="${escapeHtml(info.label)}" aria-hidden="true">
      <span class="object-symbol-inner">${symbol}</span>
    </span>
  `;
}

function bindEvents() {
  document.querySelectorAll("[data-basin-tab]").forEach((button, index, buttons) => {
    button.addEventListener("click", () => setBasinTab(button.dataset.basinTab));
    button.addEventListener("keydown", (event) => {
      let next = index;
      if (event.key === "ArrowRight") next = (index + 1) % buttons.length;
      else if (event.key === "ArrowLeft") next = (index + buttons.length - 1) % buttons.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = buttons.length - 1;
      else return;
      event.preventDefault(); buttons[next].click(); buttons[next].focus();
    });
  });
  document.getElementById("runImpactAnalysis").addEventListener("click", () => {
    state.impactRequested = true;
    refreshImpactAnalysisForTimeline();
  });
  document.getElementById("openReservoirDispatch").addEventListener("click", async () => {
    try {
      await loadObject("Station", { station_type: "reservoir" }, { fit: false });
      await focusObject({ object_type: "Station", object_id: LONGTAN_RESERVOIR_STATION_ID });
    } catch (error) { addTrace("ERR", "水库定位失败", String(error)); }
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-dispatch-time], [data-dispatch-chart]");
    if (!button) return;
    event.preventDefault();
    if (button.dataset.dispatchChart) {
      state.reservoirDispatchChart = button.dataset.dispatchChart;
    } else {
      state.reservoirDispatchTime = button.dataset.dispatchTime || null;
    }
    refreshStationTelemetryMarkers();
    window.requestAnimationFrame(() => {
      state.featureIndex.forEach((entry) => {
        if (entry.layer.isPopupOpen?.()) keepPopupInsideMap(entry.layer.getPopup());
      });
    });
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-road-object-id]");
    if (!button) return;
    const objectType = button.dataset.roadObjectType;
    if (!["Road", "RoadRoute"].includes(objectType)) return;
    const objectId = button.dataset.roadObjectId;
    const result = state.impactAnalysis?.lastResult;
    const candidates = [...(result?.impacts || []), ...(result?.nearby_impacts || []), ...(result?.road_route_impacts || []), ...(result?.road_route_nearby_impacts || [])];
    const impact = [...candidates, ...candidates.flatMap((item) => [...(item.segment_impacts || []), ...(item.nearby_segment_impacts || [])])]
      .find((item) => item.object_type === objectType && String(item.object_id) === objectId);
    void (impact ? focusImpactObject(impact) : focusObject({ object_type: objectType, object_id: objectId }))
      .catch((error) => addTrace("MISS", "道路定位失败", String(error?.message || error)));
  });
  const directiveToast = document.getElementById("directiveDraftToast");
  state.directiveToast = {
    id: "directive-draft",
    dragX: 0,
    dragY: 0,
    element: directiveToast,
  };
  bindConclusionToastDrag(state.directiveToast);
  document.getElementById("fitAllBtn").addEventListener("click", fitAll);
  document.getElementById("layerPanelBtn").addEventListener("click", toggleLayerPanel);
  document.getElementById("layerPanelCloseBtn").addEventListener("click", () => setLayerPanelOpen(false));
  document.getElementById("situationToggleBtn").addEventListener("click", toggleTelemetryPanel);
  document.getElementById("telemetryFloatBtn").addEventListener("click", () => toggleSituationPanelFloating("telemetryPanel"));
  document.getElementById("impactFloatBtn").addEventListener("click", () => toggleSituationPanelFloating("impactPanel"));
  bindImpactListControls();
  document.getElementById("agentDrawerBtn").addEventListener("click", () => setAgentDrawerOpen(true));
  document.getElementById("agentCloseBtn").addEventListener("click", () => setAgentDrawerOpen(false));
  bindPlaybackSourceControls();
  document.getElementById("playbackRestartBtn").addEventListener("click", restartBoundaryFlowPlayback);
  document.getElementById("playbackStepBtn").addEventListener("click", stepBoundaryFlowPlayback);
  document.getElementById("playbackSpeedSelect").addEventListener("change", updatePlaybackSpeed);
  document.getElementById("playbackAutoPauseSwitch").addEventListener("change", updatePlaybackAutoPause);
  document.getElementById("hydroPlayBtn").addEventListener("click", toggleHydrodynamicTimelinePlayback);
  document.getElementById("hydroTimeSlider").addEventListener("input", (event) => {
    scheduleHydrodynamicTimelineIndex(Number(event.target.value || 0));
  });
  document.getElementById("hydroTimeSlider").addEventListener("change", (event) => {
    flushHydrodynamicTimelineIndex(Number(event.target.value || 0));
  });
  document.querySelectorAll("[data-basemap]").forEach((button) => {
    button.addEventListener("click", () => setBasemap(button.dataset.basemap));
  });
  document.querySelectorAll("[data-layer-tab]").forEach((button) => {
    button.addEventListener("click", () => setLayerPanelTab(button.dataset.layerTab));
  });
  document.querySelectorAll("[data-panel-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      activateAgentPane(btn.dataset.panelToggle);
      if (btn.dataset.panelToggle === "directive") refreshDirectiveHistory();
    });
  });
  document.getElementById("chatInput").addEventListener("focus", () => {
    setAgentDrawerOpen(true);
    activateAgentPane("chat");
  });
  document.getElementById("chatForm").addEventListener("submit", onChatSubmit);
  ["directiveTitle", "directiveRecipients", "directiveContent"].forEach((id) => {
    document.getElementById(id).addEventListener("input", (event) => {
      event.target.setCustomValidity("");
      syncDirectiveDraft();
    });
  });
  document.getElementById("directivePriority").addEventListener("change", syncDirectiveDraft);
  document.getElementById("directiveCancelBtn").addEventListener("click", clearDirectiveDraft);
  document.getElementById("directiveCopyBtn").addEventListener("click", () => {
    copyDirectiveToDraft(state.directiveDraft?.directiveId);
  });
  document.getElementById("directiveIssueBtn").addEventListener("click", issueDirective);
  document.getElementById("directiveHistoryList").addEventListener("click", (event) => {
    const item = event.target.closest("[data-view-directive]");
    if (item) openIssuedDirective(item.dataset.viewDirective);
  });
  document.getElementById("chatLog").addEventListener("click", (event) => {
    if (event.target.closest("[data-open-directive-editor]")) {
      showDirectiveDraftToast();
    }
  });
}

function initDraggableMapPanels() {
  document.querySelectorAll(".map-draggable-panel").forEach((panel) => {
    const handle = panel.querySelector("[data-panel-drag-handle]");
    if (!handle) return;
    let drag = null;

    const finish = (event) => {
      if (!drag || (event?.pointerId != null && event.pointerId !== drag.pointerId)) return;
      if (handle.hasPointerCapture(drag.pointerId)) handle.releasePointerCapture(drag.pointerId);
      panel.classList.remove("is-dragging");
      drag = null;
    };

    handle.addEventListener("pointerdown", (event) => {
      if (!panel.classList.contains("is-floating")) return;
      if (event.pointerType === "mouse" && event.button !== 0) return;
      if (event.target.closest("button, a, input, select, textarea")) return;
      const rect = panel.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      drag = {
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        rect,
        x: Number(panel.dataset.dragX || 0),
        y: Number(panel.dataset.dragY || 0),
      };
      panel.classList.add("is-dragging");
      handle.setPointerCapture(event.pointerId);
      event.preventDefault();
      event.stopPropagation();
    });

    handle.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      const stage = panel.closest(".map-stage")?.getBoundingClientRect();
      if (!stage) return;
      const requestedLeft = drag.rect.left + event.clientX - drag.clientX;
      const requestedTop = drag.rect.top + event.clientY - drag.clientY;
      const left = clampPanelCoordinate(requestedLeft, stage.left + 8, stage.right - drag.rect.width - 8);
      const top = clampPanelCoordinate(requestedTop, stage.top + 8, stage.bottom - drag.rect.height - 8);
      setMapPanelTranslation(
        panel,
        drag.x + left - drag.rect.left,
        drag.y + top - drag.rect.top,
      );
      event.preventDefault();
      event.stopPropagation();
    });

    handle.addEventListener("pointerup", finish);
    handle.addEventListener("pointercancel", finish);
    handle.addEventListener("lostpointercapture", finish);
  });
  window.addEventListener("resize", () => {
    document.querySelectorAll(".map-draggable-panel.is-floating").forEach(clampMapPanelToStage);
    clampConclusionToastsToMap();
  });
}

function initAgentResize() {
  const panel = document.querySelector(".agent-panel");
  const handle = document.getElementById("agentResizeHandle");
  if (!panel || !handle) return;
  let resize = null;

  const finish = (event) => {
    if (!resize || (event?.pointerId != null && event.pointerId !== resize.pointerId)) return;
    if (handle.hasPointerCapture(resize.pointerId)) handle.releasePointerCapture(resize.pointerId);
    panel.classList.remove("is-resizing");
    resize = null;
    invalidateMapLayout();
  };

  handle.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    const mobile = window.matchMedia("(max-width: 900px)").matches;
    const rect = panel.getBoundingClientRect();
    resize = {
      pointerId: event.pointerId,
      mobile,
      clientX: event.clientX,
      clientY: event.clientY,
      width: rect.width,
      height: rect.height,
    };
    panel.classList.add("is-resizing");
    handle.setPointerCapture(event.pointerId);
    event.preventDefault();
  });

  handle.addEventListener("pointermove", (event) => {
    if (!resize || event.pointerId !== resize.pointerId) return;
    if (resize.mobile) {
      const height = clampPanelCoordinate(
        resize.height + resize.clientY - event.clientY,
        280,
        window.innerHeight * 0.9,
      );
      document.documentElement.style.setProperty("--agent-mobile-height", `${height}px`);
      handle.setAttribute("aria-valuenow", String(Math.round(height)));
    } else {
      const width = clampPanelCoordinate(
        resize.width + resize.clientX - event.clientX,
        340,
        Math.min(620, window.innerWidth * 0.56),
      );
      document.documentElement.style.setProperty("--agent-drawer-width", `${width}px`);
      handle.setAttribute("aria-valuenow", String(Math.round(width)));
    }
    invalidateMapLayout();
    event.preventDefault();
  });

  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", finish);
  handle.addEventListener("lostpointercapture", finish);
}

function clampPanelCoordinate(value, minimum, maximum) {
  if (maximum < minimum) return minimum;
  return Math.max(minimum, Math.min(maximum, value));
}

function setMapPanelTranslation(panel, x, y) {
  panel.dataset.dragX = String(x);
  panel.dataset.dragY = String(y);
  panel.style.setProperty("--panel-drag-x", `${x}px`);
  panel.style.setProperty("--panel-drag-y", `${y}px`);
}

function clampMapPanelToStage(panel) {
  const rect = panel.getBoundingClientRect();
  const stage = panel.closest(".map-stage")?.getBoundingClientRect();
  if (!stage || !rect.width || !rect.height) return;
  const left = clampPanelCoordinate(rect.left, stage.left + 8, stage.right - rect.width - 8);
  const top = clampPanelCoordinate(rect.top, stage.top + 8, stage.bottom - rect.height - 8);
  const x = Number(panel.dataset.dragX || 0) + left - rect.left;
  const y = Number(panel.dataset.dragY || 0) + top - rect.top;
  setMapPanelTranslation(panel, x, y);
}

function toggleLayerPanel() {
  const control = document.querySelector(".map-layer-control");
  const isOpen = !control.classList.contains("is-open");
  setLayerPanelOpen(isOpen);
}

function setLayerPanelOpen(isOpen) {
  const control = document.querySelector(".map-layer-control");
  const btn = document.getElementById("layerPanelBtn");
  control.classList.toggle("is-open", isOpen);
  btn.classList.toggle("is-active", isOpen);
  btn.setAttribute("aria-expanded", String(isOpen));
}

function setLayerPanelTab(name) {
  const active = name === "basemap" ? "basemap" : "objects";
  document.querySelectorAll("[data-layer-tab]").forEach((button) => {
    const selected = button.dataset.layerTab === active;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-selected", String(selected));
  });
  document.querySelectorAll("[data-layer-pane]").forEach((pane) => {
    pane.classList.toggle("is-active", pane.dataset.layerPane === active);
  });
}

function toggleTelemetryPanel() {
  const workbench = document.getElementById("situationWorkbench");
  const isOpen = workbench.classList.contains("is-collapsed");
  setTelemetryPanelOpen(isOpen);
}

function setTelemetryPanelOpen(isOpen) {
  const workbench = document.getElementById("situationWorkbench");
  const toggle = document.getElementById("situationToggleBtn");
  if (!isOpen) {
    document.querySelectorAll(".situation-pane.is-floating").forEach((panel) => {
      setSituationPanelFloating(panel, false);
    });
  }
  workbench.classList.toggle("is-collapsed", !isOpen);
  toggle.setAttribute("aria-expanded", String(isOpen));
  toggle.title = isOpen ? "收起态势工作台" : "展开态势工作台";
  toggle.setAttribute("aria-label", toggle.title);
  toggle.innerHTML = `<i data-lucide="${isOpen ? "panel-bottom-close" : "panel-bottom-open"}"></i>`;
  renderIcons();
  invalidateMapLayout();
}

function revealSituationPanel(panelId, behavior = "smooth") {
  const body = document.querySelector(".situation-workbench-body");
  const panel = document.getElementById(panelId);
  if (!body || !panel) return;
  const panelStart = panel.offsetLeft;
  const panelEnd = panelStart + panel.offsetWidth;
  const viewportStart = body.scrollLeft;
  const viewportEnd = viewportStart + body.clientWidth;
  if (panelStart >= viewportStart && panelEnd <= viewportEnd) return;
  body.scrollTo({ left: panelStart, behavior });
}

function toggleSituationPanelFloating(panelId) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  setTelemetryPanelOpen(true);
  setSituationPanelFloating(panel, !panel.classList.contains("is-floating"));
}

function setSituationPanelFloating(panel, floating) {
  panel.classList.toggle("is-floating", floating);
  if (!floating) setMapPanelTranslation(panel, 0, 0);
  const button = panel.querySelector("#telemetryFloatBtn, #impactFloatBtn");
  if (button) {
    const label = panel.id === "impactPanel" ? "影响分析" : "演进数据";
    button.title = floating ? `停靠${label}` : `浮动${label}`;
    button.setAttribute("aria-label", button.title);
    button.innerHTML = `<i data-lucide="${floating ? "panel-bottom" : "picture-in-picture-2"}"></i>`;
  }
  renderIcons();
  if (floating) window.requestAnimationFrame(() => clampMapPanelToStage(panel));
  invalidateMapLayout();
}

function invalidateMapLayout() {
  if (state.mapLayoutFrame) window.cancelAnimationFrame(state.mapLayoutFrame);
  if (state.mapLayoutTimer) window.clearTimeout(state.mapLayoutTimer);
  state.mapLayoutFrame = window.requestAnimationFrame(() => {
    state.mapLayoutFrame = null;
    state.map?.invalidateSize({ pan: false });
    clampConclusionToastsToMap();
  });
  state.mapLayoutTimer = window.setTimeout(() => {
    state.mapLayoutTimer = null;
    state.map?.invalidateSize({ pan: false });
    clampConclusionToastsToMap();
  }, 240);
}

function setAgentDrawerOpen(isOpen) {
  const shell = document.querySelector(".app-shell");
  const panel = document.querySelector(".agent-panel");
  const btn = document.getElementById("agentDrawerBtn");
  shell.classList.toggle("is-agent-open", isOpen);
  panel.classList.toggle("is-open", isOpen);
  btn.classList.toggle("is-open", isOpen);
  btn.setAttribute("aria-expanded", String(isOpen));
  invalidateMapLayout();
}

async function toggleObject(objectType) {
  if (objectType === "HydrodynamicGridCell") {
    const key = layerKey("HydrodynamicGridCell", { result: "mesh" });
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

async function toggleFacility(type) {
  const matchingKeys = filteredLayerKeys("Facility", "facility_type", type);
  if (matchingKeys.length) {
    matchingKeys.forEach(removeLayer);
    return;
  }
  const filters = { facility_type: type };
  const labels = { school: "学校", hospital: "医院", government: "政府机构" };
  await loadObject("Facility", filters, { fit: true, label: labels[type] });
}

async function toggleStation(type) {
  const allKey = layerKey("Station", {});
  const matchingKeys = filteredLayerKeys("Station", "station_type", type);
  if (matchingKeys.length) {
    matchingKeys.forEach(removeLayer);
    return;
  }
  if (state.layerGroups.has(allKey)) removeLayer(allKey);
  const labels = {
    flash_flood: "山洪测站",
    meteorological: "气象测站",
    hydrological: "水文测站",
    reservoir: "水库测站",
  };
  await loadObject("Station", { station_type: type }, { fit: true, label: labels[type] });
}

function filteredLayerKeys(objectType, filterKey, value) {
  return Array.from(state.layerMeta.entries())
    .filter(([, meta]) => meta.objectType === objectType && meta.filters?.[filterKey] === value)
    .map(([key]) => key);
}

function defaultObjectFilters(objectType) {
  if (objectType === "InundationForecastCell") return { forecast_id: "latest" };
  if (objectType === "Reservoir") return { reservoir_id: "longtan" };
  return {};
}

async function loadObject(objectType, filters = {}, options = {}) {
  if (objectType === "HydrodynamicGridCell") {
    if (filters && Object.keys(filters).some((key) => ["forecast_id"].includes(key))) {
      throw new Error("Hydrodynamic results must use apply_hydrodynamic_result.");
    }
    return showHydrodynamicMesh(options);
  }
  const generation = state.mapGeneration;
  const requestedIds = options.objectIds ?? options.object_ids;
  if (Array.isArray(requestedIds) && !requestedIds.length) return null;
  const resolvedFilters = filtersWithObjectIds(objectType, filters, requestedIds);
  const key = layerKey(objectType, resolvedFilters);
  if (!options.refresh && options.mode !== "replace" && !options.replaceObjectIds && state.layerGroups.has(key)) {
    const existing = state.layerGroups.get(key);
    if (!state.map.hasLayer(existing)) existing.addTo(state.map);
    setObjectButtonActive(objectType, true);
    syncFilteredLayerButtons();
    if (options.fit) fitLayer(existing);
    registerMapSelection(existing, objectType, options, key);
    updateMapContentContext();
    return existing;
  }

  const params = new URLSearchParams({ object_type: objectType });
  params.set("filters", JSON.stringify(resolvedFilters));
  if (options.simplify_tolerance) params.set("simplify_tolerance", options.simplify_tolerance);

  const res = await fetch(`/api/geojson?${params.toString()}`);
  if (!res.ok) throw new Error(await res.text());
  const geojson = await res.json();
  if (generation !== state.mapGeneration) throw new Error("地图已重置，取消旧的显示请求");
  if (!geojson.features?.length) throw new Error("没有可显示的对象几何，原有图层已保留");
  const returnedIds = new Set(geojson.features.map(feature => String(feature.properties?.[ID_FIELDS[objectType]])));
  if (requestedIds?.some(id => !returnedIds.has(String(id)))) throw new Error("部分指定对象没有可显示的几何，原有图层已保留");
  if (options.replaceObjectIds) hideMapObjects({object_type: objectType, object_ids: options.replaceObjectIds});
  if (options.mode === "replace") removeObjectTypeLayers(objectType);
  else if (state.layerGroups.has(key)) removeLayer(key);
  const mapSelectable = !MAP_NON_SELECTABLE_OBJECTS.has(objectType);
  const layer = objectType === "River"
    ? createRiverLayer(geojson, mapSelectable)
    : objectType === "Reservoir"
      ? createReservoirLayer(geojson, mapSelectable)
      : ["Road", "RoadRoute"].includes(objectType)
        ? createRoadLayer(geojson, mapSelectable, objectType)
        : L.geoJSON(geojson, {
          interactive: mapSelectable,
          renderer: objectType === "Watershed" ? state.watershedRenderer : undefined,
          className: objectType === "Watershed" ? "watershed-boundary" : "",
          style: (feature) => featureStyle(objectType, feature),
          pointToLayer: (feature, latlng) => pointLayer(objectType, feature, latlng),
          onEachFeature: (feature, layerItem) => {
            if (!mapSelectable) return;
            indexFeature(objectType, feature, layerItem);
            layerItem.bindPopup(
              popupHtml(objectType, feature),
              objectPopupOptions(objectType),
            );
            layerItem.on("click", () => selectFeature(objectType, feature, layerItem));
          },
        });
  layer.addTo(state.map);
  renderIcons();

  state.layerGroups.set(key, layer);
  state.layerMeta.set(key, { objectType, filters: resolvedFilters, label: options.label || OBJECT_CONFIG[objectType]?.label || objectType });
  setObjectButtonActive(objectType, true);
  syncFilteredLayerButtons();
  if (objectType === "Watershed") state.baseBounds = layer.getBounds();
  if (options.fit) {
    if (objectType === "Watershed" && state.launchCoverVisible) {
      fitWatershedForLaunchCover(true, { animate: false });
    } else {
      fitLayer(layer);
    }
  }
  registerMapSelection(layer, objectType, options, key);
  updateMapContentContext();
  return layer;
}

function eachObjectFeature(group, callback) {
  if (group?.feature) { callback(group); return; }
  group?.eachLayer?.(child => eachObjectFeature(child, callback));
}

function visibleObjectIds(objectType) {
  const ids = new Set();
  for (const [key, layer] of state.layerGroups) {
    if (state.layerMeta.get(key)?.objectType !== objectType || !state.map.hasLayer(layer)) continue;
    eachObjectFeature(layer, item => {
      const id = item.feature?.properties?.[ID_FIELDS[objectType]];
      if (id != null) ids.add(String(id));
    });
  }
  return ids;
}

function registerMapSelection(layer, objectType, options, key) {
  const ids = [];
  eachObjectFeature(layer, item => {
    const id = item.feature?.properties?.[ID_FIELDS[objectType]];
    if (id != null) ids.push(String(id));
    if (options.highlight && !state.highlightedLayers.some(entry => entry.layer === item)) applyHighlight(item, objectType);
  });
  const selectionId = options.selectionId || state.layerMeta.get(key)?.selectionId || `selection_${crypto.randomUUID()}`;
  const label = options.label || OBJECT_CONFIG[objectType]?.label || objectType;
  state.mapSelections.set(selectionId, { objectType, objectIds: [...new Set(ids)], label, objectSetId: options.objectSetId });
  const meta = state.layerMeta.get(key);
  if (meta) meta.selectionId = selectionId;
}

function visibleMapSelections() {
  const byType = new Map();
  const selections = [];
  for (const [selectionId, selection] of state.mapSelections) {
    if (!byType.has(selection.objectType)) byType.set(selection.objectType, visibleObjectIds(selection.objectType));
    const visible = byType.get(selection.objectType);
    const ids = selection.objectIds.filter(id => visible.has(id));
    if (!ids.length) continue;
    selections.push({ selection_id: selectionId, object_type: selection.objectType,
      label: selection.label, count: ids.length, object_set_id: ids.length === selection.objectIds.length ? selection.objectSetId : undefined,
      ...(ids.length <= 10 ? {object_ids: ids} : {}) });
  }
  return selections.slice(-20);
}

function hideMapObjects(action) {
  const selection = action.selection_id ? state.mapSelections.get(action.selection_id) : null;
  if (action.selection_id && !selection) throw new Error("显示集合不存在，请根据当前地图重新选择");
  const objectType = selection?.objectType || action.object_type;
  const ids = new Set(selection?.objectIds ?? action.object_ids ?? visibleObjectIds(objectType));
  if (!ids.size) return;
  function prune(group) {
    for (const child of group.getLayers?.() || []) {
      const id = child.feature?.properties?.[ID_FIELDS[objectType]];
      if (id != null && ids.has(String(id))) {
        if (state.focusedLayer === child) clearFocus();
        group.removeLayer(child);
        for (const entry of state.roadNameLayers) { if (entry.layer === child) state.roadNameLayers.delete(entry); }
      } else if (!child.feature) prune(child);
    }
  }
  for (const [key, layer] of Array.from(state.layerGroups)) {
    const meta = state.layerMeta.get(key);
    if (meta?.objectType !== objectType) continue;
    const matches = [];
    eachObjectFeature(layer, child => { if (ids.has(String(child.feature?.properties?.[ID_FIELDS[objectType]]))) matches.push(child); });
    if (!matches.length) continue;
    prune(layer);
    const remaining = [];
    eachObjectFeature(layer, child => remaining.push(String(child.feature.properties[ID_FIELDS[objectType]])));
    if (!remaining.length) { removeLayer(key); continue; }
    // Rename the partial layer so a later full query reloads the hidden objects.
    const filters = filtersWithObjectIds(objectType, {}, remaining);
    const nextKey = `${layerKey(objectType, filters)}:visible:${crypto.randomUUID()}`;
    state.layerGroups.delete(key); state.layerMeta.delete(key);
    state.layerGroups.set(nextKey, layer); state.layerMeta.set(nextKey, {...meta, filters});
  }
  if (state.selected?.object_type === objectType && ids.has(String(state.selected.id))) state.selected = null;
  state.highlightedLayers = state.highlightedLayers.filter(({layer}) => state.map.hasLayer(layer));
  for (const key of state.featureIndex.keys()) {
    if (key.startsWith(`${objectType}:`)) state.featureIndex.delete(key);
  }
  for (const [key, layer] of state.layerGroups) {
    if (state.layerMeta.get(key)?.objectType === objectType) eachObjectFeature(layer, child => indexFeature(objectType, child.feature, child));
  }
  syncFilteredLayerButtons();
  updateMapContentContext();
}

function enqueueMapActions(data) {
  const generation = state.mapGeneration;
  const execute = async () => {
    const operationId = data.operation_id || data.output_id || (data.run_id && `${data.run_id}:${data.seq}`);
    if (operationId && state.mapActionReceipts.has(operationId)) return;
    if (generation !== state.mapGeneration) return;
    const receipt = { operation_id: operationId, status: "completed", actions: [] };
    try {
      for (const action of data.map_actions || []) {
        const targetType = action.object_type || state.mapSelections.get(action.selection_id)?.objectType;
        const before = targetType ? visibleObjectIds(targetType) : new Set();
        await executeActions([action]);
        const after = targetType ? visibleObjectIds(targetType) : new Set();
        receipt.actions.push({ type: action.type, object_type: targetType, selection_id: action.selection_id,
          ...(targetType ? {visible_count: after.size, added_count: [...after].filter(id => !before.has(id)).length,
          removed_count: [...before].filter(id => !after.has(id)).length} : {}) });
      }
      receipt.hydrodynamic_timeline = currentHydrodynamicTimelineContext();
      renderMetrics(data.result_cards || []);
      addTrace("MAP", "地图动作已执行", JSON.stringify(receipt, null, 2));
    } catch (error) {
      receipt.status = receipt.actions.length ? "partial" : "failed";
      receipt.error = error.message || String(error);
      const trace = addTrace("ERR", "地图动作执行失败", receipt.error);
      if (!data.replayed) notifyAgentTrace({tag: "ERR", label: "地图动作执行失败", notification_key: operationId}, trace);
    }
    if (operationId) {
      state.mapActionReceipts.set(operationId, receipt);
      if (data.run_id) {
        try {
          const response = await fetch("/api/agent/map-receipt", { method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({run_id: data.run_id, receipt}) });
          if (!response.ok) throw new Error(await response.text());
        } catch (error) { addTrace("ERR", "地图执行回执未送达", error.message); }
      }
    }
  };
  state.mapActionQueue = state.mapActionQueue.then(execute, execute);
  return state.mapActionQueue;
}

async function showHydrodynamicMesh(options = {}) {
  const objectType = "HydrodynamicGridCell";
  const resultFilters = { result: "mesh" };
  const key = layerKey(objectType, resultFilters);
  if (options.meshOnly) clearHydrodynamicResults();
  if (options.refresh && state.layerGroups.has(key)) removeLayer(key);
  if (state.layerGroups.has(key)) {
    const existing = state.layerGroups.get(key);
    if (!state.map.hasLayer(existing)) existing.addTo(state.map);
    setObjectButtonActive(objectType, true);
    if (options.fit) fitHydrodynamicGrid();
    updateMapContentContext();
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
    bounds: hydrodynamicLayerBounds(state.hydrodynamicGridMeta),
    noWrap: true,
    updateWhenIdle: true,
  }).addTo(state.map);
  state.layerGroups.set(key, layer);
  state.layerMeta.set(key, {
    objectType,
    buttonType: "HydrodynamicGridCell",
    filters: resultFilters,
    label: options.label || OBJECT_CONFIG[objectType].label,
  });
  setObjectButtonActive(objectType, true);
  if (options.fit) fitHydrodynamicGrid();
  updateMapContentContext();
  return layer;
}

async function applyHydrodynamicResult(options = {}) {
  const filters = options.filters || {};
  if (!Object.keys(filters).length) throw new Error("apply_hydrodynamic_result requires filters.");
  const key = layerKey("HydrodynamicResult", filters);
  const loadToken = ++state.hydrodynamicResultLoadToken;
  const timelineSelection = captureHydrodynamicTimelineSelection(key);
  if (!options.refresh && state.layerGroups.has(key)) {
    const existing = state.layerGroups.get(key);
    if (!state.map.hasLayer(existing)) existing.addTo(state.map);
    showHydrodynamicTimeline(state.hydrodynamicResultMeta, existing, key, filters, timelineSelection);
    if (options.fit) fitHydrodynamicResult();
    setObjectButtonActive(options.buttonType || "ForecastResult", true);
    updateMapContentContext();
    return existing;
  }

  const metaParams = new URLSearchParams(filters);
  const metaRes = await fetch(`/api/hydrodynamic-grid/meta?${metaParams.toString()}`);
  if (loadToken !== state.hydrodynamicResultLoadToken) {
    return state.layerGroups.get(key) || null;
  }
  if (!metaRes.ok) throw new Error(await metaRes.text());
  const resultMeta = await metaRes.json();
  if (loadToken !== state.hydrodynamicResultLoadToken) {
    return state.layerGroups.get(key) || null;
  }
  if (!resultMeta.forecast?.depth_path) throw new Error("没有可用的预测结果");
  const resultVersion = String(resultMeta?.forecast?.result_version || "");
  const forecast = resultMeta?.forecast || {};
  const initialSteps = hydrodynamicTimelineSteps(forecast);
  const initialHours = initialSteps.map((step) => step.hour);
  const initialIndex = resolveHydrodynamicTimelineIndex(
    initialHours,
    resultVersion,
    key,
    timelineSelection,
    filters,
  );
  if (options.refresh && state.layerGroups.has(key)) removeLayer(key);
  removeOtherHydrodynamicResultLayers(key);
  state.hydrodynamicResultMeta = resultMeta;
  const initialFilters = hydrodynamicTileFilters(filters, resultVersion);
  if (filters.view === "envelope") delete initialFilters.time_h;
  else if (initialHours.length) {
    initialFilters.time_h = formatHydrodynamicHour(initialHours[initialIndex]);
  }
  const layer = L.gridLayer.hydrodynamicGrid({
    tileSize: 256,
    opacity: 1,
    pane: "overlayPane",
    resultFilters: initialFilters,
    renderMode: "result",
    wetOnly: true,
    interactiveCells: true,
    minTileZoom: state.hydrodynamicResultMeta?.min_tile_zoom || 13,
    bounds: hydrodynamicLayerBounds(state.hydrodynamicResultMeta, true),
    noWrap: true,
    updateWhenIdle: true,
  }).addTo(state.map);
  state.layerGroups.set(key, layer);
  state.layerMeta.set(key, {
    objectType: "HydrodynamicResult",
    buttonType: options.buttonType || "ForecastResult",
    filters,
    label: options.label || "水动力结果",
  });
  showHydrodynamicTimeline(state.hydrodynamicResultMeta, layer, key, filters, timelineSelection);
  if (options.fit) fitHydrodynamicResult();
  setObjectButtonActive(options.buttonType || "ForecastResult", true);
  updateMapContentContext();
  return layer;
}

function removeOtherHydrodynamicResultLayers(activeKey) {
  Array.from(state.layerMeta.entries()).forEach(([key, meta]) => {
    if (key !== activeKey && meta?.objectType === "HydrodynamicResult") {
      removeLayer(key);
    }
  });
}

function showHydrodynamicTimeline(meta, layer, key, filters, previousSelection = null) {
  const forecast = (meta || {}).forecast || {};
  const steps = hydrodynamicTimelineSteps(forecast);
  if (!steps.length) {
    hideHydrodynamicTimeline();
    return;
  }
  const hours = steps.map((step) => step.hour);
  stopHydrodynamicTimelinePlayback({ refreshImpact: false });
  const resultVersion = String(forecast.result_version || "");
  const index = resolveHydrodynamicTimelineIndex(
    hours,
    resultVersion,
    key,
    previousSelection,
    filters,
  );
  const rainfallSeries = hydrodynamicRainfallSeries(forecast, resultVersion);
  state.hydrodynamicTimeline = {
    ...state.hydrodynamicTimeline,
    mode: "time_slice",
    hours,
    validTimes: steps.map((step) => step.validAt),
    rainfallSeries,
    index,
    layer,
    key,
    baseFilters: { ...(filters || {}) },
    resultVersion,
    forecastId: forecast.forecast_id || filters?.forecast_id || "latest",
    forecastVersion: forecast.forecast_version || null,
    forecastTime: forecast.forecast_time || null,
    validFrom: forecast.valid_from || null,
    validTo: forecast.valid_to || null,
  };
  const control = document.getElementById("hydroTimeline");
  const slider = document.getElementById("hydroTimeSlider");
  slider.min = "0";
  slider.max = String(hours.length - 1);
  slider.value = String(index);
  control.classList.remove("is-hidden");
  setTelemetryPanelOpen(true);
  if (filters?.view === "envelope") setHydrodynamicEnvelope();
  else setHydrodynamicTimelineIndex(index);
  renderForecastWindowSummary(state.lastMockObservation);
}

function hydrodynamicRainfallSeries(forecast, resultVersion) {
  const timeline = state.hydrodynamicTimeline;
  if (
    timeline.resultVersion === resultVersion
    && Array.isArray(timeline.rainfallSeries)
  ) {
    return timeline.rainfallSeries;
  }
  const archived = normalizeTimedSeries(forecast?.rainfall_series, ["rainfall_mm"]);
  if (archived.length) return archived;

  const observation = state.lastMockObservation;
  const observationAt = observation?.simulation_time || observation?.observed_at;
  const forecastStart = forecast?.valid_from || forecast?.forecast_time;
  if (
    !observationAt
    || !forecastStart
    || timedValue(observationAt) !== timedValue(forecastStart)
  ) {
    return [];
  }
  return normalizeTimedSeries([
    {
      valid_time: observationAt,
      rainfall_mm: Number(observation.rainfall_mm || 0),
    },
    ...state.rainfallForecast,
  ], ["rainfall_mm"]);
}

function setHydrodynamicEnvelope() {
  const timeline = state.hydrodynamicTimeline;
  if (!timeline.layer) return;
  stopHydrodynamicTimelinePlayback({ refreshImpact: false });
  timeline.mode = "envelope";
  const filters = hydrodynamicTileFilters(timeline.baseFilters, timeline.resultVersion);
  delete filters.time_h;
  timeline.layer.setResultFilters(filters);
  document.getElementById("hydroTimeSlider").disabled = true;
  document.getElementById("hydroTimeLabel").textContent = "最大淹没包络";
  setMapTimeContext({mode: "envelope"});
  renderSituationSummary();
  scheduleImpactAnalysisRefresh();
}

function captureHydrodynamicTimelineSelection(key) {
  const timeline = state.hydrodynamicTimeline;
  const hour = Number(timeline.hours?.[timeline.index]);
  if (!timeline.layer || timeline.key !== key || !Number.isFinite(hour)) return null;
  return {
    key,
    hour,
    resultVersion: String(timeline.resultVersion || ""),
  };
}

function nearestHydrodynamicHourIndex(hours, targetHour) {
  if (!hours.length || !Number.isFinite(targetHour)) return 0;
  return hours.reduce((bestIndex, hour, index) => (
    Math.abs(hour - targetHour) < Math.abs(hours[bestIndex] - targetHour) ? index : bestIndex
  ), 0);
}

function resolveHydrodynamicTimelineIndex(hours, resultVersion, key, previousSelection, filters = {}) {
  if (filters.time_h != null) {
    const requestedHour = Number(filters.time_h);
    if (!Number.isFinite(requestedHour) || !hours.length || requestedHour < hours[0] || requestedHour > hours.at(-1)) {
      throw new Error("指定预测时刻不在可用时间范围内");
    }
    return nearestHydrodynamicHourIndex(hours, requestedHour);
  }
  const preserveHour = Boolean(
    previousSelection
    && previousSelection.key === key
    && previousSelection.resultVersion
    && previousSelection.resultVersion === resultVersion
    && Number.isFinite(previousSelection.hour)
  );
  return preserveHour
    ? nearestHydrodynamicHourIndex(hours, previousSelection.hour)
    : 0;
}

function hydrodynamicTileFilters(filters, resultVersion) {
  return resultVersion ? { ...(filters || {}), result_version: resultVersion } : { ...(filters || {}) };
}

function hydrodynamicLayerBounds(meta, preferForecast = false) {
  const bbox = (preferForecast && meta?.forecast?.bbox) || meta?.bbox;
  const values = [bbox?.min_lat, bbox?.min_lon, bbox?.max_lat, bbox?.max_lon]
    .map((value) => Number(value));
  if (!bbox || values.some((value) => !Number.isFinite(value))) return undefined;
  return L.latLngBounds([values[0], values[1]], [values[2], values[3]]);
}

function hideHydrodynamicTimeline() {
  stopHydrodynamicTimelinePlayback({ refreshImpact: false });
  if (state.hydrodynamicTimeline.seekTimer) {
    window.clearTimeout(state.hydrodynamicTimeline.seekTimer);
    state.hydrodynamicTimeline.seekTimer = null;
  }
  state.hydrodynamicTimeline.hours = [];
  state.hydrodynamicTimeline.mode = "time_slice";
  state.hydrodynamicTimeline.validTimes = [];
  state.hydrodynamicTimeline.rainfallSeries = [];
  state.hydrodynamicTimeline.index = 0;
  state.hydrodynamicTimeline.layer = null;
  state.hydrodynamicTimeline.key = null;
  state.hydrodynamicTimeline.baseFilters = null;
  state.hydrodynamicTimeline.resultVersion = null;
  state.hydrodynamicTimeline.forecastId = null;
  state.hydrodynamicTimeline.forecastVersion = null;
  state.hydrodynamicTimeline.forecastTime = null;
  state.hydrodynamicTimeline.validFrom = null;
  state.hydrodynamicTimeline.validTo = null;
  document.getElementById("hydroTimeline")?.classList.add("is-hidden");
  renderSituationSummary();
  renderForecastWindowSummary(state.lastMockObservation);
  setMapTimeContext({
    mode: "current",
    currentAt: state.lastMockObservation?.simulation_time
      || state.lastMockObservation?.observed_at
      || null,
  });
  clearImpactAnalysisState();
}

function scheduleHydrodynamicTimelineIndex(index) {
  const timeline = state.hydrodynamicTimeline;
  if (timeline.seekTimer) window.clearTimeout(timeline.seekTimer);
  timeline.seekTimer = window.setTimeout(() => {
    timeline.seekTimer = null;
    setHydrodynamicTimelineIndex(index);
  }, 140);
}

function flushHydrodynamicTimelineIndex(index) {
  const timeline = state.hydrodynamicTimeline;
  if (timeline.seekTimer) window.clearTimeout(timeline.seekTimer);
  timeline.seekTimer = null;
  setHydrodynamicTimelineIndex(index);
}

function setHydrodynamicTimelineIndex(index) {
  const timeline = state.hydrodynamicTimeline;
  if (!timeline.layer || !timeline.hours.length) return;
  const nextIndex = Math.max(0, Math.min(timeline.hours.length - 1, Math.round(index)));
  timeline.mode = "time_slice";
  timeline.index = nextIndex;
  const slider = document.getElementById("hydroTimeSlider");
  const label = document.getElementById("hydroTimeLabel");
  if (slider) slider.value = String(nextIndex);
  if (slider) slider.disabled = false;
  const filters = { ...(timeline.baseFilters || {}) };
  if (timeline.resultVersion) filters.result_version = timeline.resultVersion;
  const hour = timeline.hours[nextIndex];
  const validAt = timeline.validTimes[nextIndex] || null;
  filters.time_h = formatHydrodynamicHour(hour);
  const timeLabel = formatHydrodynamicTimeLabel(hour, validAt);
  if (label) {
    label.textContent = timeLabel;
    label.title = validAt
      ? `${formatForecastActualTime(validAt, true)}（预测 +${formatHydrodynamicHour(hour)} 小时）`
      : `预测 +${formatHydrodynamicHour(hour)} 小时`;
  }
  slider?.setAttribute("aria-valuetext", timeLabel);
  timeline.layer.setResultFilters(filters);
  setMapTimeContext({
    mode: "time_slice",
    currentAt: state.lastMockObservation?.simulation_time
      || state.lastMockObservation?.observed_at
      || timeline.forecastTime,
    validAt,
    hour,
  });
  renderSituationSummary();
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
  if (timeline.seekTimer) window.clearTimeout(timeline.seekTimer);
  timeline.seekTimer = null;
  setHydrodynamicPlayIcon(true);
  renderSituationSummary();
  timeline.timer = window.setInterval(() => {
    if (timeline.layer?.isLoading?.()) return;
    const next = timeline.index >= timeline.hours.length - 1 ? 0 : timeline.index + 1;
    setHydrodynamicTimelineIndex(next);
  }, 850);
}

function stopHydrodynamicTimelinePlayback(options = {}) {
  const timeline = state.hydrodynamicTimeline;
  const wasPlaying = timeline.playing;
  if (timeline.timer) window.clearInterval(timeline.timer);
  timeline.timer = null;
  timeline.playing = false;
  setHydrodynamicPlayIcon(false);
  renderSituationSummary();
  if (wasPlaying && options.refreshImpact !== false) scheduleImpactAnalysisRefresh();
}

function setHydrodynamicPlayIcon(playing) {
  const btn = document.getElementById("hydroPlayBtn");
  if (!btn) return;
  btn.innerHTML = `<i data-lucide="${playing ? "pause" : "activity"}"></i>`;
  btn.title = playing ? "暂停逐帧预览" : "逐帧预览预测结果";
  btn.setAttribute("aria-label", btn.title);
  renderIcons();
}

function formatHydrodynamicHour(hour) {
  return Number(hour).toFixed(2).replace(/\.?0+$/, "");
}

function hydrodynamicTimelineSteps(forecast) {
  const detailed = (forecast?.time_steps || [])
    .map((step) => ({
      hour: Number(step?.time_h),
      validAt: step?.valid_at ? String(step.valid_at) : null,
    }))
    .filter((step) => Number.isFinite(step.hour));
  if (detailed.length) return detailed;
  return (forecast?.time_steps_h || [])
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value))
    .map((hour) => ({
      hour,
      validAt: offsetForecastTime(forecast?.valid_from || forecast?.forecast_time, hour),
    }));
}

function offsetForecastTime(validFrom, hour) {
  if (!validFrom || !Number.isFinite(Number(hour))) return null;
  const base = new Date(validFrom);
  if (Number.isNaN(base.getTime())) return null;
  return new Date(base.getTime() + Number(hour) * 3600000).toISOString();
}

function formatForecastActualTime(value, includeYear = false) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const options = {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  };
  if (includeYear) options.year = "numeric";
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("zh-CN", options)
      .formatToParts(date)
      .map((part) => [part.type, part.value]),
  );
  const datePart = includeYear
    ? `${parts.year}-${parts.month}-${parts.day}`
    : `${parts.month}-${parts.day}`;
  return `${datePart} ${parts.hour}:${parts.minute}`;
}

function formatHydrodynamicTimeLabel(hour, validAt) {
  const offset = `+${formatHydrodynamicHour(hour)}h`;
  const actual = formatForecastActualTime(validAt);
  return actual ? `预测 ${offset} · ${actual}` : `预测 ${offset}`;
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
  const bbox = state.hydrodynamicResultMeta?.forecast?.bbox
    || state.hydrodynamicResultMeta?.bbox
    || state.hydrodynamicGridMeta?.bbox;
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
  if (meta && !["HydrodynamicGridCell", "HydrodynamicResult"].includes(meta.objectType)) unindexLayer(meta.objectType, layer);
  if (layer) state.map.removeLayer(layer);
  state.layerGroups.delete(key);
  state.layerMeta.delete(key);
  if (meta) setObjectButtonActive(meta.buttonType || meta.objectType, hasLayerButtonType(meta.buttonType || meta.objectType));
  syncFilteredLayerButtons();
  updateMapContentContext();
}

function removeObjectTypeLayers(objectType) {
  Array.from(state.layerMeta.entries()).forEach(([key, meta]) => {
    if (meta?.objectType === objectType) removeLayer(key);
  });
}

function filtersWithObjectIds(objectType, filters = {}, objectIds = null) {
  if (objectIds == null) return { ...(filters || {}) };
  const ids = objectIds.map(String).filter(Boolean);
  const idField = ID_FIELDS[objectType];
  if (!idField) return { ...(filters || {}) };
  return {
    ...(filters || {}),
    [`${idField}__in`]: Array.from(new Set(ids)),
  };
}

function resetMap() {
  state.mapGeneration += 1;
  state.mapSelections.clear();
  state.hydrodynamicResultLoadToken += 1;
  setWatershedInundationAlert(false);
  clearFocus();
  clearHighlights();
  clearImpactAnalysisState();
  clearEventMarkers();
  for (const key of Array.from(state.layerGroups.keys())) {
    const meta = state.layerMeta.get(key);
    if (!["River", "Watershed", "Reservoir"].includes(meta?.objectType)) {
      removeLayer(key);
    }
  }
  updateMapContentContext();
  fitAll();
}

function clearHydrodynamicResults() {
  state.hydrodynamicResultLoadToken += 1;
  hideHydrodynamicTimeline();
  clearImpactAnalysisState();
  for (const [key, meta] of Array.from(state.layerMeta.entries())) {
    if (meta?.objectType === "HydrodynamicResult") {
      removeLayer(key);
    }
  }
  updateMapContentContext();
}

function clearImpactAnalysisState() {
  if (state.impactRefreshTimer) window.clearTimeout(state.impactRefreshTimer);
  state.impactRefreshTimer = null;
  state.impactRefreshController?.abort();
  state.impactRefreshController = null;
  state.impactRefreshSeq += 1;
  state.impactFocusSeq += 1;
  state.impactAnalysis = null;
  state.impactRequested = false;
  state.impactMarkerLayer?.clearLayers();
  state.impactMarkers.clear();
  clearImpactObjectSelection({ removeLayer: true });
  updateMapContentContext();
  const panel = document.getElementById("impactPanel");
  panel?.classList.remove("is-loading");
  panel?.setAttribute("aria-busy", "false");
  const count = document.getElementById("impactCount");
  const time = document.getElementById("impactTimeLabel");
  const status = document.getElementById("impactStatus");
  const list = document.getElementById("impactList");
  if (count) count.textContent = "0";
  if (time) {
    time.textContent = "等待预测结果";
    time.title = "当前影响分析范围";
  }
  if (status) {
    status.textContent = "等待结果";
    status.title = "等待水动力结果";
  }
  state.impactListView.positions.clear();
  state.impactListView.renderedKey = null;
  updateImpactListControls({ impacts: [], nearby: [] });
  document.getElementById("impactNotice").hidden = true;
  document.getElementById("impactListSummary").textContent = "道路按已收录路段统计";
  if (list) list.innerHTML = '<p class="impact-empty">加载预测结果后查看影响分析</p>';
  renderImpactReadiness();
}

function clearEventMarkers() {
  state.eventMarkers.forEach((marker) => state.map.removeLayer(marker));
  state.eventMarkers.clear();
  updateMapContentContext();
}

function startAutonomyStream() {
  if (state.autonomyStream) state.autonomyStream.close();
  const es = new EventSource("/api/autonomy/stream?interval=5");
  state.autonomyStream = es;

  es.addEventListener("runtime_status", (event) => {
    const data = parseEvent(event);
    acceptWorkspace(data.workspace_id);
    if (["等待水文事件", "等待边界流量事件", "等待启动边界流量回放"].includes(data.label)) {
      updateTelemetryRuntimeStatus(data);
      return;
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
    if (data.forecast_context && !data.forecast_context.available) clearHydrodynamicResults();
    renderMockObservation(data.event || {});
  });

  es.addEventListener("agent_trace", (event) => {
    const data = parseEvent(event);
    acceptWorkspace(data.workspace_id);
    if (
      state.playbackPaused
      && data.tag === "ERR"
      && String(data.label || "").includes("洪水预测")
    ) {
      state.playbackStepPending = false;
      void refreshPlaybackStatus();
    }
    if (shouldHideAutonomyTrace(data)) return;
    if (data.output_id && state.autonomyTraceIds.has(data.output_id)) return;
    if (data.output_id) state.autonomyTraceIds.add(data.output_id);
    const trace = addTrace(data.tag || "AGENT", data.label || "智能体事件处理", data.detail || "");
    if (!data.replayed) notifyAgentTrace(data, trace);
  });

  es.addEventListener("map_actions", async (event) => {
    const data = parseEvent(event);
    acceptWorkspace(data.workspace_id);
    await enqueueMapActions(data);
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
    acceptWorkspace(data.workspace_id);
    setPlaybackSpeedControl(data.speed_multiplier || 1);
    updateTelemetryRuntimeStatus(data);
  } catch (error) {
    console.warn("boundary flow playback status failed", error);
    updatePlaybackControls({ playback_phase: "ready" });
  }
}

function bindPlaybackSourceControls() {
  const control = document.getElementById("playbackSourceControl");
  const toggleBtn = document.getElementById("playbackToggleBtn");
  const pickerBtn = document.getElementById("playbackSourcePickerBtn");
  const menu = document.getElementById("playbackSourceMenu");
  const list = document.getElementById("playbackSourceList");
  const uploadBtn = document.getElementById("playbackSourceUploadBtn");
  const fileInput = document.getElementById("playbackSourceFileInput");
  let pressTimer = null;
  let pressOrigin = null;

  const cancelLongPress = () => {
    if (pressTimer) window.clearTimeout(pressTimer);
    pressTimer = null;
    pressOrigin = null;
  };

  toggleBtn.addEventListener("pointerdown", (event) => {
    if (
      toggleBtn.disabled
      || !isPlaybackSourceSelectable()
      || (event.button !== undefined && event.button !== 0)
    ) return;
    cancelLongPress();
    pressOrigin = { x: event.clientX, y: event.clientY };
    pressTimer = window.setTimeout(() => {
      state.playbackLongPressTriggered = true;
      setPlaybackSourceMenuOpen(true);
      pressTimer = null;
    }, 600);
  });
  toggleBtn.addEventListener("pointermove", (event) => {
    if (!pressOrigin) return;
    if (Math.hypot(event.clientX - pressOrigin.x, event.clientY - pressOrigin.y) > 10) {
      cancelLongPress();
    }
  });
  ["pointerup", "pointercancel", "pointerleave"].forEach((eventName) => {
    toggleBtn.addEventListener(eventName, () => {
      cancelLongPress();
      if (state.playbackLongPressTriggered) {
        window.setTimeout(() => {
          state.playbackLongPressTriggered = false;
        }, 500);
      }
    });
  });
  toggleBtn.addEventListener("contextmenu", (event) => event.preventDefault());
  toggleBtn.addEventListener("click", (event) => {
    if (state.playbackLongPressTriggered) {
      event.preventDefault();
      state.playbackLongPressTriggered = false;
      return;
    }
    toggleBoundaryFlowPlayback();
  });

  pickerBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    setPlaybackSourceMenuOpen(!state.playbackSourceMenuOpen);
  });
  list.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-playback-source-id]");
    if (!button || button.disabled) return;
    await choosePlaybackSource(button.dataset.playbackSourceId);
  });
  uploadBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    fileInput.value = "";
    if (file) await uploadPlaybackSource(file);
  });
  document.addEventListener("pointerdown", (event) => {
    if (state.playbackSourceMenuOpen && !control.contains(event.target)) {
      setPlaybackSourceMenuOpen(false);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.playbackSourceMenuOpen) {
      setPlaybackSourceMenuOpen(false);
      pickerBtn.focus();
    }
  });
  menu.addEventListener("click", (event) => event.stopPropagation());
}

async function setPlaybackSourceMenuOpen(open) {
  const menu = document.getElementById("playbackSourceMenu");
  const pickerBtn = document.getElementById("playbackSourcePickerBtn");
  state.playbackSourceMenuOpen = Boolean(open) && isPlaybackSourceSelectable();
  menu.hidden = !state.playbackSourceMenuOpen;
  pickerBtn.classList.toggle("is-active", state.playbackSourceMenuOpen);
  pickerBtn.setAttribute("aria-expanded", String(state.playbackSourceMenuOpen));
  if (!state.playbackSourceMenuOpen) return;
  setPlaybackSourceStatus("正在读取数据列表…");
  await refreshPlaybackSources();
}

async function refreshPlaybackSources() {
  try {
    const response = await fetch("/api/autonomy/sources");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "读取演进数据失败");
    state.playbackSources = Array.isArray(data.sources) ? data.sources : [];
    const selected = state.playbackSources.find((source) => source.selected);
    if (!state.playbackSource && selected) updatePlaybackSourceDisplay(selected);
    renderPlaybackSources();
    setPlaybackSourceStatus("");
  } catch (error) {
    setPlaybackSourceStatus(error.message || String(error), true);
  }
}

function renderPlaybackSources() {
  const list = document.getElementById("playbackSourceList");
  list.innerHTML = state.playbackSources.map((source) => {
    const selected = source.source_id === state.playbackSource?.source_id;
    const kind = source.kind === "builtin" ? "内置" : "已上传";
    const period = `${source.start_time || "--"} 至 ${source.end_time || "--"}`;
    return `
      <button class="playback-source-option${selected ? " is-selected" : ""}" type="button" role="menuitem" data-playback-source-id="${escapeHtml(source.source_id)}">
        ${source.kind === "builtin"
          ? '<i data-lucide="database"></i>'
          : '<i data-lucide="file-spreadsheet"></i>'}
        <span>
          <strong>${escapeHtml(source.name || source.original_filename || "未命名数据")}</strong>
          <small>${escapeHtml(kind)} · ${Number(source.row_count || 0)} 条 · ${escapeHtml(period)}</small>
        </span>
        <i class="playback-source-check" data-lucide="check" aria-hidden="true"></i>
      </button>
    `;
  }).join("");
  renderIcons();
}

function setPlaybackSourceStatus(message, isError = false) {
  const status = document.getElementById("playbackSourceStatus");
  status.hidden = !message;
  status.textContent = message || "";
  status.classList.toggle("is-error", Boolean(isError));
}

async function choosePlaybackSource(sourceId) {
  const source = state.playbackSources.find((item) => item.source_id === sourceId);
  if (!source) return;
  setPlaybackSourceMenuBusy(true);
  setPlaybackSourceStatus(`正在使用“${source.name}”开始演进…`);
  const started = await toggleBoundaryFlowPlayback(sourceId);
  setPlaybackSourceMenuBusy(false);
  if (started) {
    setPlaybackSourceMenuOpen(false);
    await refreshPlaybackSources();
  }
}

async function uploadPlaybackSource(file) {
  setPlaybackSourceMenuBusy(true);
  setPlaybackSourceStatus(`正在校验并上传“${file.name}”…`);
  try {
    const response = await fetch(`/api/autonomy/sources?filename=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers: { "Content-Type": file.type || "text/csv" },
      body: file,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "上传演进数据失败");
    await refreshPlaybackSources();
    const sourceId = data.source?.source_id;
    if (!sourceId) throw new Error("上传结果缺少数据标识");
    await choosePlaybackSource(sourceId);
  } catch (error) {
    setPlaybackSourceStatus(error.message || String(error), true);
    addTrace("ERR", "演进数据上传失败", error.message || String(error));
  } finally {
    setPlaybackSourceMenuBusy(false);
  }
}

function setPlaybackSourceMenuBusy(busy) {
  document.getElementById("playbackSourceUploadBtn").disabled = Boolean(busy);
  document.querySelectorAll("[data-playback-source-id]").forEach((button) => {
    button.disabled = Boolean(busy);
  });
}

async function restartBoundaryFlowPlayback() {
  const toggleBtn = document.getElementById("playbackToggleBtn");
  const restartBtn = document.getElementById("playbackRestartBtn");
  toggleBtn.disabled = true;
  restartBtn.disabled = true;
  state.playbackStepPending = false;
  try {
    const res = await fetch("/api/autonomy/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speed_multiplier: state.playbackSpeed }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    acceptWorkspace(data.workspace_id);
    updateTelemetryRuntimeStatus(data);
    setLayerPanelOpen(false);
    revealSituationPanel("telemetryPanel", "auto");
    setTelemetryPanelOpen(true);
    addTrace("AUTO", "演进已重置", "已创建新的演进工作空间，并回到第一个边界流量预测时刻；点击开始演进后继续回放。");
  } catch (error) {
    addTrace("ERR", "重新开始演进失败", error.message || String(error));
  } finally {
    updatePlaybackControls(state.runtimeStatus);
    restartBtn.disabled = false;
  }
}

async function stepBoundaryFlowPlayback() {
  if (!state.playbackPaused || state.playbackStepPending) return;
  const btn = document.getElementById("playbackStepBtn");
  state.playbackStepPending = true;
  updatePlaybackControls(state.runtimeStatus);
  try {
    const response = await fetch("/api/autonomy/step", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "单步推进失败");
    acceptWorkspace(data.workspace_id);
    state.playbackStepPending = false;
    updateTelemetryRuntimeStatus(data);
  } catch (error) {
    state.playbackStepPending = false;
    addTrace("ERR", "演进单步推进失败", error.message || String(error));
    void refreshPlaybackStatus();
  } finally {
    updatePlaybackControls(state.runtimeStatus);
    btn.blur();
  }
}

async function toggleBoundaryFlowPlayback(requestedSourceId = null) {
  if (state.playbackProcessing) return false;
  const wasRunning = state.playbackRunning;
  const wasPaused = state.playbackPaused;
  const wasPhase = state.playbackPhase;
  const action = requestedSourceId
    ? "start"
    : (wasRunning ? "pause" : (wasPaused ? "resume" : "start"));
  const btn = document.getElementById("playbackToggleBtn");
  btn.disabled = true;
  try {
    if (action === "start") {
      resetMap();
      clearMockTelemetry();
      setLayerPanelOpen(false);
      revealSituationPanel("telemetryPanel", "auto");
      setTelemetryPanelOpen(true);
    } else if (action === "resume") {
      state.playbackStepPending = false;
    }
    const res = await fetch(`/api/autonomy/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        speed_multiplier: state.playbackSpeed,
        ...(requestedSourceId ? { source_id: requestedSourceId } : {}),
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    acceptWorkspace(data.workspace_id);
    updatePlaybackSourceDisplay(data.playback_source);
    updateTelemetryRuntimeStatus(data);
    const labels = {
      start: ["边界流量过程回放已启动", "后台开始按时间顺序回放四边界流量。"],
      resume: ["边界流量过程回放已继续", "后台从暂停位置继续回放四边界流量。"],
      pause: ["边界流量过程回放已暂停", "后台暂停回放新的边界流量预测时刻；已产生的事件继续处理。"],
    };
    addTrace(
      "AUTO",
      labels[action][0],
      labels[action][1],
    );
    return true;
  } catch (error) {
    addTrace("ERR", "边界流量回放切换失败", error.message || String(error));
    updatePlaybackControls({
      running: wasRunning,
      paused: wasPaused,
      playback_phase: wasPhase,
    });
    return false;
  } finally {
    updatePlaybackControls(state.runtimeStatus);
  }
}

function acceptWorkspace(workspaceId) {
  const next = String(workspaceId || "");
  if (!next || next === state.workspaceId) return;
  state.workspaceId = next;
  clearRuntimeWorkspaceView();
  updatePlaybackRestartButton();
  refreshDirectiveHistory();
}

function clearRuntimeWorkspaceView() {
  if (state.activeRunId) fetch(`/api/agent/runs/${encodeURIComponent(state.activeRunId)}/cancel`, {method: "POST"}).catch(() => {});
  finishStream(true);
  resetMap();
  clearMockTelemetry();
  state.lastTrace = null;
  const trace = document.getElementById("agentTrace");
  const chat = document.getElementById("chatLog");
  if (trace) trace.innerHTML = "";
  const traceCount = document.getElementById("traceCount");
  if (traceCount) traceCount.textContent = "0";
  if (chat) chat.innerHTML = "";
  resetDirectiveWorkspaceView();
  dismissAgentNotification();
  state.autonomyTraceIds.clear();
  state.notificationChains.clear();
  state.mapActionReceipts.clear();
}

function resetDirectiveWorkspaceView() {
  state.directives = [];
  state.directiveDraft = null;
  document.getElementById("directiveCount").textContent = "0";
  document.getElementById("directiveHistoryList").innerHTML = "";
  clearDirectiveDraft();
}

async function refreshDirectiveHistory() {
  const expectedWorkspaceId = state.workspaceId;
  if (!expectedWorkspaceId) {
    state.directives = [];
    renderDirectiveHistory();
    return;
  }
  try {
    const response = await fetch("/api/directives");
    if (!response.ok) throw new Error(await response.text());
    const data = await response.json();
    if (state.workspaceId !== expectedWorkspaceId) return;
    if (data.workspace_id && data.workspace_id !== expectedWorkspaceId) return;
    state.directives = Array.isArray(data.directives) ? data.directives : [];
    renderDirectiveHistory();
  } catch (error) {
    console.warn("directive history failed", error);
  }
}

function renderDirectiveHistory() {
  const list = document.getElementById("directiveHistoryList");
  document.getElementById("directiveCount").textContent = String(state.directives.length);
  if (!state.directives.length) {
    list.innerHTML = '<div class="directive-history-empty">当前演进尚未发出应急指令。</div>';
    return;
  }
  const priorityLabels = { normal: "普通", urgent: "紧急", critical: "特急" };
  list.innerHTML = state.directives.map((directive) => `
    <button class="directive-history-item" type="button" data-view-directive="${escapeHtml(directive.directive_id || "")}" aria-label="查看已发指令：${escapeHtml(directive.title || "未命名指令")}">
      <span class="directive-history-copy">
        <span class="directive-history-title">${escapeHtml(directive.title || "未命名指令")}</span>
        <span class="directive-history-meta">
          ${escapeHtml(directive.directive_id || "")} · ${escapeHtml(priorityLabels[directive.priority] || directive.priority || "紧急")} · ${escapeHtml(formatDirectiveTime(directive.issued_at))}<br>
          接收对象：${escapeHtml(directive.recipients || "--")}
        </span>
      </span>
      <i data-lucide="file-text" aria-hidden="true"></i>
    </button>
  `).join("");
  renderIcons();
}

function openDirectiveDraft(draft) {
  state.directiveDraft = {
    basis: draft.basis || null,
    title: String(draft.title || ""),
    content: String(draft.content || ""),
    recipients: String(draft.recipients || ""),
    priority: ["normal", "urgent", "critical"].includes(draft.priority) ? draft.priority : "urgent",
    workspaceId: state.workspaceId,
    readOnly: false,
  };
  setDirectiveEditorReadOnly(false);
  document.getElementById("directiveTitle").value = state.directiveDraft.title;
  document.getElementById("directiveContent").value = state.directiveDraft.content;
  document.getElementById("directiveRecipients").value = state.directiveDraft.recipients;
  document.getElementById("directivePriority").value = state.directiveDraft.priority;
  setDirectiveToastError("");
  renderDirectiveContext(state.runtimeStatus);
  refreshDirectiveContext();
  showDirectiveDraftToast();
}

function openIssuedDirective(directiveId) {
  const directive = state.directives.find((item) => item.directive_id === directiveId);
  if (!directive) return;
  state.directiveDraft = {
    basis: directive.basis || null,
    directiveId: String(directive.directive_id || ""),
    title: String(directive.title || ""),
    content: String(directive.content || ""),
    recipients: String(directive.recipients || ""),
    priority: ["normal", "urgent", "critical"].includes(directive.priority)
      ? directive.priority
      : "urgent",
    workspaceId: String(directive.workspace_id || state.workspaceId || ""),
    simulationTime: directive.simulation_time || null,
    forecastVersion: directive.forecast_version || null,
    issuedAt: directive.issued_at || null,
    readOnly: true,
  };
  setDirectiveEditorReadOnly(true);
  document.getElementById("directiveTitle").value = state.directiveDraft.title;
  document.getElementById("directiveContent").value = state.directiveDraft.content;
  document.getElementById("directiveRecipients").value = state.directiveDraft.recipients;
  document.getElementById("directivePriority").value = state.directiveDraft.priority;
  setDirectiveToastError("");
  renderDirectiveContext();
  showDirectiveDraftToast();
}

function setDirectiveEditorReadOnly(readOnly) {
  const toast = document.getElementById("directiveDraftToast");
  toast.classList.toggle("is-readonly", readOnly);
  document.querySelector("#directiveDraftHeading span").textContent = readOnly
    ? "已发应急指令"
    : "应急指令初稿";
  ["directiveTitle", "directiveRecipients", "directiveContent"].forEach((id) => {
    document.getElementById(id).readOnly = readOnly;
  });
  document.getElementById("directivePriority").disabled = readOnly;
  document.getElementById("directiveCancelBtn").textContent = readOnly ? "关闭" : "取消";
  document.getElementById("directiveCopyBtn").hidden = !readOnly;
  document.getElementById("directiveIssueBtn").hidden = readOnly;
}

function showDirectiveDraftToast() {
  if (!state.directiveDraft) return;
  const toast = document.getElementById("directiveDraftToast");
  toast.hidden = false;
  toast.classList.remove("is-visible");
  window.requestAnimationFrame(() => {
    toast.classList.add("is-visible");
    const focusTarget = state.directiveDraft?.readOnly
      ? document.getElementById("directiveCancelBtn")
      : document.getElementById("directiveTitle");
    focusTarget.focus({ preventScroll: true });
    clampConclusionToastsToMap();
  });
}

function syncDirectiveDraft() {
  if (!state.directiveDraft || state.directiveDraft.readOnly) return;
  state.directiveDraft = {
    ...state.directiveDraft,
    title: document.getElementById("directiveTitle").value,
    content: document.getElementById("directiveContent").value,
    recipients: document.getElementById("directiveRecipients").value,
    priority: document.getElementById("directivePriority").value,
  };
}

function clearDirectiveDraft() {
  state.directiveDraft = null;
  document.getElementById("directiveTitle").value = "";
  document.getElementById("directiveContent").value = "";
  document.getElementById("directiveRecipients").value = "";
  document.getElementById("directivePriority").value = "urgent";
  setDirectiveEditorReadOnly(false);
  setDirectiveToastError("");
  const toast = document.getElementById("directiveDraftToast");
  toast.classList.remove("is-visible");
  toast.hidden = true;
  if (state.directiveToast) {
    state.directiveToast.dragX = 0;
    state.directiveToast.dragY = 0;
    toast.style.setProperty("--drag-x", "0px");
    toast.style.setProperty("--drag-y", "0px");
  }
}

async function refreshDirectiveContext() {
  if (state.directiveDraft?.readOnly) return;
  try {
    const response = await fetch("/api/autonomy/status");
    if (!response.ok) return;
    const status = await response.json();
    state.runtimeStatus = status;
    if (state.directiveDraft?.workspaceId === state.workspaceId) {
      renderDirectiveContext(status);
    }
  } catch (_error) {
    // The latest streamed runtime status remains a valid editor context.
  }
}

function renderDirectiveContext(status = {}) {
  const workspaceElement = document.getElementById("directiveContextWorkspace");
  if (state.directiveDraft?.readOnly) {
    workspaceElement.textContent = "已发出";
    workspaceElement.title = [
      state.directiveDraft.directiveId,
      formatDirectiveTime(state.directiveDraft.issuedAt),
    ].filter(Boolean).join(" · ");
    document.getElementById("directiveContextTime").textContent = formatMockTime(
      state.directiveDraft.simulationTime,
    );
    document.getElementById("directiveContextForecast").textContent = formatForecastVersion(
      state.directiveDraft.forecastVersion,
    );
    return;
  }
  const basis = state.directiveDraft?.basis?.snapshot;
  if (basis) {
    workspaceElement.textContent = "草稿依据";
    document.getElementById("directiveContextTime").textContent = formatMockTime(basis.simulation_time);
    document.getElementById("directiveContextForecast").textContent = formatForecastVersion(basis.forecast_version);
    return;
  }
  workspaceElement.textContent = "当前演进";
  workspaceElement.removeAttribute("title");
  document.getElementById("directiveContextTime").textContent = formatMockTime(status.observed_at);
  document.getElementById("directiveContextForecast").textContent = formatForecastVersion(status.forecast_version);
}

function validateDirectiveDraft() {
  const fields = [
    [document.getElementById("directiveTitle"), "请填写指令标题"],
    [document.getElementById("directiveRecipients"), "请填写接收对象"],
    [document.getElementById("directiveContent"), "请填写指令正文"],
  ];
  for (const [field, message] of fields) {
    field.setCustomValidity(field.value.trim() ? "" : message);
    if (!field.reportValidity()) return false;
  }
  syncDirectiveDraft();
  return true;
}

function setDirectiveToastError(message) {
  const output = document.getElementById("directiveToastError");
  output.textContent = message || "";
  output.hidden = !message;
}

async function issueDirective() {
  if (!state.directiveDraft || state.directiveDraft.readOnly) return;
  if (!validateDirectiveDraft()) return;
  setDirectiveToastError("");
  const button = document.getElementById("directiveIssueBtn");
  button.disabled = true;
  try {
    const response = await fetch("/api/directives", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workspace_id: state.directiveDraft.workspaceId,
        basis: state.directiveDraft.basis,
        title: state.directiveDraft.title,
        content: state.directiveDraft.content,
        recipients: state.directiveDraft.recipients,
        priority: state.directiveDraft.priority,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "发出失败");
    const directive = data.directive;
    state.directives = [directive, ...state.directives.filter((item) => item.directive_id !== directive.directive_id)];
    renderDirectiveHistory();
    clearDirectiveDraft();
    setTelemetryPanelOpen(true);
    window.requestAnimationFrame(() => revealSituationPanel("directivePanel", "auto"));
    window.setTimeout(() => revealSituationPanel("directivePanel", "auto"), 240);
  } catch (error) {
    setDirectiveToastError(error.message || String(error));
    addTrace("ERR", "应急指令发出失败", error.message || String(error));
  } finally {
    button.disabled = false;
  }
}

function copyDirectiveToDraft(directiveId) {
  const directive = state.directives.find((item) => item.directive_id === directiveId);
  if (!directive) return;
  openDirectiveDraft({
    basis: directive.basis,
    title: directive.title,
    content: directive.content,
    recipients: directive.recipients,
    priority: directive.priority,
  });
}

function formatDirectiveTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return formatMockTime(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatForecastVersion(value) {
  if (value == null || value === "") return "--";
  const text = String(value);
  if (/^v\d+$/i.test(text)) return text.toLowerCase();
  const number = Number(value);
  return Number.isInteger(number) && number >= 0
    ? `v${String(number).padStart(3, "0")}`
    : text;
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

async function updatePlaybackAutoPause(event) {
  const input = event.currentTarget;
  const previous = state.playbackAutoPauseEnabled;
  const enabled = Boolean(input.checked);
  setPlaybackAutoPauseControl(enabled);
  input.disabled = true;
  try {
    const response = await fetch("/api/autonomy/auto-pause", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_pause_enabled: enabled }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "自动暂停设置失败");
    updateTelemetryRuntimeStatus(data);
  } catch (error) {
    setPlaybackAutoPauseControl(previous);
    addTrace("ERR", "自动暂停设置失败", error.message || String(error));
  } finally {
    input.disabled = false;
  }
}

function setPlaybackAutoPauseControl(enabled) {
  state.playbackAutoPauseEnabled = Boolean(enabled);
  const input = document.getElementById("playbackAutoPauseSwitch");
  if (input) input.checked = state.playbackAutoPauseEnabled;
}

function setPlaybackSpeedControl(speed) {
  const value = [1, 2, 5, 10, 20].includes(Number(speed)) ? Number(speed) : 20;
  state.playbackSpeed = value;
  const select = document.getElementById("playbackSpeedSelect");
  if (select) select.value = String(value);
}

function updatePlaybackControls(data = {}) {
  const phase = playbackPhaseFromStatus(data);
  state.playbackPhase = phase;
  state.playbackRunning = phase === "running";
  state.playbackPaused = phase === "paused";
  state.playbackProcessing = phase === "processing";
  if (!state.playbackPaused) state.playbackStepPending = false;
  const btn = document.getElementById("playbackToggleBtn");
  if (!btn) return;
  const forecastPending = state.playbackPaused && (
    state.playbackStepPending || state.runtimeStatus?.policy_state === "PENDING"
  );
  btn.classList.toggle("is-running", state.playbackRunning);
  btn.classList.toggle("is-paused", state.playbackPaused);
  btn.classList.toggle("is-processing", state.playbackProcessing);
  btn.setAttribute("aria-pressed", String(state.playbackRunning));
  btn.disabled = state.playbackProcessing || forecastPending;
  btn.title = state.playbackProcessing
    ? "正在处理当前时刻触发的预测和后续事件"
    : forecastPending
    ? "正在计算当前滚动预测，完成后可继续演进"
    : state.playbackRunning
      ? "暂停边界流量过程回放"
      : state.playbackPaused
        ? "从暂停位置继续边界流量过程回放"
        : ["finished", "stopped"].includes(phase)
          ? "开始一轮新的边界流量过程回放"
          : "启动边界流量过程回放";
  btn.setAttribute("aria-label", btn.title);
  updatePlaybackSourceAvailability();
  renderPlaybackToggleButton();
  updatePlaybackRestartButton();
  updatePlaybackStepButton();
  renderIcons();
}

function playbackPhaseFromStatus(data = {}) {
  const explicit = String(data.playback_phase || "");
  if (["ready", "running", "processing", "paused", "finished", "stopped"].includes(explicit)) {
    return explicit;
  }
  if (data.processing || data.status === "processing") return "processing";
  if (data.running) return "running";
  if (data.paused || data.status === "paused" || data.status === "stepped") return "paused";
  if (data.status === "finished") return "finished";
  if (data.status === "stopped") return "stopped";
  if (data.status === "reset") return "ready";
  return state.playbackPhase || "ready";
}

function isPlaybackSourceSelectable() {
  return ["ready", "finished", "stopped"].includes(state.playbackPhase);
}

function updatePlaybackSourceAvailability() {
  const selectable = isPlaybackSourceSelectable();
  const picker = document.getElementById("playbackSourcePickerBtn");
  if (picker) picker.disabled = !selectable;
  if (!selectable && state.playbackSourceMenuOpen) setPlaybackSourceMenuOpen(false);
}

function updatePlaybackSourceDisplay(source) {
  if (!source?.source_id) return;
  state.playbackSource = source;
  renderPlaybackToggleButton();
  if (state.playbackSourceMenuOpen) renderPlaybackSources();
}

function renderPlaybackToggleButton() {
  const btn = document.getElementById("playbackToggleBtn");
  if (!btn) return;
  const sourceName = state.playbackSource?.name || "内置演进数据";
  const icon = state.playbackProcessing
    ? "activity"
    : state.playbackRunning
      ? "pause"
      : (state.playbackPaused ? "step-forward" : "play");
  const label = state.playbackRunning
    ? "暂停演进"
    : state.playbackProcessing
      ? "事件处理中"
      : state.playbackPaused
        ? "继续演进"
        : ["finished", "stopped"].includes(state.playbackPhase)
          ? "开始新演进"
          : "开始演进";
  btn.innerHTML = `
    <i data-lucide="${icon}"></i>
    <span class="playback-toggle-copy">
      <span>${label}</span>
      <small id="playbackSourceLabel" title="${escapeHtml(sourceName)}">${escapeHtml(sourceName)}</small>
    </span>
  `;
  renderIcons();
}

function updatePlaybackRestartButton() {
  const btn = document.getElementById("playbackRestartBtn");
  if (btn) btn.hidden = !state.playbackPaused;
}

function updatePlaybackStepButton() {
  const btn = document.getElementById("playbackStepBtn");
  if (!btn) return;
  const policyPending = state.runtimeStatus?.policy_state === "PENDING";
  const stepUnavailable = state.runtimeStatus?.step_available === false;
  btn.hidden = !state.playbackPaused;
  btn.disabled = (
    !state.playbackPaused
    || state.playbackStepPending
    || policyPending
    || stepUnavailable
  );
  btn.title = state.playbackStepPending || policyPending
    ? "正在计算当前滚动预测"
    : stepUnavailable
      ? "没有可继续步进的预测时刻"
    : "向前步进一个预测时刻";
  btn.setAttribute("aria-label", btn.title);
}

function updateTelemetryRuntimeStatus(data) {
  if (data.forecast_context && !data.forecast_context.available && state.hydrodynamicTimeline.layer) clearHydrodynamicResults();
  if (typeof data.auto_pause_enabled === "boolean") {
    setPlaybackAutoPauseControl(data.auto_pause_enabled);
  }
  state.runtimeStatus = { ...state.runtimeStatus, ...data };
  updatePlaybackControls(state.runtimeStatus);
  updatePlaybackSourceDisplay(data.playback_source);
  if (Number(data.total_rows || 0) > 0) state.playbackTotalRows = Number(data.total_rows);
  renderSituationSummary();
  if (state.playbackRunning) {
    if (!state.lastMockObservation) setTelemetryState("等待", "normal");
    return;
  }
  if (state.playbackProcessing) {
    setTelemetryState("处理中", "processing");
  } else if (state.playbackPhase === "finished") {
    setTelemetryState("完成", "normal");
  } else if (state.playbackPaused) {
    setTelemetryState("已暂停", "stopped");
  } else if (state.playbackPhase === "stopped") {
    setTelemetryState("已停止", "stopped");
  } else if (state.playbackPhase === "ready") {
    setTelemetryState(state.lastMockObservation ? "已停止" : "待机", "stopped");
  }
}

function renderMockObservation(event) {
  const observation = event.payload?.observation;
  if (!observation) return;
  const simulationTime = observation.simulation_time || observation.observed_at;
  state.lastMockObservation = observation;
  state.rainfallForecast = normalizeTimedSeries(
    observation.rainfall_forecast,
    ["rainfall_mm"],
  );
  state.boundaryFlowForecast = normalizeBoundaryFlowForecast(
    observation.boundary_flow_forecast,
  );
  updateStationRainfall(
    observation.station_rainfall,
    simulationTime,
    observation.station_rainfall_forecast,
  );
  ensureStationRainfallLayer(observation.station_rainfall);
  updateReservoirTelemetry(observation, simulationTime);
  ensureReservoirStationLayer(observation);
  if (state.mapTimeContext.mode === "current" || !state.hydrodynamicTimeline.layer) {
    setMapTimeContext({ mode: "current", currentAt: simulationTime });
  } else {
    applyMapTimeContext();
  }
  document.getElementById("telemetryTime").textContent = formatMockTime(simulationTime);
  state.basinRainfallHistory = state.basinRainfallHistory.filter((row) => row.valid_time !== simulationTime);
  state.basinRainfallHistory.push({ ...observation, valid_time: simulationTime });
  state.basinRainfallHistory = state.basinRainfallHistory.slice(-48);
  recordBoundaryFlowObservation(observation);
  renderBoundaryFlowHistoryChart();
  renderForecastWindowSummary(observation);

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
  renderSituationSummary();
}

function clearMockTelemetry() {
  state.lastMockObservation = null;
  state.rainfallForecast = [];
  state.boundaryFlowForecast = null;
  state.stationRainfallLayerInitialized = false;
  state.stationRainfallHistory = new Map();
  state.stationRainfallForecast = new Map();
  updateStationRainfall([], null, []);
  state.reservoirStationLayerInitialized = false;
  state.reservoirTelemetryHistory = [];
  updateReservoirTelemetry({}, null);
  setMapTimeContext({ mode: "current", currentAt: null });
  clearBoundaryFlowHistory();
  document.getElementById("telemetryTime").textContent = "--";
  state.basinRainfallHistory = [];
  document.querySelectorAll("[data-mock-field], [data-mock-boundary]").forEach((element) => {
    element.textContent = "--";
  });
  document.getElementById("telemetryProgressBar").style.width = "0%";
  document.getElementById("telemetryProgressText").textContent = `0 / ${state.playbackTotalRows}`;
  setTelemetryState("等待", "normal");
  renderSituationSummary();
  renderForecastWindowSummary(null);
}

function clearBoundaryFlowHistory() {
  state.boundaryFlowHistoryTimes = [];
  BOUNDARY_FLOW_KEYS.forEach((key) => {
    state.boundaryFlowHistory[key] = [];
  });
  renderBoundaryFlowHistoryChart();
}

function initBoundaryFlowHistoryChart() {
  const svg = document.getElementById("boundaryFlowHistoryChart");
  if (!svg) return;
  const renderAtLayoutSize = () => {
    if (state.boundaryFlowChartFrame) return;
    state.boundaryFlowChartFrame = window.requestAnimationFrame(() => {
      state.boundaryFlowChartFrame = null;
      renderBoundaryFlowHistoryChart();
    });
  };
  if (window.ResizeObserver) {
    state.boundaryFlowChartObserver = new ResizeObserver(renderAtLayoutSize);
    state.boundaryFlowChartObserver.observe(svg);
  } else {
    window.addEventListener("resize", renderAtLayoutSize);
  }
  renderBoundaryFlowHistoryChart();
}

function recordBoundaryFlowObservation(observation) {
  state.boundaryFlowHistoryTimes.push(observation.simulation_time || observation.observed_at || "");
  BOUNDARY_FLOW_KEYS.forEach((key) => {
    const flow = Number(observation.boundaries?.[key]?.flow_m3s);
    state.boundaryFlowHistory[key].push(Number.isFinite(flow) ? flow : null);
  });
  if (state.boundaryFlowHistoryTimes.length > BOUNDARY_FLOW_HISTORY_LIMIT) {
    state.boundaryFlowHistoryTimes.splice(0, state.boundaryFlowHistoryTimes.length - BOUNDARY_FLOW_HISTORY_LIMIT);
    BOUNDARY_FLOW_KEYS.forEach((key) => {
      const history = state.boundaryFlowHistory[key];
      history.splice(0, history.length - BOUNDARY_FLOW_HISTORY_LIMIT);
    });
  }
}

function normalizeBoundaryFlowForecast(forecast) {
  if (!forecast || typeof forecast !== "object") return null;
  const series = (Array.isArray(forecast.series) ? forecast.series : [])
    .map((point) => {
      const boundaries = {};
      BOUNDARY_FLOW_KEYS.forEach((key) => {
        const flow = finiteTelemetryNumber(point?.boundaries?.[key]?.flow_m3s);
        boundaries[key] = { flow_m3s: flow };
      });
      return {
        ...point,
        valid_time: String(point?.valid_time || ""),
        total_flow_m3s: finiteTelemetryNumber(point?.total_flow_m3s),
        boundaries,
      };
    })
    .filter((point) => point.valid_time);
  return { ...forecast, series };
}

function renderBoundaryFlowHistoryChart() {
  const svg = document.getElementById("boundaryFlowHistoryChart");
  if (!svg) return;
  const width = Math.max(240, Math.round(svg.clientWidth || 320));
  const height = Math.max(78, Math.round(svg.clientHeight || 78));
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const observedCount = Math.min(24, state.boundaryFlowHistoryTimes.length);
  const observedStart = state.boundaryFlowHistoryTimes.length - observedCount;
  const observedTimes = state.boundaryFlowHistoryTimes.slice(observedStart);
  const forecast = state.boundaryFlowForecast?.series || [];
  const forecastTimes = forecast.map((point) => point.valid_time);
  const timelineTimes = [...observedTimes, ...forecastTimes];
  const values = BOUNDARY_FLOW_KEYS.flatMap((key) => [
    ...state.boundaryFlowHistory[key]
      .slice(observedStart)
      .filter((value) => Number.isFinite(value)),
    ...forecast
      .map((point) => point.boundaries?.[key]?.flow_m3s)
      .filter((value) => Number.isFinite(value)),
  ]);
  const rangeLabel = document.getElementById("flowHistoryRange");
  if (!values.length) {
    svg.innerHTML = `<text class="flow-history-empty" x="${width / 2}" y="${height / 2 + 4}" text-anchor="middle">等待边界流量</text>`;
    if (rangeLabel) rangeLabel.textContent = "--";
    return;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const paddedRange = expandFlowRange(min, max);
  const plot = { left: 36, top: 8, right: 8, bottom: 18 };
  const pointFor = (value, index) => {
    const count = Math.max(1, timelineTimes.length - 1);
    const ratioX = timelineTimes.length === 1 ? 1 : index / count;
    const ratioY = (value - paddedRange.min) / (paddedRange.max - paddedRange.min || 1);
    return {
      x: plot.left + ratioX * (width - plot.left - plot.right),
      y: plot.top + (1 - ratioY) * (height - plot.top - plot.bottom),
    };
  };
  const grid = [0, 0.5, 1].map((ratio) => {
    const y = plot.top + ratio * (height - plot.top - plot.bottom);
    return `<line class="flow-history-grid" x1="${plot.left}" y1="${y.toFixed(2)}" x2="${width - plot.right}" y2="${y.toFixed(2)}"></line>`;
  }).join("");
  const verticalGrid = [0, 0.5, 1].map((ratio) => {
    const x = plot.left + ratio * (width - plot.left - plot.right);
    return `<line class="flow-history-grid is-vertical" x1="${x.toFixed(2)}" y1="${plot.top}" x2="${x.toFixed(2)}" y2="${height - plot.bottom}"></line>`;
  }).join("");
  const lines = BOUNDARY_FLOW_KEYS.map((key) => {
    const observedValues = state.boundaryFlowHistory[key].slice(observedStart);
    const observedPoints = observedValues
      .map((value, index) => Number.isFinite(value) ? pointFor(value, index) : null)
      .filter(Boolean);
    if (!observedPoints.length) return "";
    const forecastValues = forecast.map((point) => point.boundaries?.[key]?.flow_m3s);
    const forecastPoints = [observedValues.at(-1), ...forecastValues]
      .map((value, index) => Number.isFinite(value)
        ? pointFor(value, observedCount - 1 + index)
        : null)
      .filter(Boolean);
    const observedPath = observedPoints.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ");
    const forecastPath = forecastPoints.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ");
    const current = observedPoints.at(-1);
    const color = BOUNDARY_FLOW_COLORS[key];
    return `
      <path class="flow-history-line is-observed" style="--flow-color:${color}" d="${observedPath}">
        <title>${BOUNDARY_FLOW_LABELS[key]} · 已播放</title>
      </path>
      ${forecastPath ? `<path class="flow-history-line is-forecast" style="--flow-color:${color}" d="${forecastPath}"><title>${BOUNDARY_FLOW_LABELS[key]} · 未来预测</title></path>` : ""}
      <circle class="flow-history-dot" style="--flow-color:${color}" cx="${current.x.toFixed(2)}" cy="${current.y.toFixed(2)}" r="3"></circle>
    `;
  }).join("");
  const firstTime = timelineTimes[0];
  const lastTime = timelineTimes.at(-1);
  const currentIndex = Math.max(0, observedCount - 1);
  const currentX = pointFor(paddedRange.min, currentIndex).x;
  const thresholdAt = state.boundaryFlowForecast?.first_threshold_exceeded_at;
  const thresholdIndex = thresholdAt
    ? timelineTimes.findIndex((time) => timedValue(time) === timedValue(thresholdAt))
    : -1;
  const thresholdX = thresholdIndex >= 0
    ? pointFor(paddedRange.min, thresholdIndex).x
    : null;
  if (rangeLabel) rangeLabel.textContent = `${formatMockClock(firstTime)} - ${formatMockClock(lastTime)}`;
  svg.innerHTML = `
    <rect class="flow-history-plot" x="${plot.left}" y="${plot.top}" width="${width - plot.left - plot.right}" height="${height - plot.top - plot.bottom}"></rect>
    ${grid}
    ${verticalGrid}
    <line class="flow-history-now" x1="${currentX.toFixed(2)}" y1="${plot.top}" x2="${currentX.toFixed(2)}" y2="${height - plot.bottom}"></line>
    <text class="flow-history-now-label" x="${Math.min(width - plot.right - 2, currentX + 3).toFixed(2)}" y="${plot.top + 9}">当前</text>
    ${thresholdX === null ? "" : `<line class="flow-history-threshold" x1="${thresholdX.toFixed(2)}" y1="${plot.top}" x2="${thresholdX.toFixed(2)}" y2="${height - plot.bottom}"></line><text class="flow-history-threshold-label" x="${Math.min(width - plot.right - 2, thresholdX + 3).toFixed(2)}" y="${height - plot.bottom - 3}">&gt;230</text>`}
    <text class="flow-history-axis" x="4" y="${plot.top + 4}">${formatCompactFlow(paddedRange.max)}</text>
    <text class="flow-history-axis" x="4" y="${height - plot.bottom}">${formatCompactFlow(paddedRange.min)}</text>
    ${lines}
    <text class="flow-history-time" x="${plot.left}" y="${height - 5}">${formatMockClock(firstTime)}</text>
    <text class="flow-history-time" x="${width - plot.right}" y="${height - 5}" text-anchor="end">${formatMockClock(lastTime)}</text>
  `;
}

function renderForecastWindowSummary(observation) {
  renderBasinWorkbench(observation);
  const flow = state.boundaryFlowForecast;
  const window = document.getElementById("telemetryForecastWindow");
  const cnnVersion = state.hydrodynamicTimeline.forecastVersion;
  if (window) {
    const timeRange = flow?.window_start && flow?.window_end
      ? `${formatRainfallChartTime(flow.window_start)} → ${formatRainfallChartTime(flow.window_end)}`
      : "等待预测窗口";
    window.textContent = cnnVersion
      ? `${timeRange} · CNN ${formatForecastVersion(cnnVersion)}`
      : timeRange;
  }
}

function expandFlowRange(min, max) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { min: 0, max: 1 };
  if (min === max) {
    const padding = Math.max(1, max * 0.08);
    return { min: Math.max(0, min - padding), max: max + padding };
  }
  const padding = (max - min) * 0.12;
  return { min: Math.max(0, min - padding), max: max + padding };
}

function formatCompactFlow(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  if (Math.abs(number) >= 100) return number.toFixed(0);
  if (Math.abs(number) >= 10) return number.toFixed(1);
  return number.toFixed(2);
}

function renderSituationSummary() {
  const evolution = document.getElementById("situationEvolutionSummary");
  const forecast = document.getElementById("situationForecastSummary");
  if (!evolution || !forecast) return;

  const observation = state.lastMockObservation;
  const simulationTime = observation?.simulation_time || observation?.observed_at;
  const evolutionTime = formatForecastActualTime(simulationTime);
  const evolutionStatus = evolutionPlaybackStatusLabel();
  evolution.textContent = evolutionTime
    ? `${evolutionTime} · ${evolutionStatus}`
    : evolutionStatus;
  evolution.title = `当前演进时刻：${evolutionTime || "等待数据"}；状态：${evolutionStatus}`;

  const timeline = state.hydrodynamicTimeline;
  const hour = Number(timeline.hours?.[timeline.index]);
  const validAt = timeline.validTimes?.[timeline.index] || null;
  if (!timeline.layer) {
    forecast.textContent = "未加载";
    forecast.title = "尚未加载预测时间轴";
    return;
  }
  if (!Number.isFinite(hour)) {
    forecast.textContent = "已加载";
    forecast.title = "预测结果已加载，暂无可用预测时刻";
    return;
  }
  const offset = `+${formatHydrodynamicHour(hour)}h`;
  const actual = formatForecastActualTime(validAt);
  const preview = timeline.playing ? " · 预览中" : "";
  forecast.textContent = `${offset}${actual ? ` · ${actual}` : ""}${preview}`;
  forecast.title = `预测查看时刻：${formatHydrodynamicTimeLabel(hour, validAt)}${
    timeline.playing ? "；正在逐帧预览" : ""
  }`;
}

function evolutionPlaybackStatusLabel() {
  if (state.playbackProcessing) return "处理中";
  if (state.playbackRunning) return "运行中";
  if (state.playbackPaused) return "已暂停";
  if (state.playbackPhase === "finished") return "已完成";
  if (state.playbackPhase === "stopped") return "已停止";
  return state.lastMockObservation ? "待继续" : "等待数据";
}

function formatMockNumber(value, digits) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "--";
}

function formatMockTime(value) {
  if (!value) return "--";
  return String(value).replace("T", " ").replace(/:00\+08:00$/, "");
}

function formatMockClock(value) {
  if (!value) return "--";
  const match = String(value).match(/T(\d{2}:\d{2})|(\d{2}:\d{2})/);
  return match ? (match[1] || match[2]) : formatMockTime(value);
}

function setTelemetryState(label, stateName) {
  const element = document.getElementById("telemetryState");
  element.textContent = label;
  element.className = `telemetry-state is-${stateName}`;
}

function renderDomainEvent(data) {
  if (!data || !data.event_type) return;
  if (data.event_type === "InundationGenerated") {
    setTelemetryPanelOpen(true);
  }
  if (data.event_type === "ImpactAnalyzed") {
    registerImpactAnalysisResult(data.payload || null);
  }
  if (data.event_type === "DirectiveIssued") {
    refreshDirectiveHistory();
  }
  const tag = data.event_type === "FloodForecastRequired" ? "ALERT" : "EVENT";
  const label = data.event_type === "FloodForecastRequired" ? "洪水预测请求进入智能体" : (data.title || data.event_type);
  addTrace(tag, label, eventDetail(data));
  setCyclePhase(eventPhase(data.event_type));
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
    const labels = { Facility: "设施", Bridge: "桥梁", Road: "道路", EvacuationRoute: "路线", EvacuationUnit: "转移单元", EvacuationSite: "安置点", Station: "测站" };
    const parts = Object.keys(labels).map((key) => {
      const count = Number((summary[key] || {}).count || 0);
      return count ? `${labels[key]} ${count} 个` : "";
    }).filter(Boolean);
    return parts.length ? parts.join("，") : "未识别到受预测淹没影响的对象";
  }
  if (data.event_type === "DirectiveIssued") {
    return `${payload.directive_id || ""} · ${payload.title || ""}；接收对象：${payload.recipients || "--"}`;
  }
  return data.severity || "";
}

function eventPhase(eventType) {
  return {
    BoundaryFlowForecastAdvanced: "observe",
    BoundaryFlowObserved: "observe",
    FloodForecastRequired: "analyze",
    FloodEpisodeEnded: "monitor",
    InundationGenerated: "compute",
    ImpactAnalyzed: "analyze",
    DirectiveIssued: "decide",
    ExposureAnalyzed: "decide",
  }[eventType] || "analyze";
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
  if (objectType === "InundationForecastCell") {
    const depth = Number(feature.properties?.depth_m || feature.properties?.YMSS || 0);
    const color = depth > 1.2 ? "#7f1d1d" : depth > 0.6 ? "#dc2626" : "#fecaca";
    return { color, weight: 0.5, fillColor: color, fillOpacity: 0.34 };
  }
  if (objectType === "HydrodynamicGridCell") {
    const depth = Number(feature.properties?.depth_m || 0);
    return hydrodynamicCellStyle(depth);
  }
  if (objectType === "Watershed") return watershedStyle(state.inundationAlertActive);
  if (objectType === "River") return riverMainStyle();
  if (objectType === "Reservoir") return reservoirWaterStyle();
  if (objectType === "County") return { color: "#7b8794", weight: 1.2, fillOpacity: 0 };
  if (objectType === "Town") return { color: "#7a6a22", weight: 1, fillColor: "#facc15", fillOpacity: 0.08 };
  if (objectType === "Road" || objectType === "RoadRoute") return roadLineStyle(feature);
  if (objectType === "EvacuationRoute") return { color: "#d44a3a", weight: 3, opacity: 0.92 };
  if (objectType === "HydraulicStructure") return { color: "#0f766e", weight: 2, opacity: 0.9 };
  return { color: OBJECT_CONFIG[objectType]?.color || "#334155", weight: 2 };
}

function watershedStyle(alertActive = false) {
  return {
    color: alertActive ? "#bd3a32" : "#1f2937",
    weight: alertActive ? 3.2 : 1.3,
    opacity: alertActive ? 0.94 : 1,
    dashArray: alertActive ? "10 7" : null,
    fillColor: "#9bc4df",
    fillOpacity: 0.03,
    lineCap: "round",
    lineJoin: "round",
  };
}

async function setWatershedInundationAlert(active) {
  state.inundationAlertActive = active === true;
  if (state.inundationAlertActive && !hasObjectType("Watershed")) {
    await loadObject("Watershed", {}, { fit: false });
  }
  state.layerMeta.forEach((meta, key) => {
    if (meta?.objectType !== "Watershed") return;
    const layer = state.layerGroups.get(key);
    layer?.setStyle?.(watershedStyle(state.inundationAlertActive));
    layer?.eachLayer?.((item) => {
      item.getElement?.()?.classList.toggle(
        "is-inundation-alert",
        state.inundationAlertActive,
      );
    });
  });
  updateMapContentContext();
}

function reservoirWaterStyle() {
  return {
    color: "#0284c7",
    weight: 6,
    opacity: 0.95,
    fillColor: "#0ea5e9",
    fillOpacity: 0.16,
    lineCap: "round",
    lineJoin: "round",
  };
}

function reservoirHaloStyle() {
  return {
    color: "#7dd3fc",
    weight: 15,
    opacity: 0.28,
    fillOpacity: 0,
    lineCap: "round",
    lineJoin: "round",
  };
}

function reservoirHighlightStyle() {
  return {
    color: "#e0f2fe",
    weight: 2,
    opacity: 0.72,
    fillOpacity: 0,
    lineCap: "round",
    lineJoin: "round",
  };
}

function roadClassInfo(feature) {
  const props = feature?.properties || feature || {};
  const roadClass = String(props.road_class || "").trim().toLowerCase();
  const isLink = !props.road_route_id && roadClass === "motorway_link";
  const isExpressway = props.road_category
    ? props.road_category === "expressway"
    : roadClass === "motorway" || roadClass === "motorway_link";
  const style = ROAD_STYLES[isExpressway ? "expressway" : "other"];
  return {
    ...style,
    label: props.road_category === "mixed" ? "包含高速及非高速路段" : isLink ? "高速匝道" : isExpressway ? "高速公路" : "非高速道路",
    weight: isLink ? 3.2 : style.weight,
  };
}

function roadLineStyle(feature, outline = false) {
  const style = roadClassInfo(feature);
  return {
    color: outline ? style.outline : style.color,
    weight: style.weight + (outline ? 2.5 : 0),
    opacity: outline ? 0.9 : 1,
    lineCap: "round",
    lineJoin: "round",
  };
}

function createRoadLayer(geojson, mapSelectable, objectType = "Road") {
  const group = L.featureGroup();
  const namedLayers = [];
  L.geoJSON(geojson, {
    interactive: false,
    style: (feature) => roadLineStyle(feature, true),
  }).addTo(group);
  L.geoJSON(geojson, {
    interactive: mapSelectable,
    style: (feature) => featureStyle(objectType, feature),
    onEachFeature: (feature, layerItem) => {
      const name = roadMapName(feature.properties || {});
      if (name) {
        layerItem.bindTooltip(escapeHtml(name), {
          permanent: true,
          direction: "center",
          offset: [0, -13],
          opacity: 1,
          interactive: false,
          className: "road-name-label",
        });
        namedLayers.push({ layer: layerItem, name, objectType });
      }
      if (!mapSelectable) return;
      indexFeature(objectType, feature, layerItem);
      layerItem.bindPopup(popupHtml(objectType, feature), objectPopupOptions(objectType));
      layerItem.on("click", () => selectFeature(objectType, feature, layerItem));
    },
  }).addTo(group);
  group.on("add", () => {
    namedLayers.forEach((entry) => state.roadNameLayers.add(entry));
    refreshRoadNameLabels();
  });
  group.on("remove", () => {
    namedLayers.forEach((entry) => state.roadNameLayers.delete(entry));
    refreshRoadNameLabels();
  });
  return group;
}

function roadMapName(props) {
  const name = String(props.name || "").trim();
  if (!name || /^(未命名|无名|unnamed\b|unknown\b)/i.test(name)) return "";
  if (["generated_from_stable_id", "generated_stable_id", "generated_from_source_id"].includes(props.name_source)) return "";
  return name;
}

function roadLabelAnchor(layer, viewport) {
  const latlngs = layer.getLatLngs();
  const lines = latlngs[0]?.lat !== undefined ? [latlngs] : latlngs;
  let anchor = null;
  let longest = 0;
  let visibleLength = 0;
  for (const line of lines) {
    const points = line.map((latlng) => state.map.latLngToContainerPoint(latlng));
    for (let index = 1; index < points.length; index += 1) {
      const clipped = L.LineUtil.clipSegment(points[index - 1], points[index], viewport);
      if (!clipped) continue;
      const length = clipped[0].distanceTo(clipped[1]);
      visibleLength += length;
      if (length > longest) {
        longest = length;
        // Keep labels on an actual visible line, including disconnected routes.
        anchor = clipped[0].add(clipped[1]).divideBy(2);
      }
    }
  }
  return anchor ? { point: anchor, visibleLength } : null;
}

function refreshRoadNameLabels() {
  if (!state.map) return;
  const viewport = L.bounds([0, 0], state.map.getSize());
  const candidates = [];
  state.roadNameLayers.forEach((entry) => {
    const element = entry.layer.getTooltip()?.getElement();
    if (!element) return;
    element.style.visibility = "hidden";
    const anchor = roadLabelAnchor(entry.layer, viewport);
    if (anchor) candidates.push({ ...entry, ...anchor, element });
  });
  candidates.sort((a, b) => (
    Number(b.objectType === "RoadRoute") - Number(a.objectType === "RoadRoute")
    || b.visibleLength - a.visibleLength
  ));
  const placed = [];
  for (const item of candidates) {
    const halfWidth = item.element.offsetWidth / 2 + 6;
    const halfHeight = item.element.offsetHeight / 2 + 4;
    const center = item.point.subtract([0, 13]);
    const bounds = L.bounds(
      center.subtract([halfWidth, halfHeight]),
      center.add([halfWidth, halfHeight]),
    );
    if (!viewport.contains(bounds)) continue;
    if (placed.some((other) => bounds.intersects(other.bounds)
      || (item.name === other.name && center.distanceTo(other.center) < 260))) continue;
    item.layer.getTooltip().setLatLng(state.map.containerPointToLatLng(item.point));
    item.element.style.visibility = "visible";
    placed.push({ name: item.name, center, bounds });
  }
}

function createReservoirLayer(geojson, mapSelectable) {
  const group = L.featureGroup();
  const polygonFilter = (feature) => feature.geometry?.type !== "Point";
  L.geoJSON(geojson, {
    interactive: false,
    pane: "riverPane",
    filter: polygonFilter,
    style: reservoirHaloStyle,
  }).addTo(group);
  L.geoJSON(geojson, {
    interactive: mapSelectable,
    pane: "riverPane",
    style: reservoirWaterStyle,
    pointToLayer: (feature, latlng) => pointLayer("Reservoir", feature, latlng),
    onEachFeature: (feature, layerItem) => {
      if (!mapSelectable) return;
      indexFeature("Reservoir", feature, layerItem);
      layerItem.bindPopup(
        popupHtml("Reservoir", feature),
        objectPopupOptions("Reservoir"),
      );
      if (feature.properties?.name === "龙潭水库") {
        layerItem.bindTooltip("龙潭水库", {
          permanent: true,
          direction: "center",
          className: "reservoir-name-label",
        });
      }
      layerItem.on("click", () => selectFeature("Reservoir", feature, layerItem));
    },
  }).addTo(group);
  L.geoJSON(geojson, {
    interactive: false,
    pane: "riverPane",
    filter: polygonFilter,
    style: reservoirHighlightStyle,
  }).addTo(group);
  return group;
}

function createRiverLayer(geojson, mapSelectable) {
  const group = L.featureGroup();
  L.geoJSON(geojson, {
    interactive: false,
    pane: "riverPane",
    style: riverHaloStyle,
  }).addTo(group);
  L.geoJSON(geojson, {
    interactive: mapSelectable,
    pane: "riverPane",
    style: riverMainStyle,
    onEachFeature: (feature, layerItem) => {
      if (!mapSelectable) return;
      indexFeature("River", feature, layerItem);
      layerItem.bindPopup(
        popupHtml("River", feature),
        objectPopupOptions("River"),
      );
      layerItem.on("click", () => selectFeature("River", feature, layerItem));
    },
  }).addTo(group);
  L.geoJSON(geojson, {
    interactive: false,
    pane: "riverPane",
    style: riverHighlightStyle,
  }).addTo(group);
  addRiverDirectionMarkers(geojson, group);
  addRiverLabel(geojson, group);
  return group;
}

function riverHaloStyle() {
  return {
    color: "#7dd3fc",
    weight: 15,
    opacity: 0.28,
    lineCap: "round",
    lineJoin: "round",
  };
}

function riverMainStyle() {
  return {
    color: "#0284c7",
    weight: 6,
    opacity: 0.95,
    lineCap: "round",
    lineJoin: "round",
  };
}

function riverHighlightStyle() {
  return {
    color: "#e0f2fe",
    weight: 2,
    opacity: 0.72,
    lineCap: "round",
    lineJoin: "round",
  };
}

function addRiverDirectionMarkers(geojson, group) {
  const coords = longestLineCoordinates(geojson);
  if (coords.length < 2) return;
  const total = lineLengthMeters(coords);
  if (!Number.isFinite(total) || total <= 0) return;
  const count = Math.max(4, Math.min(12, Math.round(total / 4500)));
  for (let index = 0; index < count; index += 1) {
    const distance = total * ((index + 1) / (count + 1));
    const sample = pointAlongLine(coords, distance);
    if (!sample) continue;
    L.marker([sample.lat, sample.lng], {
      pane: "riverMarkerPane",
      interactive: false,
      keyboard: false,
      icon: L.divIcon({
        className: "river-direction-marker",
        html: `<span style="--river-angle:${sample.angleDeg.toFixed(1)}deg"></span>`,
        iconSize: [18, 18],
        iconAnchor: [9, 9],
      }),
    }).addTo(group);
  }
}

function addRiverLabel(geojson, group) {
  const coords = longestLineCoordinates(geojson);
  if (coords.length < 2) return;
  const total = lineLengthMeters(coords);
  const sample = pointAlongLine(coords, total * 0.52);
  if (!sample) return;
  L.marker([sample.lat, sample.lng], {
    pane: "riverMarkerPane",
    interactive: false,
    keyboard: false,
    icon: L.divIcon({
      className: "river-name-label",
      html: "<span>珊瑚河</span>",
      iconSize: [72, 24],
      iconAnchor: [36, 12],
    }),
  }).addTo(group);
}

function longestLineCoordinates(geojson) {
  const lines = [];
  const features = geojson?.type === "FeatureCollection" ? geojson.features : [geojson];
  features.forEach((feature) => {
    const geometry = feature?.type === "Feature" ? feature.geometry : feature;
    if (geometry?.type === "LineString") lines.push(geometry.coordinates || []);
    if (geometry?.type === "MultiLineString") lines.push(...(geometry.coordinates || []));
  });
  return lines
    .map((line) => line.filter((coord) => Array.isArray(coord) && coord.length >= 2))
    .sort((a, b) => lineLengthMeters(b) - lineLengthMeters(a))[0] || [];
}

function lineLengthMeters(coords) {
  let total = 0;
  for (let index = 1; index < coords.length; index += 1) {
    total += distanceMeters(coords[index - 1], coords[index]);
  }
  return total;
}

function pointAlongLine(coords, targetMeters) {
  let travelled = 0;
  for (let index = 1; index < coords.length; index += 1) {
    const start = coords[index - 1];
    const end = coords[index];
    const segment = distanceMeters(start, end);
    if (segment <= 0) continue;
    if (travelled + segment >= targetMeters) {
      const ratio = Math.max(0, Math.min(1, (targetMeters - travelled) / segment));
      const lng = start[0] + (end[0] - start[0]) * ratio;
      const lat = start[1] + (end[1] - start[1]) * ratio;
      const angleDeg = Math.atan2(-(end[1] - start[1]), end[0] - start[0]) * 180 / Math.PI;
      return { lng, lat, angleDeg };
    }
    travelled += segment;
  }
  const last = coords.at(-1);
  if (!last) return null;
  return { lng: last[0], lat: last[1], angleDeg: 0 };
}

function distanceMeters(start, end) {
  const toRad = Math.PI / 180;
  const lat1 = Number(start[1]) * toRad;
  const lat2 = Number(end[1]) * toRad;
  const deltaLat = (Number(end[1]) - Number(start[1])) * toRad;
  const deltaLng = (Number(end[0]) - Number(start[0])) * toRad;
  const a = Math.sin(deltaLat / 2) ** 2
    + Math.cos(lat1) * Math.cos(lat2) * Math.sin(deltaLng / 2) ** 2;
  return 6371000 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function pointStyle(objectType, feature) {
  let color = OBJECT_CONFIG[objectType]?.color || "#334155";
  if (objectType === "Facility") {
    const type = feature.properties?.facility_type;
    color = type === "school" ? "#d44a3a" : type === "hospital" ? "#b91c1c" : "#7c3aed";
  }
  if (objectType === "EvacuationSite") color = "#24895d";
  if (objectType === "EvacuationUnit") color = "#c97a12";
  if (objectType === "DangerArea") color = "#b91c1c";
  return {
    radius: pointRadius(objectType),
    color: "#ffffff",
    weight: pointStrokeWeight(objectType),
    fillColor: color,
    fillOpacity: objectType === "DangerArea" ? 0.78 : 0.88,
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
  const props = feature?.properties || feature || {};
  const stationRainfall = stationRainfallForProps(props);
  const reservoirTelemetry = reservoirTelemetryForProps(props);
  const rainfallLabel = stationRainfall
    ? ` · ${stationRainfall.display_mode === "forecast" ? "预测雨量" : "当前雨量"} ${formatStationRainfall(stationRainfall.rainfall_mm)} mm`
    : "";
  const reservoirLabel = reservoirTelemetry
    ? ` · ${reservoirTelemetry.display_mode === "forecast" ? "预测水位" : "当前水位"} ${formatReservoirLevel(reservoirTelemetry.reservoir_level_m)} m`
    : "";
  const accessibleLabel = props.name
    ? `${props.name} · ${info.label}${rainfallLabel}${reservoirLabel}`
    : `${info.label}${rainfallLabel}${reservoirLabel}`;
  const symbol = window.FloodMapSymbols?.render(info.icon) || "";
  return L.divIcon({
    className: `object-symbol-marker object-symbol-${info.key}`,
    html: `
      <span class="object-symbol-inner" title="${escapeHtml(accessibleLabel)}" role="img" aria-label="${escapeHtml(accessibleLabel)}">${symbol}</span>
      ${stationRainfallBadgeHtml(stationRainfall)}
      ${reservoirLevelBadgeHtml(reservoirTelemetry)}
    `,
    iconSize: [32, 38],
    iconAnchor: [16, 37],
    popupAnchor: [0, -36],
  });
}

function objectIconInfo(objectType, feature) {
  const props = feature?.properties || feature || {};
  if (objectType === "Facility") {
    const type = props.facility_type || "facility";
    return {
      school: { key: "school", icon: "school", label: "学校" },
      hospital: { key: "hospital", icon: "hospital", label: "医院" },
      government: { key: "government", icon: "landmark", label: "政府机构" },
    }[type] || { key: "facility", icon: "building-2", label: "重要设施" };
  }
  if (objectType === "Station") {
    const type = props.station_type || "station";
    return {
      flash_flood: { key: "station-flash-flood", icon: "cloud-lightning", label: "山洪测站" },
      meteorological: { key: "station-meteorological", icon: "cloud-sun", label: "气象测站" },
      hydrological: { key: "station-hydrological", icon: "gauge", label: "水文测站" },
      reservoir: { key: "station-reservoir", icon: "dam", label: "水库测站" },
    }[type] || { key: "station", icon: "radio-tower", label: "测站" };
  }
  return {
    River: { key: "river", icon: "route", label: "珊瑚河" },
    Watershed: { key: "watershed", icon: "map", label: "珊瑚河流域" },
    County: { key: "county", icon: "map", label: "县级边界" },
    Town: { key: "town", icon: "map", label: "乡镇边界" },
    Reservoir: { key: "reservoir", icon: "waves-horizontal", label: "水库" },
    Sluice: { key: "sluice", icon: "dam", label: "水闸" },
    Bridge: { key: "bridge", icon: "bridge", label: "桥梁" },
    Road: { key: "road", icon: "route", label: "路段" },
    RoadRoute: { key: "road", icon: "route", label: "编号道路" },
    EvacuationSite: { key: "place", icon: "house-heart", label: "安置地点" },
    EvacuationUnit: { key: "evacuation-unit", icon: "users", label: "转移单元" },
    EvacuationRoute: { key: "route", icon: "route", label: "转移路线" },
    DangerArea: { key: "danger-area", icon: "triangle-alert", label: "危险区" },
    HydrodynamicGridCell: { key: "hydrodynamic-grid", icon: "layers", label: "水动力网格单元" },
    InundationForecastCell: { key: "forecast-result", icon: "waves-horizontal", label: "预测淹没范围" },
    ForecastResult: { key: "forecast-result", icon: "waves-horizontal", label: "预测淹没范围" },
  }[objectType] || { key: "default", icon: "map-pin", label: OBJECT_CONFIG[objectType]?.label || objectType };
}

function pointRadius(objectType) {
  return {
    DangerArea: 2.2,
    EvacuationSite: 2.6,
    EvacuationUnit: 3.0,
    Bridge: 3.4,
    Sluice: 3.6,
    Reservoir: 3.8,
    Facility: 3.6,
    Station: 4.0,
  }[objectType] || 3.4;
}

function pointStrokeWeight(objectType) {
  return ["DangerArea", "EvacuationSite", "EvacuationUnit"].includes(objectType) ? 0.9 : 1.2;
}

function popupHtml(objectType, feature) {
  const props = feature.properties || {};
  const name = props.name || props[ID_FIELDS[objectType]] || OBJECT_CONFIG[objectType]?.label || objectType;
  const id = props[ID_FIELDS[objectType]] || "";
  if (objectType === "RoadRoute") {
    return `<div class="popup-title">${escapeHtml(props.ref)} · ${escapeHtml(name)}</div>
      <div class="popup-meta">${escapeHtml(roadClassInfo(props).label)} · 已收录 ${escapeHtml(String(props.segment_count))} 段</div>
      <div class="popup-meta">${escapeHtml(props.coverage_scope)}</div>`;
  }
  if (objectType === "Road") {
    const description = [roadClassInfo(feature).label, props.ref].filter(Boolean).join(" · ");
    return `
      <div class="popup-title">${escapeHtml(name)}</div>
      <div class="popup-meta">${escapeHtml(description)}</div>
      <div class="popup-meta">路段 ${escapeHtml(id)} · ${escapeHtml(formatRouteDistance(props.length_m))}</div>
    `;
  }
  if (objectType === "EvacuationRoute") {
    const steps = parseRouteInstructions(props.instructions);
    return `
      <div class="popup-title">${escapeHtml(name)}</div>
      <div class="popup-meta">路线 ${escapeHtml(id)} · ${formatRouteDistance(props.length_m)} · ${formatRouteDuration(props.duration_s)}</div>
      ${steps.length ? `<div class="popup-meta">导航步骤 ${steps.length} 步，点击路线查看详情</div>` : ""}
    `;
  }
  if (objectType === "Reservoir" && props.reservoir_id === "longtan") {
    return `<div class="popup-title">${escapeHtml(name)}</div>
      ${reservoirTelemetryPopupHtml(reservoirTelemetryForProps(props))}`;
  }
  if (objectType === "Station") {
    const stationType = objectIconInfo(objectType, feature).label;
    const rainfall = stationRainfallForProps(props);
    const reservoirTelemetry = reservoirTelemetryForProps(props);
    return `
      <div class="popup-title">${escapeHtml(name)}</div>
      <div class="popup-meta">${escapeHtml(stationType)} · 编码 ${escapeHtml(id)}</div>
      ${stationRainfallPopupHtml(rainfall)}
      ${reservoirTelemetryPopupHtml(reservoirTelemetry)}
    `;
  }
  return `
    <div class="popup-title">${escapeHtml(name)}</div>
    <div class="popup-meta">${escapeHtml(OBJECT_CONFIG[objectType]?.label || objectType)} ${escapeHtml(id)}</div>
  `;
}

function objectPopupOptions(objectType) {
  const options = {
    autoClose: false,
    closeOnClick: false,
    closeButton: true,
  };
  if (!["Station", "Reservoir"].includes(objectType)) return options;
  return {
    ...options,
    maxWidth: 400,
    autoPanPaddingTopLeft: L.point(18, 18),
    autoPanPaddingBottomRight: L.point(18, 18),
  };
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

function updateStationRainfall(readings, observedAt, forecasts = []) {
  const next = new Map();
  if (Array.isArray(readings)) {
    readings.forEach((reading) => {
      const stationId = String(reading?.station_id || "");
      const rainfall = Number(reading?.rainfall_mm);
      if (!stationId || !Number.isFinite(rainfall)) return;
      next.set(stationId, {
        ...reading,
        station_id: stationId,
        rainfall_mm: Math.max(0, rainfall),
      });
    });
  }
  state.stationRainfall = next;
  state.stationRainfallObservedAt = observedAt || null;
  recordStationRainfallHistory(Array.from(next.values()), observedAt);
  state.stationRainfallForecast = normalizeStationRainfallForecast(forecasts);
  refreshStationTelemetryMarkers();
}

function recordStationRainfallHistory(readings, observedAt) {
  if (!observedAt || !readings.length) return;
  const time = String(observedAt);
  const history = new Map(state.stationRainfallHistory);
  readings.forEach((reading) => {
    const stationId = String(reading.station_id || "");
    if (!stationId) return;
    const series = (history.get(stationId) || [])
      .filter((point) => point.observed_time !== time);
    series.push({
      observed_time: time,
      rainfall_mm: reading.rainfall_mm,
    });
    series.sort((left, right) => left.observed_time.localeCompare(right.observed_time));
    history.set(stationId, series.slice(-STATION_RAINFALL_HISTORY_LIMIT));
  });
  state.stationRainfallHistory = history;
}

function normalizeStationRainfallForecast(forecasts) {
  const next = new Map();
  if (!Array.isArray(forecasts)) return next;
  forecasts.forEach((forecast) => {
    const stationId = String(forecast?.station_id || "");
    if (!stationId) return;
    const series = (Array.isArray(forecast.series) ? forecast.series : [])
      .map((point) => ({
        valid_time: String(point?.valid_time || ""),
        rainfall_mm: Math.max(0, Number(point?.rainfall_mm)),
      }))
      .filter((point) => point.valid_time && Number.isFinite(point.rainfall_mm));
    next.set(stationId, { ...forecast, station_id: stationId, series });
  });
  return next;
}

function normalizeTimedSeries(series, numericFields = []) {
  if (!Array.isArray(series)) return [];
  return series
    .map((point) => {
      const normalized = {
        ...point,
        valid_time: String(point?.valid_time || ""),
      };
      numericFields.forEach((field) => {
        normalized[field] = finiteTelemetryNumber(point?.[field]);
      });
      return normalized;
    })
    .filter((point) => (
      point.valid_time
      && numericFields.every((field) => point[field] !== null)
    ))
    .sort((left, right) => timedValue(left.valid_time) - timedValue(right.valid_time));
}

function timedValue(value) {
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : NaN;
}

function interpolateTimedPoint(series, validAt, numericFields) {
  const target = timedValue(validAt);
  if (!Number.isFinite(target) || !Array.isArray(series) || !series.length) return null;
  const points = series
    .filter((point) => Number.isFinite(timedValue(point.valid_time)))
    .sort((left, right) => timedValue(left.valid_time) - timedValue(right.valid_time));
  if (!points.length) return null;
  const exact = points.find((point) => timedValue(point.valid_time) === target);
  if (exact) return { ...exact };
  const rightIndex = points.findIndex((point) => timedValue(point.valid_time) > target);
  if (rightIndex <= 0) return null;
  const left = points[rightIndex - 1];
  const right = points[rightIndex];
  const leftTime = timedValue(left.valid_time);
  const rightTime = timedValue(right.valid_time);
  const ratio = (target - leftTime) / Math.max(1, rightTime - leftTime);
  const result = {
    ...(ratio < 0.5 ? left : right),
    valid_time: String(validAt),
    interpolated: true,
  };
  numericFields.forEach((field) => {
    const leftValue = finiteTelemetryNumber(left[field]);
    const rightValue = finiteTelemetryNumber(right[field]);
    result[field] = leftValue === null || rightValue === null
      ? null
      : leftValue + (rightValue - leftValue) * ratio;
  });
  return result;
}

function mapRainfallForContext() {
  const observation = state.lastMockObservation;
  if (state.mapTimeContext.mode === "envelope") return 0;
  if (state.mapTimeContext.mode !== "time_slice") {
    return Number(observation?.rainfall_mm || 0);
  }
  return interpolateTimedPoint(
    state.hydrodynamicTimeline.rainfallSeries,
    state.mapTimeContext.validAt,
    ["rainfall_mm"],
  )?.rainfall_mm || 0;
}

function ensureStationRainfallLayer(readings) {
  if (!Array.isArray(readings) || !readings.length || state.stationRainfallLayerInitialized) return;
  state.stationRainfallLayerInitialized = true;
  void loadObject("Station", { station_type: "meteorological" }, {
    fit: false,
    label: "气象测站",
  }).catch((error) => {
    state.stationRainfallLayerInitialized = false;
    addTrace("ERR", "气象测站加载失败", error.message || String(error));
  });
}

function refreshStationTelemetryMarkers() {
  state.featureIndex.forEach((entry) => {
    if (entry.objectType === "Reservoir") {
      entry.layer.setPopupContent?.(popupHtml("Reservoir", entry.feature));
      return;
    }
    if (entry.objectType !== "Station") return;
    const props = entry.feature?.properties || {};
    if (!["meteorological", "reservoir"].includes(props.station_type)) return;
    const wasFocused = state.focusedLayer === entry.layer;
    const wasHighlighted = state.highlightedLayers.some(({ layer }) => layer === entry.layer);
    entry.layer.setIcon?.(objectDivIcon("Station", entry.feature));
    entry.layer.setPopupContent?.(popupHtml("Station", entry.feature));
    if (wasFocused) entry.layer.getElement()?.classList.add("is-focused");
    if (wasHighlighted) entry.layer.getElement()?.classList.add("is-highlighted");
  });
  if (state.selected?.object_type !== "Station") return;
  const entry = state.featureIndex.get(
    featureIndexKey("Station", state.selected.id),
  );
  const target = document.getElementById("selectedObject");
  if (entry && target) {
    target.innerHTML = stationDetailHtml(entry.feature?.properties || {});
  }
}

function stationRainfallForProps(props = {}) {
  if (props.station_type !== "meteorological") return null;
  const stationId = String(props.station_id || props.station_code || "");
  const current = state.stationRainfall.get(stationId) || null;
  if (!current || state.mapTimeContext.mode !== "time_slice") {
    return current ? {
      ...current,
      display_mode: "current",
      display_time: state.stationRainfallObservedAt,
      envelope_context: state.mapTimeContext.mode === "envelope",
    } : null;
  }
  const forecast = state.stationRainfallForecast.get(stationId)?.series || [];
  const point = interpolateTimedPoint(
    [
      {
        valid_time: state.stationRainfallObservedAt,
        rainfall_mm: current.rainfall_mm,
      },
      ...forecast,
    ],
    state.mapTimeContext.validAt,
    ["rainfall_mm"],
  );
  return point ? {
    ...current,
    ...point,
    station_id: stationId,
    display_mode: "forecast",
    display_time: state.mapTimeContext.validAt,
  } : current;
}

function reservoirTelemetryForProps(props = {}) {
  if (props.station_type !== "reservoir" && props.reservoir_id !== "longtan") return null;
  const stationId = String(props.station_id || props.station_code || "");
  if (stationId !== LONGTAN_RESERVOIR_STATION_ID && props.reservoir_id !== "longtan") return null;
  const current = state.reservoirTelemetry;
  if (!current || state.mapTimeContext.mode !== "time_slice") {
    return current ? {
      ...current,
      status: state.reservoirAssessment?.current?.status || null,
      display_mode: "current",
      display_time: state.reservoirTelemetryObservedAt,
      envelope_context: state.mapTimeContext.mode === "envelope",
    } : null;
  }
  const point = interpolateTimedPoint(
    [
      {
        valid_time: state.reservoirTelemetryObservedAt,
        ...current,
        status: state.reservoirAssessment?.current?.status || null,
      },
      ...(state.reservoirForecast?.series || []),
    ],
    state.mapTimeContext.validAt,
    ["reservoir_level_m", "reservoir_inflow_m3s", "reservoir_release_m3s"],
  );
  return point ? {
    ...current,
    ...point,
    station_id: LONGTAN_RESERVOIR_STATION_ID,
    display_mode: "forecast",
    display_time: state.mapTimeContext.validAt,
  } : current;
}

function stationRainfallBadgeHtml(reading) {
  if (!reading) return "";
  const rainfall = Number(reading.rainfall_mm || 0);
  const level = rainfallLevel(rainfall);
  const side = stationRainfallBadgeSide(reading.station_id);
  return `
    <span class="station-rainfall-badge is-${level} is-${side}" aria-hidden="true">
      <strong>${escapeHtml(formatStationRainfall(rainfall))}</strong><small>mm</small>
    </span>
  `;
}

function stationRainfallBadgeSide(stationId) {
  const checksum = Array.from(String(stationId || "")).reduce(
    (total, character, index) => total + character.charCodeAt(0) * (index + 1),
    0,
  );
  return checksum % 2 === 0 ? "left" : "right";
}

function stationRainfallPopupHtml(reading) {
  if (!reading) return "";
  const rainfall = Number(reading.rainfall_mm || 0);
  const forecast = reading.display_mode === "forecast";
  return `
    <div class="station-rainfall-panel">
      <div class="station-rainfall-current is-${rainfallLevel(rainfall)}">
        <div>
          <span class="station-rainfall-current-label">${forecast ? "当前地图预测" : "当前模拟观测"}</span>
          <div class="station-rainfall-current-value">
            <strong>${escapeHtml(formatStationRainfall(rainfall))}</strong><span>mm</span>
          </div>
        </div>
        <time>${escapeHtml(formatRainfallChartTime(reading.display_time || state.stationRainfallObservedAt))}</time>
      </div>
      ${stationRainfallChartHtml(reading.station_id)}
    </div>
  `;
}

function stationRainfallChartHtml(stationId) {
  const observed = state.stationRainfallHistory.get(String(stationId || "")) || [];
  const forecast = state.stationRainfallForecast.get(String(stationId || ""))?.series || [];
  const points = [
    ...observed.map((point) => ({
      time: point.observed_time,
      rainfall_mm: point.rainfall_mm,
      kind: "observed",
    })),
    ...forecast.map((point) => ({
      time: point.valid_time,
      rainfall_mm: point.rainfall_mm,
      kind: "forecast",
    })),
  ];
  if (!points.length) return "";

  const width = 280;
  const chartTop = 14;
  const baseline = 86;
  const chartHeight = baseline - chartTop;
  const step = width / points.length;
  const barWidth = Math.max(2, Math.min(8, step - 1.2));
  const maxRainfall = Math.max(1, ...points.map((point) => Number(point.rainfall_mm) || 0));
  const bars = points.map((point, index) => {
    const rainfall = Math.max(0, Number(point.rainfall_mm) || 0);
    const height = rainfall > 0
      ? Math.max(2, rainfall / maxRainfall * chartHeight)
      : 1;
    const x = index * step + (step - barWidth) / 2;
    const y = baseline - height;
    const label = point.kind === "observed" ? "模拟观测" : "预测";
    return `
      <rect class="is-${point.kind}" x="${x.toFixed(2)}" y="${y.toFixed(2)}"
        width="${barWidth.toFixed(2)}" height="${height.toFixed(2)}" rx="0.8">
        <title>${escapeHtml(`${formatRainfallChartTime(point.time)} · ${label} ${formatStationRainfall(rainfall)} mm`)}</title>
      </rect>
    `;
  }).join("");
  const currentX = Math.min(width - 1, Math.max(1, observed.length * step));
  const currentPercent = currentX / width * 100;
  const currentTimeClass = currentPercent < 18
    ? "is-near-start"
    : (currentPercent > 82 ? "is-near-end" : "");
  const futureTotal = forecast.reduce(
    (total, point) => total + (Number(point.rainfall_mm) || 0),
    0,
  );
  const peak = forecast.reduce((current, point) => (
    !current || Number(point.rainfall_mm) > Number(current.rainfall_mm)
      ? point
      : current
  ), null);
  const firstTime = points[0]?.time;
  const lastTime = points.at(-1)?.time;
  const forecastHours = forecast.length;

  return `
    <div class="station-rainfall-chart" role="group" aria-label="逐小时降雨模拟观测和预测">
      <div class="station-rainfall-chart-head">
        <strong>逐小时降雨</strong><span>mm</span>
        <div class="station-rainfall-legend" aria-hidden="true">
          <span class="is-observed">模拟观测</span>
          <span class="is-forecast">预测</span>
        </div>
      </div>
      <svg viewBox="0 0 ${width} 92" role="img" aria-label="蓝色为模拟观测，橙色为未来预测">
        <line class="station-rainfall-grid" x1="0" y1="${chartTop}" x2="${width}" y2="${chartTop}"></line>
        <line class="station-rainfall-grid" x1="0" y1="${((chartTop + baseline) / 2).toFixed(1)}" x2="${width}" y2="${((chartTop + baseline) / 2).toFixed(1)}"></line>
        <line class="station-rainfall-axis" x1="0" y1="${baseline}" x2="${width}" y2="${baseline}"></line>
        ${bars}
        <line class="station-rainfall-now" x1="${currentX.toFixed(2)}" y1="8" x2="${currentX.toFixed(2)}" y2="${baseline + 2}"></line>
        <text class="station-rainfall-max-label" x="2" y="10">${escapeHtml(formatStationRainfall(maxRainfall))}</text>
      </svg>
      <div class="station-rainfall-chart-times" style="--rainfall-now-position: ${currentPercent.toFixed(2)}%">
        <span>${observed.length > 3 ? escapeHtml(formatRainfallChartTime(firstTime)) : ""}</span>
        <strong class="${currentTimeClass}">${escapeHtml(formatRainfallChartTime(state.stationRainfallObservedAt))}</strong>
        <span>${escapeHtml(formatRainfallChartTime(lastTime))}</span>
      </div>
      ${forecastHours ? `
        <div class="station-rainfall-forecast-summary">
          <div><span>未来 ${forecastHours} 小时累计</span><strong>${escapeHtml(formatStationRainfall(futureTotal))} mm</strong></div>
          <div><span>预测峰值</span><strong>${escapeHtml(formatStationRainfall(peak?.rainfall_mm))} mm</strong><small>${escapeHtml(formatRainfallChartTime(peak?.valid_time))}</small></div>
        </div>
      ` : ""}
    </div>
  `;
}

function formatRainfallChartTime(value) {
  if (!value) return "--";
  const match = String(value).match(/^\d{4}-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
  if (!match) return formatMockTime(value);
  return `${Number(match[1])}/${Number(match[2])} ${match[3]}:${match[4]}`;
}

function formatStationRainfall(value) {
  const rainfall = Number(value);
  if (!Number.isFinite(rainfall)) return "--";
  if (rainfall > 0 && rainfall < 0.1) return "<0.1";
  return rainfall.toFixed(1);
}

function rainfallLevel(value) {
  const rainfall = Number(value || 0);
  if (rainfall <= 0) return "dry";
  if (rainfall < 2.5) return "light";
  if (rainfall < 8) return "moderate";
  if (rainfall < 16) return "heavy";
  if (rainfall < 30) return "storm";
  return "severe";
}

function updateReservoirTelemetry(observation, observedAt) {
  state.reservoirDispatchTime = null;
  const reservoirLevel = finiteTelemetryNumber(observation?.reservoir_level_m);
  const current = observedAt && reservoirLevel !== null
    ? {
      station_id: LONGTAN_RESERVOIR_STATION_ID,
      reservoir_id: "longtan",
      reservoir_dispatch: observation.reservoir_dispatch || null,
      reservoir_level_m: reservoirLevel,
      reservoir_inflow_m3s: finiteTelemetryNumber(observation.reservoir_inflow_m3s),
      reservoir_release_m3s: finiteTelemetryNumber(observation.reservoir_release_m3s),
    }
    : null;
  state.reservoirTelemetry = current;
  state.reservoirTelemetryObservedAt = current ? String(observedAt) : null;
  if (current) recordReservoirTelemetryHistory(current, observedAt);

  const forecast = normalizeReservoirForecast(observation?.reservoir_forecast);
  state.reservoirForecast = forecast;
  state.reservoirAssessment = forecast?.assessment || null;
  refreshStationTelemetryMarkers();
}

function finiteTelemetryNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function recordReservoirTelemetryHistory(current, observedAt) {
  if (!observedAt) return;
  const time = String(observedAt);
  const history = state.reservoirTelemetryHistory
    .filter((point) => point.observed_time !== time);
  history.push({
    observed_time: time,
    reservoir_dispatch: current.reservoir_dispatch,
    reservoir_level_m: current.reservoir_level_m,
    reservoir_inflow_m3s: current.reservoir_inflow_m3s,
    reservoir_release_m3s: current.reservoir_release_m3s,
  });
  history.sort((left, right) => left.observed_time.localeCompare(right.observed_time));
  state.reservoirTelemetryHistory = history.slice(-RESERVOIR_TELEMETRY_HISTORY_LIMIT);
}

function normalizeReservoirForecast(forecast) {
  if (!forecast || typeof forecast !== "object") return null;
  const series = (Array.isArray(forecast.series) ? forecast.series : [])
    .map((point) => ({
      valid_time: String(point?.valid_time || ""),
      reservoir_level_m: finiteTelemetryNumber(point?.reservoir_level_m),
      reservoir_inflow_m3s: finiteTelemetryNumber(point?.reservoir_inflow_m3s),
      reservoir_release_m3s: finiteTelemetryNumber(point?.reservoir_release_m3s),
      status: point?.status || null,
      reservoir_dispatch: point?.reservoir_dispatch || null,
    }))
    .filter((point) => point.valid_time && point.reservoir_level_m !== null);
  return { ...forecast, series };
}

function ensureReservoirStationLayer(observation) {
  if (
    finiteTelemetryNumber(observation?.reservoir_level_m) === null
    || state.reservoirStationLayerInitialized
  ) return;
  state.reservoirStationLayerInitialized = true;
  void loadObject("Station", { station_type: "reservoir" }, {
    fit: false,
    label: "水库测站",
  }).catch((error) => {
    state.reservoirStationLayerInitialized = false;
    addTrace("ERR", "水库测站加载失败", error.message || String(error));
  });
}

function reservoirLevelBadgeHtml(telemetry) {
  if (!telemetry) return "";
  const status = telemetry.status || state.reservoirAssessment?.current?.status;
  const statusKey = reservoirStatusKey(status);
  return `
    <span class="station-reservoir-badge is-${statusKey}" aria-hidden="true">
      <strong>${escapeHtml(formatReservoirLevel(telemetry.reservoir_level_m))}</strong><small>m</small>
    </span>
  `;
}

function reservoirStatusKey(status) {
  const key = String(status?.key || "normal");
  return ["normal", "warning", "danger", "critical"].includes(key) ? key : "normal";
}

const DISPATCH_STATES = {
  NORMAL: { label: "正常蓄水", color: "#238b57" },
  PRERELEASE: { label: "预泄判定", color: "#a47708" },
  FULL_CAPACITY: { label: "全能力下泄", color: "#c46114" },
  DRAWDOWN: { label: "退水腾库", color: "#087f8c" },
  EMERGENCY: { label: "应急下泄判定", color: "#c62828" },
};

function reservoirDispatchPoints() {
  return reservoirChartTimeline(state.reservoirTelemetryHistory, state.reservoirForecast?.series || []);
}

function selectedReservoirDispatchPoint() {
  const points = reservoirDispatchPoints();
  const requested = state.reservoirDispatchTime
    || (state.mapTimeContext.mode === "time_slice" ? state.mapTimeContext.validAt : state.reservoirTelemetryObservedAt);
  const target = Date.parse(requested);
  // Decisions are discrete hourly results; never interpolate state/reason/capacity.
  if (!Number.isFinite(target)) return null;
  if (!points.length || target < Date.parse(points[0].time) || target > Date.parse(points.at(-1).time)) return null;
  return points.findLast((point) => Date.parse(point.time) <= target) || null;
}

function reservoirTelemetryPopupHtml(telemetry) {
  if (!telemetry) return `<div class="station-reservoir-panel dispatch-empty">开始演进后查看水库模拟调度过程。</div>`;
  const point = selectedReservoirDispatchPoint();
  const decision = point?.reservoir_dispatch;
  const info = DISPATCH_STATES[decision?.state];
  const future = point && Date.parse(point.time) > Date.parse(state.reservoirTelemetryObservedAt);
  const thresholds = decision?.thresholds;
  const stats = point ? [
    ["入库流量", formatReservoirFlow(point.reservoir_inflow_m3s), "m³/s"],
    ["计算泄流", formatReservoirFlow(point.reservoir_release_m3s), "m³/s"],
    ["时段末水位", formatReservoirLevel(point.reservoir_level_m), "m"],
  ].map(([label, value, unit]) => `<div><span>${label}</span><strong>${escapeHtml(value)}</strong><small>${unit}</small></div>`).join("") : "";
  return `<div class="station-reservoir-panel dispatch-panel">
    <header class="dispatch-head" style="--dispatch-color:${info?.color || "#64748b"}">
      <div><span>模拟调度 · ${!point ? "暂无数据" : future ? "未来预计算" : "已回放"}</span>
        <h3>${escapeHtml(info?.label || "暂无调度记录")}</h3></div>
      <button type="button" data-dispatch-time="" title="跟随当前地图时间">跟随时间</button>
    </header>
    <div class="dispatch-time">${point ? escapeHtml(formatMockTime(point.time)) : "该时刻不在当前可查看的调度范围内"}</div>
    <div class="dispatch-metrics">${stats}</div>
    <div class="dispatch-chart-tabs" role="group" aria-label="调度过程图">
      <button type="button" data-dispatch-chart="flow" aria-pressed="${state.reservoirDispatchChart === "flow"}">入流 / 泄流</button>
      <button type="button" data-dispatch-chart="level" aria-pressed="${state.reservoirDispatchChart === "level"}">库水位</button>
    </div>
    ${state.reservoirDispatchChart === "level" ? reservoirLevelChartHtml() : reservoirFlowChartHtml()}
    ${reservoirDispatchTimelineHtml(point)}
    ${decision ? `<details class="dispatch-reason">
      <summary>调度依据与约束</summary>
    ${thresholds ? `<div class="dispatch-limits">汛限 ${thresholds.flood_limit_level_m.toFixed(2)} · 设计 ${thresholds.design_flood_level_m.toFixed(2)} · 校核 ${thresholds.check_flood_level_m.toFixed(2)} m</div>` : ""}
      <p>${escapeHtml(decision.reason || "暂无判定依据")}</p>
      <dl><div><dt>时段初水位（泄流能力依据）</dt><dd>${escapeHtml(formatReservoirLevel(decision.start_level_m))} m</dd></div><div><dt>预泄所需平均流量</dt><dd>${escapeHtml(formatReservoirFlow(decision.required_release_m3s))} m³/s</dd></div>
      <div><dt>当前泄流能力</dt><dd>${escapeHtml(formatReservoirFlow(decision.available_release_m3s))} m³/s</dd></div>
      <div><dt>全能力下泄预计最高水位</dt><dd>${escapeHtml(formatReservoirLevel(decision.forecast_max_level_full_m))} m</dd></div></dl>
      <p class="dispatch-constraint">${escapeHtml(decision.constraint || "")}</p>
      ${decision.curve_extrapolated ? '<p class="dispatch-constraint">本时段结果包含曲线范围外推。</p>' : ""}
    </details>` : ""}
    <footer class="dispatch-footnote">虚线为预计算 · 点击状态带查看 · 模拟调度</footer>
  </div>`;
}

function reservoirDispatchTimelineHtml(selected) {
  const points = reservoirDispatchPoints();
  const segments = points.map((point) => {
    const info = DISPATCH_STATES[point.reservoir_dispatch?.state];
    const future = Date.parse(point.time) > Date.parse(state.reservoirTelemetryObservedAt);
    const label = `${formatMockTime(point.time)} · ${future ? "未来预计算" : "已回放"} · ${info?.label || "暂无记录"}`;
    return `<button type="button" class="dispatch-segment${future ? " is-future" : ""}" style="--dispatch-color:${info?.color || "#cbd5e1"}" data-dispatch-time="${escapeHtml(point.time)}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}" aria-pressed="${point.time === selected?.time}"></button>`;
  }).join("");
  const legend = Object.values(DISPATCH_STATES).map((info) => `<span style="--dispatch-color:${info.color}">${info.label}</span>`).join("");
  return `<section class="dispatch-timeline"><strong>逐时调度状态</strong><div class="dispatch-track">${segments}</div><div class="dispatch-legend">${legend}</div></section>`;
}

function reservoirDispatchCursorHtml(timeline, width, bottom) {
  const selected = selectedReservoirDispatchPoint();
  const index = timeline.findIndex((point) => point.time === selected?.time);
  if (index < 0) return "";
  const x = reservoirChartXFactory(timeline.length, width)(index);
  return `<line class="dispatch-cursor" x1="${x}" x2="${x}" y1="7" y2="${bottom}"></line>`;
}

function reservoirLevelChartHtml() {
  const observed = state.reservoirTelemetryHistory;
  const forecast = state.reservoirForecast?.series || [];
  const timeline = reservoirChartTimeline(observed, forecast);
  const levels = timeline
    .map((point) => finiteTelemetryNumber(point.reservoir_level_m))
    .filter((value) => value !== null);
  const dispatchLimits = state.reservoirTelemetry?.reservoir_dispatch?.thresholds;
  const thresholds = dispatchLimits ? {
    normal_pool_level_m: dispatchLimits.flood_limit_level_m,
    design_flood_level_m: dispatchLimits.design_flood_level_m,
    check_flood_level_m: dispatchLimits.check_flood_level_m,
  } : state.reservoirAssessment?.thresholds;
  if (!levels.length || !thresholds) return "";

  const width = 340;
  const chartTop = 12;
  const chartBottom = 94;
  const normalPool = Number(thresholds.normal_pool_level_m);
  const designFlood = Number(thresholds.design_flood_level_m);
  const checkFlood = Number(thresholds.check_flood_level_m);
  const chartMin = Math.min(normalPool, ...levels) - 0.18;
  const chartMax = Math.max(checkFlood, ...levels) + 0.18;
  const yFor = (value) => chartBottom
    - (Number(value) - chartMin) / (chartMax - chartMin) * (chartBottom - chartTop);
  const xFor = reservoirChartXFactory(timeline.length, width);
  const observedPath = reservoirChartPath(observed, 0, "reservoir_level_m", xFor, yFor);
  const forecastStart = Math.max(0, observed.length - 1);
  const forecastPoints = observed.length
    ? [observed.at(-1), ...forecast]
    : forecast;
  const forecastPath = reservoirChartPath(
    forecastPoints,
    forecastStart,
    "reservoir_level_m",
    xFor,
    yFor,
  );
  const nowX = xFor(Math.max(0, observed.length - 1));
  const nowPercent = nowX / width * 100;
  const bands = [
    ["normal", chartMin, normalPool],
    ["warning", normalPool, designFlood],
    ["danger", designFlood, checkFlood],
    ["critical", checkFlood, chartMax],
  ].map(([key, low, high]) => {
    const top = yFor(high);
    const bottom = yFor(low);
    return `<rect class="station-reservoir-level-band is-${key}" x="0" y="${top.toFixed(2)}" width="${width}" height="${Math.max(0, bottom - top).toFixed(2)}"></rect>`;
  }).join("");
  const thresholdLines = [normalPool, designFlood, checkFlood].map((level) => `
    <line class="station-reservoir-threshold" x1="0" y1="${yFor(level).toFixed(2)}" x2="${width}" y2="${yFor(level).toFixed(2)}"></line>
    <text class="station-reservoir-threshold-label" x="3" y="${Math.max(8, yFor(level) - 2).toFixed(2)}">${level.toFixed(2)}</text>
  `).join("");

  return `
    <div class="station-reservoir-chart" role="group" aria-label="水库水位模拟观测和未来预测">
      <div class="station-reservoir-chart-head">
        <strong>水库水位</strong><span>m</span>
        <div class="station-reservoir-legend" aria-hidden="true">
          <span class="is-observed">模拟观测</span>
          <span class="is-forecast">预测</span>
        </div>
      </div>
      <svg viewBox="0 0 ${width} 100" role="img" aria-label="实线为模拟观测，虚线为未来24小时预测">
        ${bands}
        ${thresholdLines}
        ${reservoirDispatchCursorHtml(timeline, width, chartBottom)}
        <path class="station-reservoir-level-line is-observed" d="${observedPath}"></path>
        <path class="station-reservoir-level-line is-forecast" d="${forecastPath}"></path>
        <line class="station-reservoir-now" x1="${nowX.toFixed(2)}" y1="7" x2="${nowX.toFixed(2)}" y2="${chartBottom + 2}"></line>
      </svg>
      ${reservoirChartTimesHtml(timeline, nowPercent)}
    </div>
  `;
}

function reservoirFlowChartHtml() {
  const observed = state.reservoirTelemetryHistory;
  const forecast = state.reservoirForecast?.series || [];
  const timeline = reservoirChartTimeline(observed, forecast);
  if (!timeline.length) return "";
  const values = timeline.flatMap((point) => [
    finiteTelemetryNumber(point.reservoir_inflow_m3s),
    finiteTelemetryNumber(point.reservoir_release_m3s),
  ]).filter((value) => value !== null);
  if (!values.length) return "";

  const width = 340;
  const chartTop = 12;
  const chartBottom = 82;
  const maxFlow = Math.max(1, ...values) * 1.08;
  const yFor = (value) => chartBottom
    - Math.max(0, Number(value)) / maxFlow * (chartBottom - chartTop);
  const xFor = reservoirChartXFactory(timeline.length, width);
  const forecastStart = Math.max(0, observed.length - 1);
  const forecastPoints = observed.length
    ? [observed.at(-1), ...forecast]
    : forecast;
  const nowX = xFor(Math.max(0, observed.length - 1));
  const paths = [
    ["inflow is-observed", observed, 0, "reservoir_inflow_m3s"],
    ["release is-observed", observed, 0, "reservoir_release_m3s"],
    ["inflow is-forecast", forecastPoints, forecastStart, "reservoir_inflow_m3s"],
    ["release is-forecast", forecastPoints, forecastStart, "reservoir_release_m3s"],
  ].map(([className, points, startIndex, key]) => `
    <path class="station-reservoir-flow-line ${className}" d="${reservoirChartPath(points, startIndex, key, xFor, yFor)}"></path>
  `).join("");

  return `
    <div class="station-reservoir-chart is-flow" role="group" aria-label="入库和泄洪流量模拟观测和未来预测">
      <div class="station-reservoir-chart-head">
        <strong>入库与泄洪流量</strong><span>m³/s</span>
        <div class="station-reservoir-legend is-flow" aria-hidden="true">
          <span class="is-inflow">入库</span>
          <span class="is-release">泄洪</span>
          <span class="is-dashed">预测</span>
        </div>
      </div>
      <svg viewBox="0 0 ${width} 88" role="img" aria-label="蓝线为入库流量，绿色为泄洪流量，虚线为预测">
        <line class="station-reservoir-grid" x1="0" y1="${chartTop}" x2="${width}" y2="${chartTop}"></line>
        <line class="station-reservoir-grid" x1="0" y1="${((chartTop + chartBottom) / 2).toFixed(2)}" x2="${width}" y2="${((chartTop + chartBottom) / 2).toFixed(2)}"></line>
        <line class="station-reservoir-axis" x1="0" y1="${chartBottom}" x2="${width}" y2="${chartBottom}"></line>
        ${paths}
        ${reservoirDispatchCursorHtml(timeline, width, chartBottom)}
        <line class="station-reservoir-now" x1="${nowX.toFixed(2)}" y1="7" x2="${nowX.toFixed(2)}" y2="${chartBottom + 2}"></line>
        <text class="station-reservoir-max-label" x="2" y="10">${escapeHtml(formatReservoirFlow(maxFlow))}</text>
      </svg>
      ${reservoirChartTimesHtml(timeline, nowX / width * 100)}
    </div>
  `;
}

function reservoirChartTimeline(observed, forecast) {
  return [
    ...observed.map((point) => ({ ...point, time: point.observed_time })),
    ...forecast.map((point) => ({ ...point, time: point.valid_time })),
  ];
}

function reservoirChartXFactory(pointCount, width) {
  const denominator = Math.max(1, pointCount - 1);
  return (index) => Math.max(0, Math.min(width, index / denominator * width));
}

function reservoirChartPath(points, startIndex, key, xFor, yFor) {
  return points.map((point, offset) => {
    const value = finiteTelemetryNumber(point?.[key]);
    if (value === null) return "";
    const command = offset === 0 ? "M" : "L";
    return `${command}${xFor(startIndex + offset).toFixed(2)},${yFor(value).toFixed(2)}`;
  }).filter(Boolean).join(" ");
}

function reservoirChartTimesHtml(timeline, nowPercent) {
  const currentClass = nowPercent < 18
    ? "is-near-start"
    : (nowPercent > 82 ? "is-near-end" : "");
  return `
    <div class="station-reservoir-chart-times" style="--reservoir-now-position: ${nowPercent.toFixed(2)}%">
      <span>${timeline.length > 3 ? escapeHtml(formatRainfallChartTime(timeline[0]?.time)) : ""}</span>
      <strong class="${currentClass}">${escapeHtml(formatRainfallChartTime(state.reservoirTelemetryObservedAt))}</strong>
      <span>${escapeHtml(formatRainfallChartTime(timeline.at(-1)?.time))}</span>
    </div>
  `;
}

function reservoirForecastAssessmentHtml() {
  const assessment = state.reservoirAssessment;
  const peak = assessment?.peak;
  if (!peak) return "";
  const alert = assessment.alert;
  const statusKey = reservoirStatusKey(peak.status);
  return `
    <div class="station-reservoir-forecast-summary">
      <div>
        <span>未来 ${escapeHtml(String(assessment.window_hours || 24))} 小时最高水位</span>
        <strong class="is-${statusKey}">${escapeHtml(formatReservoirLevel(peak.level_m))} m</strong>
        <small>${escapeHtml(formatRainfallChartTime(peak.valid_time))}</small>
      </div>
      ${alert ? `
        <p class="station-reservoir-alert is-${escapeHtml(alert.severity || "warning")}">${escapeHtml(reservoirAlertText(alert))}</p>
      ` : ""}
    </div>
  `;
}

function reservoirAlertText(alert) {
  if (!alert) return "";
  const template = alert.triggered_in_forecast
    ? alert.future_text_template
    : alert.current_text_template;
  if (!template || !alert.triggered_at) return String(alert.text || "");
  return String(template).replaceAll(
    "{triggered_at}",
    formatMockTime(alert.triggered_at),
  );
}

function formatReservoirLevel(value) {
  const level = finiteTelemetryNumber(value);
  return level === null ? "--" : level.toFixed(2);
}

function formatReservoirFlow(value) {
  const flow = finiteTelemetryNumber(value);
  if (flow === null) return "--";
  return Math.abs(flow) < 10 ? flow.toFixed(2) : flow.toFixed(1);
}

function unindexLayer(objectType, group) {
  if (!group) return;
  group.eachLayer?.((layerItem) => {
    if (!layerItem.feature && layerItem.eachLayer) {
      unindexLayer(objectType, layerItem);
      return;
    }
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
  if (!objectType || !objectId || MAP_NON_SELECTABLE_OBJECTS.has(objectType)) {
    throw new Error("缺少可定位的具体对象");
  }
  let entry = state.featureIndex.get(featureIndexKey(objectType, objectId));
  if (!entry || !state.map.hasLayer(entry.layer)) {
    await loadObject(objectType, {}, { fit: false, objectIds: [String(objectId)], label: action.label });
    entry = state.featureIndex.get(featureIndexKey(objectType, objectId));
  }
  if (!entry) throw new Error(`未找到对象：${objectType} ${objectId}`);
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
  clearImpactRoadFocus();
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
  if (objectType === "Road") return roadDetailHtml(props);
  if (objectType === "RoadRoute") return roadRouteDetailHtml(props);
  if (objectType === "EvacuationRoute") return routeDetailHtml(props);
  if (objectType === "Station") return stationDetailHtml(props);
  const keys = Object.keys(props).filter((key) => props[key] !== "" && props[key] !== null && key !== "geometry");
  const rows = keys.slice(0, 8).map((key) => `<div><strong>${escapeHtml(key)}</strong>: ${escapeHtml(String(props[key]))}</div>`);
  return `<div class="muted"><strong>${escapeHtml(OBJECT_CONFIG[objectType]?.label || objectType)}</strong>${rows.join("")}</div>`;
}

function roadDetailHtml(props) {
  const rows = [
    ["名称", props.name],
    ["道路编号", props.ref || "未标注"],
    ["类型", roadClassInfo(props).label],
    ["路段ID", props.road_id],
    ["路段长度", formatRouteDistance(props.length_m)],
    ["通行方向", { F: "单向", T: "反向单向", B: "双向" }[props.one_way] || "未标注"],
    ["桥上路段", props.bridge_flag ? "是" : "否"],
    ["隧道路段", props.tunnel_flag ? "是" : "否"],
  ];
  const routes = (props.road_route_ids || []).map((id) => roadObjectButton("RoadRoute", id, id.replace("road_route_", ""))).join("");
  return `<div class="muted"><strong>道路路段</strong>${rows.map(([label, value]) => `<div><strong>${label}</strong>: ${escapeHtml(String(value ?? "未标注"))}</div>`).join("")}
    <div>所属编号道路：${routes || "未归属（缺少可靠编号）"}</div></div>`;
}

function roadObjectButton(objectType, objectId, label) {
  return `<button type="button" class="road-object-link" data-road-object-type="${escapeHtml(objectType)}" data-road-object-id="${escapeHtml(String(objectId))}">${escapeHtml(String(label))}</button>`;
}

function roadRouteDetailHtml(props) {
  const segments = (props.road_ids || []).map((id) => roadObjectButton("Road", id, id)).join("");
  return `<div class="muted"><strong>${escapeHtml(props.ref)} · ${escapeHtml(props.name)}</strong>
    <div>${escapeHtml(roadClassInfo(props).label)} · 已收录 ${escapeHtml(String(props.segment_count))} 段</div>
    <div>共线路段：${escapeHtml(String(props.shared_segment_count))} 段</div>
    <div>路段累计长度：${escapeHtml(formatRouteDistance(props.recorded_length_m))}（含双向线及匝道）</div>
    <div>${escapeHtml(props.coverage_scope)}</div>
    <details><summary>成员路段（点击定位）</summary><div class="road-member-list">${segments}</div></details>
  </div>`;
}

function stationDetailHtml(props) {
  const stationType = objectIconInfo("Station", props).label;
  const rainfall = stationRainfallForProps(props);
  const reservoirTelemetry = reservoirTelemetryForProps(props);
  const rows = [
    ["名称", props.name],
    ["编码", props.station_code || props.station_id],
    ["类型", stationType],
    ["数据来源", props.source_system],
    ["当前模拟观测雨量", rainfall ? `${formatStationRainfall(rainfall.rainfall_mm)} mm` : null],
    ["当前水库水位", reservoirTelemetry ? `${formatReservoirLevel(reservoirTelemetry.reservoir_level_m)} m` : null],
    ["当前入库流量", reservoirTelemetry ? `${formatReservoirFlow(reservoirTelemetry.reservoir_inflow_m3s)} m³/s` : null],
    ["当前泄洪流量", reservoirTelemetry ? `${formatReservoirFlow(reservoirTelemetry.reservoir_release_m3s)} m³/s` : null],
    ["数据时刻", rainfall
      ? formatMockTime(state.stationRainfallObservedAt)
      : (reservoirTelemetry ? formatMockTime(state.reservoirTelemetryObservedAt) : null)],
    ["经度", props.longitude],
    ["纬度", props.latitude],
  ].filter(([, value]) => value !== "" && value !== null && value !== undefined);
  return `
    <div class="muted station-detail">
      <strong>测站</strong>
      ${rows.map(([label, value]) => `<div><strong>${escapeHtml(label)}</strong>: ${escapeHtml(String(value))}</div>`).join("")}
    </div>
  `;
}

function routeDetailHtml(props) {
  const steps = parseRouteInstructions(props.instructions);
  const summaryRows = [
    ["路线ID", props.evacuation_route_id],
    ["名称", props.name],
    ["方式", routeProfileLabel(props.profile)],
    ["洪水校核", {initial_dry: "演示初始无洪水", not_checked: "未进行（历史记录）", checked: "已按预测校核"}[props.flood_validation]],
    ["距离", formatRouteDistance(props.length_m)],
    ["预计用时", formatRouteDuration(props.duration_s)],
    ["道路", props.road_detail],
  ].filter(([, value]) => value !== "" && value !== null && value !== undefined && value !== "--");
  const summary = summaryRows
    .map(([label, value]) => `<div><strong>${escapeHtml(label)}</strong>: ${escapeHtml(String(value))}</div>`)
    .join("");
  return `
    <div class="muted route-detail">
      <strong>${escapeHtml(OBJECT_CONFIG.EvacuationRoute?.label || "路线")}</strong>
      ${summary}
      ${routeNavigationHtml(steps)}
    </div>
  `;
}

function routeNavigationHtml(steps) {
  if (!steps.length) {
    return '<div class="route-navigation-empty">暂无逐步导航信息</div>';
  }
  const items = steps.map((step, index) => {
    const instruction = step.text || step.instruction || "继续沿路线行进";
    const street = step.street_name && step.street_name !== instruction ? step.street_name : "";
    const meta = [
      formatRouteDistance(step.distance),
      formatRouteStepDuration(step.time),
    ].filter((value) => value && value !== "--").join(" · ");
    return `
      <li>
        <span class="route-step-index">${index + 1}</span>
        <div>
          <strong>${escapeHtml(instruction)}</strong>
          ${street ? `<small>${escapeHtml(street)}</small>` : ""}
          ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
        </div>
      </li>
    `;
  }).join("");
  return `
    <section class="route-navigation" aria-label="导航步骤">
      <div class="route-navigation-title">导航步骤</div>
      <ol>${items}</ol>
    </section>
  `;
}

function parseRouteInstructions(value) {
  if (Array.isArray(value)) return value.filter((item) => item && typeof item === "object");
  if (!value) return [];
  try {
    const parsed = JSON.parse(String(value));
    return Array.isArray(parsed) ? parsed.filter((item) => item && typeof item === "object") : [];
  } catch (_error) {
    return [];
  }
}

function routeProfileLabel(value) {
  return { car: "驾车", foot: "步行" }[String(value || "").toLowerCase()] || value || "--";
}

function formatRouteDistance(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "--";
  return number >= 1000 ? `${(number / 1000).toFixed(1)} km` : `${number.toFixed(0)} m`;
}

function formatRouteDuration(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "--";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${Math.max(1, minutes)} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}

function formatRouteStepDuration(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "";
  const seconds = number > 10000 ? number / 1000 : number;
  return formatRouteDuration(seconds);
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

  await refreshPlaybackStatus();
  settlePendingQuestion();
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

  const listen = (name, callback) => es.addEventListener(name, event => {
    const data = JSON.parse(event.data);
    if (data.workspace_id && data.workspace_id !== state.workspaceId) return;
    callback(event);
  });

  listen("run", (event) => {
    const data = parseEvent(event);
    state.activeRunId = data.run_id;
  });

  listen("map_actions", async (event) => {
    const data = parseEvent(event);
    await enqueueMapActions(data);
  });

  listen("directive_draft", (event) => {
    const data = parseEvent(event);
    openDirectiveDraft(data.draft || {});
    attachDirectiveDraftCard(assistant, data.draft || {});
    addTrace("COMMAND", "应急指令初稿已生成", data.draft?.title || "已打开指令编辑器");
  });

  listen("text", (event) => {
    const data = parseEvent(event);
    appendMessageMarkdown(assistant, data.content || "");
    scrollChat();
  });

  listen("tool_call", (event) => {
    const data = parseEvent(event);
    addTrace("CALL", readableTool(data.name, data.args || {}), JSON.stringify(data.args || {}, null, 2));
  });

  listen("domain_result", (event) => {
    const data = parseEvent(event);
    if (data.name === "analyze_inundation_impacts") registerImpactAnalysisResult(data.result);
  });

  listen("tool_result", (event) => {
    const data = parseEvent(event);
    addTrace(data.blocked ? "BLOCK" : "RESULT", data.name || "tool result", compactText(data.result || ""));
  });

  listen("reasoning", () => {});

  listen("debug", () => {});

  listen("confirmation_required", (event) => {
    const data = parseEvent(event);
    const trace = addTrace("ASK", `需要确认: ${data.tool_name}`, JSON.stringify(data.args || {}, null, 2));
    notifyAgentTrace({tag: "ASK", label: "需要确认", notification_key: state.activeRunId}, trace);
    appendConfirmation(data);
    finishStream(false);
  });

  listen("question", (event) => {
    const data = parseEvent(event);
    const trace = addTrace("ASK", "等待用户输入", data.question || "");
    notifyAgentTrace({tag: "ASK", label: "等待用户输入", notification_key: state.activeRunId}, trace);
    appendQuestion(data);
    finishStream(false);
  });

  listen("done", () => {
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
    if (action.type === "hide_hydrodynamic_mesh") {
      removeObjectTypeLayers("HydrodynamicGridCell");
    }
    if (action.type === "hide_objects") {
      hideMapObjects(action);
    }
    if (action.type === "reset") {
      resetMap();
    }
    if (action.type === "clear_hydrodynamic_result") {
      clearHydrodynamicResults();
    }
    if (action.type === "set_watershed_inundation_alert") {
      await setWatershedInundationAlert(action.active);
    }
    if (action.type === "load_object") {
      await loadObject(action.object_type, action.filters || {}, {
        fit: action.fit,
        label: action.label,
        simplify_tolerance: action.simplify_tolerance,
        refresh: action.refresh,
        objectIds: action.object_ids,
        mode: action.mode || "add",
        selectionId: action.selection_id,
        objectSetId: action.object_set_id,
        replaceObjectIds: action.replace_object_ids,
        highlight: action.highlight,
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

function hydrodynamicFilterSignature(filters = {}) {
  return JSON.stringify(
    Object.entries(filters || {})
      .map(([name, value]) => [name, String(value)])
      .sort(([first], [second]) => first.localeCompare(second)),
  );
}

L.GridLayer.HydrodynamicGrid = L.GridLayer.extend({
  onAdd(map) {
    this._pendingTileRequests ||= new Set();
    L.GridLayer.prototype.onAdd.call(this, map);
    if (this.options.interactiveCells) map.on("click", this._handleCellClick, this);
  },

  onRemove(map) {
    this._resultRevision = (this._resultRevision || 0) + 1;
    this._abortPendingTileRequests();
    map.off("click", this._handleCellClick, this);
    this.clearSelection();
    L.GridLayer.prototype.onRemove.call(this, map);
  },

  setResultFilters(filters) {
    const nextFilters = { ...(filters || {}) };
    if (
      hydrodynamicFilterSignature(this.options.resultFilters)
      === hydrodynamicFilterSignature(nextFilters)
    ) {
      return this;
    }
    this.options.resultFilters = nextFilters;
    this._resultRevision = (this._resultRevision || 0) + 1;
    this._abortPendingTileRequests();
    this.clearSelection();
    // GridLayer.redraw removes the current tiles before requesting replacements.
    return this.redraw();
  },

  _abortPendingTileRequests() {
    this._pendingTileRequests?.forEach((controller) => controller.abort());
    this._pendingTileRequests?.clear();
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
    const requestRevision = this._resultRevision || 0;
    const resultFilters = {
      ...(this.options.resultFilters || { result: "mesh" }),
    };
    const params = new URLSearchParams({
      z: String(coords.z),
      x: String(coords.x),
      y: String(coords.y),
      tile_crs: "gcj02",
    });
    Object.entries(resultFilters).forEach(([name, value]) => {
      params.set(name, value);
    });
    if (this.options.wetOnly) {
      params.set("wet_only", "1");
    }
    const controller = new AbortController();
    this._pendingTileRequests ||= new Set();
    this._pendingTileRequests.add(controller);
    let completed = false;
    const complete = () => {
      if (completed) return;
      completed = true;
      done(null, tile);
    };
    fetch(`/api/hydrodynamic-grid/tile?${params.toString()}`, {
      signal: controller.signal,
    })
      .then((res) => {
        if (!res.ok) throw new Error(`tile ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (requestRevision !== (this._resultRevision || 0) || !this._map) {
          complete();
          return;
        }
        tile._hydrodynamicData = data;
        tile._hydrodynamicCoords = { ...coords };
        drawHydrodynamicTile(ctx, size, coords, data, this.options.renderMode || "mesh");
        complete();
      })
      .catch((error) => {
        if (error?.name !== "AbortError" && requestRevision === (this._resultRevision || 0) && this._map) {
          console.warn("hydrodynamic grid tile failed", error);
        }
        complete();
      })
      .finally(() => this._pendingTileRequests?.delete(controller));
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
  const cells = data.cells.map((cell) => {
    const depth = Number(cell[1] || 0);
    return {
      depth,
      points: [
        latLngToTilePixel(Number(cell[3]), Number(cell[2]), coords.z, origin),
        latLngToTilePixel(Number(cell[5]), Number(cell[4]), coords.z, origin),
        latLngToTilePixel(Number(cell[7]), Number(cell[6]), coords.z, origin),
      ],
    };
  });
  if (renderMode === "result") {
    drawHydrodynamicResultTile(ctx, size, cells);
    return;
  }
  const style = hydrodynamicMeshStyle();
  cells.forEach((cell) => {
    traceHydrodynamicTriangle(ctx, cell.points);
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

function drawHydrodynamicResultTile(ctx, size, cells) {
  if (!cells.length) return;
  const mask = document.createElement("canvas");
  mask.width = size.x;
  mask.height = size.y;
  const maskCtx = mask.getContext("2d");
  maskCtx.fillStyle = "#fff";
  cells.forEach((cell) => {
    traceHydrodynamicTriangle(maskCtx, cell.points);
    maskCtx.fill();
  });

  const outline = document.createElement("canvas");
  outline.width = size.x;
  outline.height = size.y;
  const outlineCtx = outline.getContext("2d");
  const offsets = [[-1, 0], [1, 0], [0, -1], [0, 1], [-0.75, -0.75], [0.75, -0.75], [-0.75, 0.75], [0.75, 0.75]];
  offsets.forEach(([x, y]) => outlineCtx.drawImage(mask, x, y));
  outlineCtx.globalCompositeOperation = "destination-out";
  outlineCtx.drawImage(mask, 0, 0);
  outlineCtx.globalCompositeOperation = "source-in";
  outlineCtx.fillStyle = "rgba(127, 29, 29, 0.58)";
  outlineCtx.fillRect(0, 0, size.x, size.y);
  outlineCtx.globalCompositeOperation = "source-over";
  ctx.drawImage(outline, 0, 0);

  cells.forEach((cell) => {
    const style = hydrodynamicCellStyle(cell.depth);
    traceHydrodynamicTriangle(ctx, cell.points);
    ctx.fillStyle = style.fillColor;
    ctx.globalAlpha = style.fillOpacity;
    ctx.fill();
    ctx.globalAlpha = style.opacity;
    ctx.strokeStyle = style.color;
    ctx.lineWidth = style.weight;
    ctx.stroke();
  });
  ctx.globalAlpha = 1;
}

function traceHydrodynamicTriangle(ctx, points) {
  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);
  ctx.lineTo(points[1].x, points[1].y);
  ctx.lineTo(points[2].x, points[2].y);
  ctx.closePath();
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
    color: "rgba(71, 85, 105, 0.46)",
    weight: 0.5,
    fillColor: "rgba(255, 255, 255, 0)",
    fillOpacity: 0,
    opacity: 0.82,
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
  const t = Math.max(0, Math.min(1, depth / 2.5));
  const color = hydrodynamicDepthColor(depth);
  return {
    color: "rgba(127, 29, 29, 0.34)",
    weight: 0.38,
    fillColor: color,
    fillOpacity: 0.62 + t * 0.3,
    opacity: 0.76,
  };
}

function hydrodynamicDepthColor(depth) {
  const stops = [
    [0, [254, 202, 202]],
    [0.25, [252, 165, 165]],
    [0.6, [248, 113, 113]],
    [1.2, [220, 38, 38]],
    [2.5, [127, 29, 29]],
  ];
  for (let index = 1; index < stops.length; index += 1) {
    const [limit, color] = stops[index];
    if (depth > limit) continue;
    const [previousLimit, previousColor] = stops[index - 1];
    const ratio = (depth - previousLimit) / Math.max(limit - previousLimit, 0.0001);
    return interpolateColor(previousColor, color, ratio);
  }
  return interpolateColor(stops.at(-1)[1], [69, 10, 10], Math.min(1, (depth - 2.5) / 1.5));
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
  if (!Number.isFinite(lon) || !Number.isFinite(lat) || Math.abs(lon) > 180 || Math.abs(lat) > 90) throw new Error("事件坐标无效");
  const eventId = event.event_id || `${event.event_type}:${lon}:${lat}`;
  if (state.eventMarkers.has(eventId)) {
    const existing = state.eventMarkers.get(eventId);
    if (action.fit) state.map.flyTo(existing.getLatLng(), Math.max(state.map.getZoom(), 13), { animate: true, duration: 0.75 });
    updateMapContentContext();
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
  updateMapContentContext();
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
  if (item.dataset.directiveDraftTitle) {
    item.insertAdjacentHTML("beforeend", `
      <button class="directive-chat-card" type="button" data-open-directive-editor>
        <i data-lucide="file-pen-line"></i>
        <span><strong>应急指令初稿已生成</strong><small>${escapeHtml(item.dataset.directiveDraftTitle)}</small></span>
        <em>打开初稿</em>
      </button>
    `);
    renderIcons();
  }
}

function attachDirectiveDraftCard(item, draft = {}) {
  item.dataset.directiveDraftTitle = String(draft.title || "未命名指令");
  setMessageMarkdown(item, item.dataset.rawMarkdown || "");
  scrollChat();
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
  const normalizedTag = String(tag || "").toUpperCase();
  const traceTime = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const detailHtml = detail ? renderTraceDetail(normalizedTag, String(detail)) : "";
  item.className = `trace-item ${traceTagClass(tag)}`;
  item.innerHTML = `
    <div class="trace-label">
      <span class="trace-title">${escapeHtml(label || "")}</span>
      <span class="trace-meta">
        <time class="trace-time">${escapeHtml(traceTime)}</time>
        <span class="trace-badges"><span class="trace-count" hidden></span><span class="trace-tag">${escapeHtml(tag)}</span></span>
      </span>
    </div>
    ${detailHtml}
  `;
  wrap.appendChild(item);
  const traceCount = document.getElementById("traceCount");
  if (traceCount) traceCount.textContent = String(wrap.childElementCount);
  state.lastTrace = { key, item, count: 1 };
  wrap.scrollTop = wrap.scrollHeight;

  return item;
}

function renderTraceDetail(tag, detail) {
  const rendered = `<div class="trace-detail markdown-body">${renderMarkdown(detail)}</div>`;
  const disclosureLabels = {
    CALL: "查看调用参数",
    RESULT: "查看返回结果",
    MAP: "查看地图动作",
  };
  const summary = disclosureLabels[tag];
  if (!summary) return rendered;
  return `<details class="trace-detail-disclosure"><summary>${summary}</summary>${rendered}</details>`;
}

function notifyAgentTrace(data, trace) {
  const error = data.tag === "ERR" || data.tag === "BLOCK";
  const needsInput = data.tag === "ASK";
  if (!error && !needsInput && data.label !== "智能体结论") return;
  const key = `${data.workspace_id || state.workspaceId || "manual"}:${data.notification_key || data.output_id || data.label}`;
  const previous = state.agentNotification;
  // Completed chains already dismissed or expired stay quiet on later stages.
  if (!error && !needsInput && state.notificationChains.has(key) && previous?.key !== key) return;
  state.notificationChains.add(key);
  if (previous?.sticky && !error && !needsInput) return;
  const title = error ? (data.label || "智能体处理失败") : needsInput ? "需要你的确认或补充" : "智能体研判已完成";
  showAgentNotification({ key, title, trace, sticky: error || needsInput, needsInput, error });
}

function showAgentNotification({ key, title, trace, sticky = false, needsInput = false, error = false }) {
  let item = state.agentNotification;
  if (item?.key === key) {
    item.trace = trace;
    item.sticky = item.sticky || sticky;
    item.needsInput = needsInput;
    item.element.querySelector(".agent-notification-title").textContent = title;
    item.element.querySelector(".agent-notification-view").textContent = needsInput ? "去处理" : error ? "查看原因" : "查看结论";
    item.element.classList.toggle("is-attention", item.sticky);
    pauseAgentNotification(item);
    item.remaining = 6000;
    resumeAgentNotification(item);
    return;
  }
  dismissAgentNotification();
  const element = document.createElement("article");
  element.className = `agent-notification${sticky ? " is-attention" : ""}`;
  element.innerHTML = `<span class="agent-notification-dot" aria-hidden="true"></span>
    <strong class="agent-notification-title"></strong>
    <button class="agent-notification-view" type="button">${needsInput ? "去处理" : error ? "查看原因" : "查看结论"}</button>
    <button class="agent-notification-close" type="button" aria-label="关闭通知">×</button>`;
  element.querySelector(".agent-notification-title").textContent = title;
  item = { key, trace, sticky, needsInput, element, remaining: 6000, timer: null, started: 0, hovered: false };
  state.agentNotification = item;
  element.querySelector(".agent-notification-close").onclick = dismissAgentNotification;
  element.querySelector(".agent-notification-view").onclick = () => {
    setAgentDrawerOpen(true);
    activateAgentPane(item.needsInput ? "chat" : "trace");
    const target = item.needsInput ? document.getElementById("chatLog").lastElementChild : item.trace;
    if (target?.isConnected) {
      target.tabIndex = -1;
      requestAnimationFrame(() => {
        target.scrollIntoView({ block: "center", behavior: "smooth" });
        target.focus({ preventScroll: true });
        target.classList.add("is-notification-target");
        window.setTimeout(() => target.classList.remove("is-notification-target"), 2200);
      });
    }
    dismissAgentNotification();
  };
  element.addEventListener("mouseenter", () => { item.hovered = true; pauseAgentNotification(item); });
  element.addEventListener("mouseleave", () => { item.hovered = false; resumeAgentNotification(item); });
  element.addEventListener("focusin", () => pauseAgentNotification(item));
  element.addEventListener("focusout", () => window.setTimeout(() => resumeAgentNotification(item), 0));
  document.getElementById("agentNotificationRegion").appendChild(element);
  resumeAgentNotification(item);
}

function pauseAgentNotification(item) {
  if (item.timer !== null) {
    clearTimeout(item.timer);
    item.remaining = Math.max(0, item.remaining - (performance.now() - item.started));
    item.timer = null;
  }
}

function resumeAgentNotification(item) {
  if (state.agentNotification !== item || item.sticky || item.hovered || item.element.contains(document.activeElement) || item.timer !== null) return;
  item.started = performance.now();
  item.timer = window.setTimeout(() => {
    if (state.agentNotification === item) dismissAgentNotification();
  }, item.remaining);
}

function dismissAgentNotification() {
  const item = state.agentNotification;
  if (!item) return;
  pauseAgentNotification(item);
  item.element.remove();
  state.agentNotification = null;
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
    const bounds = conclusionToastMapBounds();
    if (!bounds) return;
    const clampedX = clampPanelCoordinate(
      deltaX,
      bounds.left - drag.rect.left,
      bounds.right - drag.rect.right,
    );
    const clampedY = clampPanelCoordinate(
      deltaY,
      bounds.top - drag.rect.top,
      bounds.bottom - drag.rect.bottom,
    );
    item.dragX = drag.originX + clampedX;
    item.dragY = drag.originY + clampedY;
    toast.style.setProperty("--drag-x", `${item.dragX}px`);
    toast.style.setProperty("--drag-y", `${item.dragY}px`);
  });

  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", finish);
  handle.addEventListener("lostpointercapture", finish);
}

function conclusionToastMapBounds() {
  const mapRect = document.getElementById("map")?.getBoundingClientRect();
  const toolbarRect = document.querySelector(".map-toolbar")?.getBoundingClientRect();
  if (!mapRect?.width || !mapRect?.height) return null;
  return {
    left: mapRect.left + 8,
    right: mapRect.right - 8,
    top: Math.max(mapRect.top + 8, (toolbarRect?.bottom || mapRect.top) + 8),
    bottom: mapRect.bottom - 8,
  };
}

function clampConclusionToastsToMap() {
  const bounds = conclusionToastMapBounds();
  const region = document.getElementById("conclusionToastRegion");
  if (!bounds || !region) return;
  const availableHeight = Math.max(72, bounds.bottom - bounds.top - 108);
  region.style.setProperty("--conclusion-body-max-height", `${Math.min(360, availableHeight)}px`);
  const items = [];
  if (state.directiveToast?.element && !state.directiveToast.element.hidden) {
    items.push(state.directiveToast);
  }
  items.forEach((item) => {
    const rect = item.element?.getBoundingClientRect();
    if (!rect?.width || !rect?.height) return;
    const left = clampPanelCoordinate(rect.left, bounds.left, bounds.right - rect.width);
    const top = clampPanelCoordinate(rect.top, bounds.top, bounds.bottom - rect.height);
    item.dragX += left - rect.left;
    item.dragY += top - rect.top;
    item.element.style.setProperty("--drag-x", `${item.dragX}px`);
    item.element.style.setProperty("--drag-y", `${item.dragY}px`);
  });
}

function traceTagClass(tag) {
  const value = String(tag || "agent").toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
  return value ? `trace-${value}` : "trace-agent";
}

function shouldHideAutonomyTrace(data = {}) {
  return new Set(["CUT"]).has(data.tag);
}

function activateAgentPane(name) {
  const active = ["trace", "chat"].includes(name) ? name : "trace";
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
    workspace_id: state.workspaceId,
    map_time_context: { ...state.mapTimeContext },
    hydrodynamic_timeline: currentHydrodynamicTimelineContext(),
    visible_selections: visibleMapSelections(),
    visible_layers: Array.from(state.layerMeta.values(), meta => ({object_type: meta.objectType, label: meta.label, selection_id: meta.selectionId})),
    map_action_receipts: Array.from(state.mapActionReceipts.values()).slice(-8),
  };
}

function currentHydrodynamicTimelineContext() {
  const timeline = state.hydrodynamicTimeline;
  if (!timeline.layer || !timeline.key) {
    return {
      active: false,
      mode: "none",
      current_hydrodynamic_time_h: null,
      current_hydrodynamic_valid_at: null,
    };
  }
  if (!timeline.hours.length) {
    return {
      active: true,
      mode: "none",
      current_hydrodynamic_time_h: null,
      current_hydrodynamic_valid_at: null,
    };
  }
  const hour = Number(timeline.hours[timeline.index]);
  const envelope = timeline.mode === "envelope";
  return {
    active: true,
    mode: envelope ? "envelope" : "time_slice",
    forecast_id: timeline.forecastId,
    forecast_version: timeline.forecastVersion,
    forecast_time: timeline.forecastTime,
    valid_from: timeline.validFrom,
    valid_to: timeline.validTo,
    current_hydrodynamic_time_h: !envelope && Number.isFinite(hour)
      ? Number(formatHydrodynamicHour(hour))
      : null,
    current_hydrodynamic_valid_at: envelope
      ? null
      : timeline.validTimes[timeline.index] || null,
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
  state.impactRequested = true;
  if (!result || typeof result !== "object") return;
  if (!["completed", "partial", "no_forecast_cells"].includes(result.status)) return;
  const params = result.parameters || {};
  state.impactAnalysis = {
    forecastId: result.forecast_id || "latest",
    targetType: result.target_type || "all",
    objectIds: result.analysis_scope?.requested_object_ids ?? null,
    filters: result.analysis_scope?.filters || {},
    minDepthM: Number(params.min_depth_m ?? 0.15),
    maxDistanceM: Number(params.max_distance_m ?? 10),
    bridgeInfluenceRadiusM: Number(params.bridge_influence_radius_m ?? 80),
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
  } else if (timeline.mode === "envelope" && result.time_h != null) {
    scheduleImpactAnalysisRefresh();
    return;
  }
  renderImpactAnalysisResult(result);
}

function scheduleImpactAnalysisRefresh() {
  renderImpactReadiness();
  if (!state.impactRequested) return;
  if (!["time_slice", "envelope"].includes(currentHydrodynamicTimelineContext().mode)) return;
  if (state.hydrodynamicTimeline.playing) return;
  if (state.impactRefreshTimer) window.clearTimeout(state.impactRefreshTimer);
  state.impactRefreshController?.abort();
  state.impactRefreshController = null;
  state.impactRefreshSeq += 1;
  state.impactRefreshTimer = window.setTimeout(refreshImpactAnalysisForTimeline, 600);
}

async function refreshImpactAnalysisForTimeline() {
  state.impactRefreshTimer = null;
  const timeline = currentHydrodynamicTimelineContext();
  if (!["time_slice", "envelope"].includes(timeline.mode)) return;
  if (timeline.mode === "time_slice" && timeline.current_hydrodynamic_time_h == null) return;
  const forecastId = state.hydrodynamicTimeline.baseFilters?.forecast_id
    || state.hydrodynamicResultMeta?.forecast?.forecast_id
    || "latest";
  const params = new URLSearchParams({
    forecast_id: forecastId,
    target_type: state.impactAnalysis?.targetType || "all",
    min_depth_m: String(state.impactAnalysis?.minDepthM ?? 0.15),
    max_distance_m: String(state.impactAnalysis?.maxDistanceM ?? 10),
    bridge_influence_radius_m: String(
      state.impactAnalysis?.bridgeInfluenceRadiusM ?? 80,
    ),
  });
  if (state.impactAnalysis?.objectIds != null) params.set("object_ids", JSON.stringify(state.impactAnalysis.objectIds));
  if (state.impactAnalysis?.filters) params.set("filters", JSON.stringify(state.impactAnalysis.filters));
  if (timeline.mode === "time_slice") {
    params.set("time_h", String(timeline.current_hydrodynamic_time_h));
  }
  const seq = ++state.impactRefreshSeq;
  const controller = new AbortController();
  state.impactRefreshController?.abort();
  state.impactRefreshController = controller;
  renderImpactReadiness();
  setImpactAnalysisLoading(
    timeline.current_hydrodynamic_time_h,
    timeline.current_hydrodynamic_valid_at,
    timeline.mode,
  );
  try {
    const res = await fetch(`/api/impact-analysis?${params.toString()}`, {
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(await res.text());
    const result = await res.json();
    if (result.error) throw new Error(result.error);
    if (seq !== state.impactRefreshSeq) return;
    registerImpactAnalysisResult(result, { render: false });
    renderImpactAnalysisResult(result);
  } catch (error) {
    if (error?.name === "AbortError") return;
    if (seq !== state.impactRefreshSeq) return;
    setImpactAnalysisError(error);
    console.warn("impact analysis refresh failed", error);
  } finally {
    if (state.impactRefreshController === controller) {
      state.impactRefreshController = null;
      renderImpactReadiness();
    }
  }
}

function setImpactAnalysisLoading(hour, validAt = null, mode = "time_slice") {
  const panel = document.getElementById("impactPanel");
  panel?.classList.add("is-loading");
  panel?.setAttribute("aria-busy", "true");
  const count = document.getElementById("impactCount");
  const time = document.getElementById("impactTimeLabel");
  const status = document.getElementById("impactStatus");
  if (count) count.textContent = "--";
  setImpactScopeLabel(time, hour, validAt, mode === "envelope");
  if (status) {
    status.textContent = "分析中";
    status.title = mode === "envelope"
      ? "正在计算未来24小时最大包络的受影响对象"
      : "正在计算当前预测时刻的受影响对象";
  }
}

function setImpactAnalysisError(error) {
  document.getElementById("impactPanel")?.classList.remove("is-loading");
  document.getElementById("impactPanel")?.setAttribute("aria-busy", "false");
  const notice = document.getElementById("impactNotice");
  notice.hidden = false;
  notice.textContent = "本次分析失败，下方保留的是上一次结果。";
  const previous = state.impactAnalysis?.lastResult;
  if (previous) setImpactScopeLabel(document.getElementById("impactTimeLabel"), previous.time_h, previous.analysis_time_at, previous.time_h == null);
  const status = document.getElementById("impactStatus");
  if (status) {
    status.textContent = "分析失败";
    status.title = `影响分析失败：${String(error?.message || error)}`;
  }
}

function setImpactScopeLabel(element, hour, validAt, envelope = false) {
  if (!element) return;
  if (envelope) {
    element.textContent = "24h 最大包络";
    element.title = "分析范围：未来24小时最大淹没包络";
    return;
  }
  const offset = `+${formatHydrodynamicHour(hour)}h`;
  const actual = formatForecastActualTime(validAt, true);
  element.textContent = actual ? `预测时刻 · ${actual}` : `预测时刻 · ${offset}`;
  element.title = `分析范围：${formatHydrodynamicTimeLabel(hour, validAt)}`;
}

function renderImpactAnalysisResult(result) {
  state.impactRequested = true;
  const { impacts, nearby, routes: routeImpacts } = impactListData(result);
  const detailImpacts = [...impacts, ...nearby, ...routeImpacts,
    ...routeImpacts.flatMap((item) => [...(item.segment_impacts || []), ...(item.nearby_segment_impacts || [])])];
  const currentKeys = new Set(detailImpacts.map(impactObjectKey));
  if (state.selectedImpactKey && !currentKeys.has(state.selectedImpactKey)) {
    clearImpactObjectSelection({ removeLayer: true });
  }
  state.impactAnalysis = {
    ...(state.impactAnalysis || {}),
    lastResult: result,
  };
  renderImpactMarkers([...impacts, ...nearby]);
  renderImpactList(result);
  renderImpactReadiness();
  updateSelectedImpactDetails(detailImpacts);
}

function renderImpactMarkers(impacts) {
  state.impactMarkerLayer?.clearLayers();
  state.impactMarkers.clear();
  impacts.filter(impactHasLocation).forEach((impact) => {
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
  updateMapContentContext();
}

function impactMarkerIcon(impact, selected = false) {
  const riskLevel = ["critical", "high", "medium", "low"].includes(impact.risk_level)
    ? impact.risk_level
    : "unknown";
  return L.divIcon({
    className: `impact-point-marker is-${riskLevel}${impact.impact_status === "nearby_flood" ? " is-nearby" : ""}${selected ? " is-selected" : ""}`,
    html: '<span class="impact-point-core" aria-hidden="true"></span>',
    iconSize: [16, 16],
    iconAnchor: [8, 8],
    tooltipAnchor: [0, -8],
  });
}

function impactTooltipHtml(impact) {
  const depthLabel = impactDepthLabel(impact);
  const status = impactStatusLabel(impact) || impactPassabilityLabel(impact.passability_status);
  return `<strong>${escapeHtml(impact.name || impact.object_id)}</strong><br>${escapeHtml(impactTypeLabel(impact.object_type))} · ${escapeHtml(depthLabel)} ${formatImpactNumber(impact.depth_m, 2)} m${status ? `<br>${escapeHtml(status)}` : ""}`;
}

function impactHasLocation(impact) {
  return impact.longitude != null && impact.latitude != null
    && Number.isFinite(Number(impact.longitude)) && Number.isFinite(Number(impact.latitude));
}

function uniqueImpactObjects(items) {
  return [...new Map(items.filter((item) => item?.object_type && item.object_id != null)
    .map((item) => [impactObjectKey(item), item])).values()];
}

function impactListData(result) {
  const routes = uniqueImpactObjects([
    ...(result?.road_route_impacts || []), ...(result?.road_route_nearby_impacts || []),
    ...(result?.impacts || []).filter((item) => item.object_type === "RoadRoute"),
    ...(result?.nearby_impacts || []).filter((item) => item.object_type === "RoadRoute"),
  ]);
  // Count leaf objects once, even for a RoadRoute-only result or shared road segments.
  const impacts = uniqueImpactObjects([
    ...(result?.impacts || []).filter((item) => item.object_type !== "RoadRoute"),
    ...routes.flatMap((route) => route.segment_impacts || []),
  ]);
  const affectedKeys = new Set(impacts.map(impactObjectKey));
  const nearby = uniqueImpactObjects([
    ...(result?.nearby_impacts || []).filter((item) => item.object_type !== "RoadRoute"),
    ...routes.flatMap((route) => route.nearby_segment_impacts || []),
  ]).filter((item) => !affectedKeys.has(impactObjectKey(item)));
  return { impacts, nearby, routes };
}

function impactGroupLabel(type) {
  return type === "Road" ? "道路" : impactTypeLabel(type);
}

function bindImpactListControls() {
  const tabs = [...document.querySelectorAll("[data-impact-tab]")];
  tabs.forEach((button, index) => {
    button.addEventListener("click", () => {
      state.impactListView.tab = button.dataset.impactTab;
      refreshImpactListView();
    });
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      tabs[next].focus();
      tabs[next].click();
    });
  });
  document.getElementById("impactTypeFilter").addEventListener("change", (event) => {
    state.impactListView.type = event.target.value;
    refreshImpactListView();
  });
}

function refreshImpactListView() {
  const result = state.impactAnalysis?.lastResult;
  if (result) renderImpactList(result);
  else updateImpactListControls({ impacts: [], nearby: [] });
}

function updateImpactListControls(data, noData = false) {
  const view = state.impactListView;
  document.querySelectorAll("[data-impact-tab]").forEach((button) => {
    const active = button.dataset.impactTab === view.tab;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  document.getElementById("impactCount").textContent = noData ? "--" : data.impacts.length;
  document.getElementById("impactNearbyCount").textContent = noData ? "--" : data.nearby.length;
  document.getElementById("impactList").setAttribute("aria-labelledby", view.tab === "nearby" ? "impactNearbyTab" : "impactAffectedTab");
  const rows = view.tab === "nearby" ? data.nearby : data.impacts;
  const types = [...new Set([...data.impacts, ...data.nearby].map((item) => item.object_type))];
  if (view.type !== "all" && !types.includes(view.type)) types.push(view.type);
  types.sort((a, b) => (a === "Road" ? -1 : b === "Road" ? 1 : impactGroupLabel(a).localeCompare(impactGroupLabel(b), "zh-CN")));
  const select = document.getElementById("impactTypeFilter");
  select.replaceChildren(new Option("全部类型", "all"), ...types.map((type) => (
    new Option(`${impactGroupLabel(type)} (${rows.filter((item) => item.object_type === type).length})`, type)
  )));
  select.value = view.type;
}

function rememberImpactListPosition(list) {
  const view = state.impactListView;
  if (!view.renderedKey || !list.clientHeight) return;
  const top = list.getBoundingClientRect().top;
  const anchor = [...list.querySelectorAll("[data-impact-row]")].find((row) => (
    row.getClientRects().length && row.getBoundingClientRect().bottom > top
  ));
  // Retain the last position when a time slice temporarily has no matching objects.
  if (!anchor) return;
  view.positions.set(view.renderedKey, {
    top: list.scrollTop,
    anchor: anchor.dataset.impactRow,
    offset: anchor.getBoundingClientRect().top - top,
  });
}

function restoreImpactListPosition(list) {
  const view = state.impactListView;
  view.renderedKey = `${view.tab}:${view.type}`;
  const position = view.positions.get(view.renderedKey);
  list.scrollTop = position?.top || 0;
  if (!position?.anchor) return;
  const anchor = [...list.querySelectorAll("[data-impact-row]")].find((row) => row.dataset.impactRow === position.anchor);
  if (anchor?.getClientRects().length) {
    list.scrollTop += anchor.getBoundingClientRect().top - list.getBoundingClientRect().top - position.offset;
  }
}

function renderImpactList(result) {
  const panel = document.getElementById("impactPanel");
  const count = document.getElementById("impactCount");
  const time = document.getElementById("impactTimeLabel");
  const status = document.getElementById("impactStatus");
  const list = document.getElementById("impactList");
  const notice = document.getElementById("impactNotice");
  const data = impactListData(result);
  const { impacts, nearby } = data;
  const view = state.impactListView;
  rememberImpactListPosition(list);
  const focusedControl = list.contains(document.activeElement) ? document.activeElement.dataset.impactControl : null;
  panel.classList.remove("is-loading");
  panel.setAttribute("aria-busy", "false");
  count.title = "预测影响对象数；道路按唯一路段统计，编号道路汇总不重复计数";
  setImpactScopeLabel(time, result?.time_h, result?.analysis_time_at, result?.time_h == null);
  status.textContent = impacts.length ? "已分析" : "未发现";
  status.title = "仅表示预测影响；道路通行状态需另行判定";
  const noData = result?.status === "no_forecast_cells";
  notice.hidden = true;
  if (noData) {
    count.textContent = "--";
    status.textContent = "无预测数据";
    status.title = "缺少对应预测数据，无法判定受影响对象";
  } else if (result?.status === "partial") {
    status.textContent = "部分未评估";
    const unassessed = result.linear_analysis?.unassessed_objects?.length || 0;
    const skipped = result.linear_analysis?.skipped_cell_ids?.length || 0;
    notice.textContent = [unassessed ? `${unassessed} 个对象未评估` : "", skipped ? `${skipped} 个网格几何无效` : ""].filter(Boolean).join("；") || "部分几何缺失或无效";
    notice.textContent += "，不能据此判断安全。";
    notice.hidden = false;
  } else if (!impacts.length && nearby.length) {
    status.textContent = "需核查";
    status.title = `未发现预测影响，另有 ${nearby.length} 个对象邻近积水`;
  }
  updateImpactListControls(data, noData);
  const rows = (view.tab === "nearby" ? nearby : impacts).filter((item) => view.type === "all" || item.object_type === view.type);
  list.replaceChildren();
  if (!rows.length || noData) {
    const empty = document.createElement("p");
    empty.className = "impact-empty";
    empty.textContent = noData ? "当前范围无可用预测网格，无法判断影响。"
      : view.type !== "all" ? `当前${view.tab === "nearby" ? "邻近积水" : "预测影响"}结果中没有${impactGroupLabel(view.type)}。可切换对象类型查看。`
      : view.tab === "nearby" ? "当前分析范围内未发现仅邻近积水的对象。"
      : "当前分析范围内未发现预测影响对象。";
    list.appendChild(empty);
  } else {
    const groups = new Map();
    rows.forEach((item) => {
      if (!groups.has(item.object_type)) groups.set(item.object_type, []);
      groups.get(item.object_type).push(item);
    });
    // Keep categories stable as depths change. Within a category show risk, then depth.
    const order = ["Road", "Bridge", "EvacuationRoute", "EvacuationUnit", "EvacuationSite", "Facility", "Station"];
    [...groups].sort(([a], [b]) => (order.indexOf(a) < 0 ? 99 : order.indexOf(a)) - (order.indexOf(b) < 0 ? 99 : order.indexOf(b)))
      .forEach(([type, items]) => {
        const section = document.createElement("details");
        section.className = "impact-group";
        section.dataset.impactGroup = type;
        const expansionKey = `${view.tab}:group:${type}`;
        section.open = view.expanded.get(expansionKey) ?? (type === "Road" || view.type !== "all" || groups.size === 1);
        const heading = document.createElement("summary");
        heading.dataset.impactRow = heading.dataset.impactControl = `group:${type}`;
        heading.innerHTML = `<span>${escapeHtml(impactGroupLabel(type))}</span><span class="impact-group-count">${items.length} ${type === "Road" ? "段" : "个"}</span>`;
        section.appendChild(heading);
        section.addEventListener("toggle", () => {
          if (section.isConnected) view.expanded.set(expansionKey, section.open);
        });
        if (type === "Road") renderImpactRoads(section, items, data.routes);
        else sortImpactListItems(items).forEach((item) => section.appendChild(impactListItem(item)));
        list.appendChild(section);
      });
  }
  document.getElementById("impactListSummary").textContent = noData ? "无预测数据不代表无影响"
    : view.tab === "nearby" ? `${rows.length} 个待核查 · 不计入预测影响数`
    : `显示 ${rows.length} / ${impacts.length} 个对象 · 道路按唯一路段计数`;
  restoreImpactListPosition(list);
  if (focusedControl) {
    [...list.querySelectorAll("[data-impact-control]")].find((item) => item.dataset.impactControl === focusedControl)?.focus({ preventScroll: true });
  }
  if (panel.classList.contains("is-floating")) window.requestAnimationFrame(() => clampMapPanelToStage(panel));
}

function sortImpactListItems(items) {
  const rank = { critical: 4, high: 3, medium: 2, low: 1 };
  return [...items].sort((a, b) => (rank[b.risk_level] || 0) - (rank[a.risk_level] || 0)
    || Number(b.depth_m || 0) - Number(a.depth_m || 0)
    || String(a.object_id).localeCompare(String(b.object_id)));
}

function impactRouteName(route) {
  return [...new Set([route.ref, route.name].map((value) => String(value || "").trim()).filter(Boolean))].join(" · ") || String(route.object_id);
}

function impactRouteForTab(route, tab) {
  if (tab !== "nearby" || !route.nearby_segment_impacts?.length) return route;
  const members = route.nearby_segment_impacts;
  const deepest = members.reduce((a, b) => Number(a.depth_m) >= Number(b.depth_m) ? a : b);
  return {
    ...route,
    ...Object.fromEntries(["depth_m", "velocity_mps", "longitude", "latitude", "mesh_cell_id", "forecast_cell_id", "risk_level"].map((key) => [key, deepest[key]])),
    impact_status: "nearby_flood",
    depth_basis: "nearby_forecast_cells",
    passability_status: "inspection_required",
    structure_unverified_segment_count: 0,
    nearest_distance_m: Math.min(...members.map((item) => Number(item.nearest_distance_m ?? item.distance_m))),
    distance_m: Math.min(...members.map((item) => Number(item.nearest_distance_m ?? item.distance_m))),
  };
}

function renderImpactRoads(container, roads, routes) {
  const view = state.impactListView;
  const memberField = view.tab === "nearby" ? "nearby_segment_impacts" : "segment_impacts";
  const byKey = new Map(roads.map((item) => [impactObjectKey(item), item]));
  const grouped = new Set();
  const memberships = new Map();
  const groups = routes.map((route) => ({
    route,
    members: uniqueImpactObjects(route[memberField] || []).map((item) => byKey.get(impactObjectKey(item))).filter(Boolean),
  })).filter(({ members }) => members.length);
  groups.forEach(({ members }) => members.forEach((item) => memberships.set(impactObjectKey(item), (memberships.get(impactObjectKey(item)) || 0) + 1)));
  groups.forEach(({ route, members }) => {
    const key = impactObjectKey(route);
    const name = impactRouteName(route);
    const expansionKey = `${view.tab}:route:${key}`;
    const section = document.createElement("div");
    section.className = "impact-road-route";
    const head = document.createElement("div");
    head.className = "impact-route-header";
    head.dataset.impactRow = `route:${key}`;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "impact-route-toggle";
    toggle.dataset.impactControl = `toggle:${key}`;
    toggle.innerHTML = '<span aria-hidden="true">›</span>';
    const children = document.createElement("div");
    children.className = "impact-route-members";
    children.id = `impact-members-${encodeURIComponent(String(route.object_id))}`;
    const setOpen = (open) => {
      children.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", `${open ? "收起" : "展开"}${name}的路段`);
    };
    toggle.setAttribute("aria-controls", children.id);
    setOpen(view.expanded.get(expansionKey) ?? false);
    toggle.addEventListener("click", () => {
      const open = children.hidden;
      view.expanded.set(expansionKey, open);
      setOpen(open);
    });
    const routeView = impactRouteForTab(route, view.tab);
    const button = impactListItem(routeView, { title: name });
    button.classList.add("impact-route-focus");
    button.querySelector(".impact-list-evidence").textContent = `已收录 ${route.recorded_segment_count} 段 · ${members.length} 段${view.tab === "nearby" ? "邻近积水" : "相交"}`;
    head.append(toggle, button);
    members.forEach((item) => {
      grouped.add(impactObjectKey(item));
      children.appendChild(impactListItem(item, {
        title: item.name && ![route.name, route.ref].includes(item.name) ? `${item.name} · 路段 ${item.object_id}` : `路段 ${item.object_id}`,
        parentKey: key,
        shared: memberships.get(impactObjectKey(item)) > 1,
      }));
    });
    section.append(head, children);
    container.appendChild(section);
  });
  const ungrouped = roads.filter((item) => !grouped.has(impactObjectKey(item)));
  if (ungrouped.length) {
    const label = document.createElement("div");
    label.className = "impact-ungrouped-label";
    label.textContent = `未归属编号道路 · ${ungrouped.length} 段`;
    container.appendChild(label);
    sortImpactListItems(ungrouped).forEach((item) => container.appendChild(impactListItem(item)));
  }
  const note = document.createElement("p");
  note.className = "impact-road-note";
  note.textContent = `仅统计已收录路段${[...memberships.values()].some((count) => count > 1) ? "；共线路段在各道路下列出，总数去重" : "；道路汇总不重复计数"}`;
  container.appendChild(note);
}

function impactListItem(impact, options = {}) {
  const key = impactObjectKey(impact);
  const isNearby = impact.impact_status === "nearby_flood";
  const linear = ["Road", "EvacuationRoute"].includes(impact.object_type);
  const needsElevation = impact.impact_status === "structure_overlap_unverified" || impact.data_quality === "insufficient_bridge_elevation" || impact.structure_unverified_segment_count > 0;
  const iconInfo = objectIconInfo(impact.object_type, impact);
  const symbol = window.FloodMapSymbols?.render(iconInfo.icon) || "";
  const button = document.createElement("button");
  button.type = "button";
  button.className = `impact-list-item${isNearby ? " is-nearby" : ""}`;
  button.classList.toggle("is-selected", key === state.selectedImpactKey);
  button.dataset.impactKey = key;
  button.dataset.impactRow = button.dataset.impactControl = `${options.parentKey || "object"}/${key}`;
  let evidence = isNearby ? `邻近网格最大水深 ${formatImpactNumber(impact.depth_m, 3)} m`
    : linear ? `相交 ${formatImpactNumber(impact.overlap_length_m, 1)} m · ${impact.intersecting_mesh_cell_ids?.length || 0} 个网格`
    : impact.object_type === "Bridge" ? (impact.impact_status === "bridge_approach_inundated" ? "桥头影响区积水" : "桥梁影响区积水")
    : `预测网格 ${impact.mesh_cell_id || "--"}`;
  const title = options.title || impact.name || `${impactTypeLabel(impact.object_type)} ${impact.object_id}`;
  const value = isNearby ? formatImpactNumber(impact.nearest_distance_m ?? impact.distance_m, 1) : formatImpactNumber(impact.depth_m, 3);
  const metric = isNearby ? "距积水边界" : linear || impact.object_type === "RoadRoute" ? "相交网格最大水深" : impact.object_type === "Bridge" ? "洪泛区最大水深" : "预测网格水深";
  button.title = `${title} · ${evidence} · ${metric} ${value} m · ID ${impact.object_id}`;
  button.innerHTML = `
    <span class="impact-list-symbol object-symbol-${escapeHtml(iconInfo.key)}">
      <span class="impact-object-icon" aria-hidden="true">${symbol}</span>
      <span class="impact-risk-dot is-${escapeHtml(impact.risk_level || "unknown")}" role="img" aria-label="${escapeHtml(impactRiskLabel(impact.risk_level))}"></span>
    </span>
    <span class="impact-list-copy">
      <strong>${escapeHtml(title)}</strong>
      <small class="impact-list-evidence">${escapeHtml(evidence)}</small>
      ${needsElevation || isNearby || options.shared ? `<span class="impact-item-badges">${needsElevation ? '<span>高程待核查</span>' : ""}${isNearby ? '<span>需核查</span>' : ""}${options.shared ? '<span class="is-shared">共线路段</span>' : ""}</span>` : ""}
    </span>
    <span class="impact-list-depth"><b>${value}<small>m</small></b><span>${metric}</span></span>`;
  button.addEventListener("click", () => void focusImpactObject(impact));
  return button;
}

async function focusImpactObject(impact) {
  const objectType = impact?.object_type;
  const objectId = String(impact?.object_id || "");
  if (!objectType || !objectId) return;
  const key = impactObjectKey(impact);
  const focusSeq = ++state.impactFocusSeq;
  if (state.selectedImpactLayerKey && state.selectedImpactKey !== key) {
    state.selectedImpactLayerKey = null;
  }
  state.selectedImpactKey = key;
  state.selectedImpactTab = impact.impact_status === "nearby_flood" ? "nearby" : "affected";
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
  if (objectType === "RoadRoute") {
    clearFocus();
    state.selected = { object_type: objectType, id: objectId, name: impactRouteName(impact) };
  } else {
    selectFeature(objectType, entry.feature, entry.layer);
  }
  setLayerPanelOpen(true);
  entry.layer.setPopupContent?.(impactPopupHtml(impact));
  entry.layer.openPopup?.(impactHasLocation(impact) ? [Number(impact.latitude), Number(impact.longitude)] : undefined);
  document.getElementById("selectedObject").innerHTML = impactDetailHtml(impact, entry.feature?.properties || {});
  if (objectType === "RoadRoute") {
    await updateImpactRoadFocus(impact, { fit: true });
  } else if (["Road", "EvacuationRoute"].includes(objectType) && impactHasLocation(impact)) {
    state.map.flyTo([Number(impact.latitude), Number(impact.longitude)], Math.max(state.map.getZoom(), 16), { duration: 0.65 });
  } else {
    fitFeatureLayer(entry.layer);
  }
}

function impactRouteFocusMembers(impact) {
  return uniqueImpactObjects(impact.impact_status === "nearby_flood"
    ? impact.nearby_segment_impacts || []
    : impact.segment_impacts || []);
}

function clearImpactRoadFocus() {
  state.impactRoadFocusSeq += 1;
  state.impactRoadFocusLayer?.remove();
  state.impactRoadFocusLayer = null;
}

async function updateImpactRoadFocus(impact, { fit = false } = {}) {
  clearImpactRoadFocus();
  const seq = state.impactRoadFocusSeq;
  const key = impactObjectKey(impact);
  const members = impactRouteFocusMembers(impact);
  if (!members.length) return;
  const features = [];
  const missingIds = [];
  members.forEach((member) => {
    const feature = state.featureIndex.get(featureIndexKey("Road", member.object_id))?.feature;
    if (feature?.geometry) features.push(feature);
    else missingIds.push(String(member.object_id));
  });
  if (missingIds.length) {
    try {
      const params = new URLSearchParams({
        object_type: "Road",
        filters: JSON.stringify(filtersWithObjectIds("Road", {}, missingIds)),
      });
      const response = await fetch(`/api/geojson?${params.toString()}`);
      if (!response.ok) throw new Error(await response.text());
      const geojson = await response.json();
      const requested = new Set(missingIds);
      features.push(...(geojson.features || []).filter((feature) => requested.has(String(feature.properties?.road_id))));
    } catch (error) {
      if (seq === state.impactRoadFocusSeq) addTrace("MISS", "路段几何加载失败，将按影响位置定位", String(error?.message || error));
    }
  }
  // A later selection or timeline result must win over an earlier geometry request.
  if (seq !== state.impactRoadFocusSeq || key !== state.selectedImpactKey || key !== impactObjectKey(state.selected)) return;
  if (features.length) {
    state.impactRoadFocusLayer = L.geoJSON({ type: "FeatureCollection", features }, {
      interactive: false,
      pane: "impactPane",
      style: { color: "#f59e0b", weight: 6, opacity: 1, lineCap: "round", lineJoin: "round" },
    }).addTo(state.map);
  }
  if (!fit) return;
  if (members.length === 1 && impactHasLocation(members[0])) {
    const member = members[0];
    state.map.flyTo([Number(member.latitude), Number(member.longitude)], Math.max(state.map.getZoom(), 16), { duration: 0.65 });
    return;
  }
  const bounds = state.impactRoadFocusLayer?.getBounds() || L.latLngBounds([]);
  members.filter(impactHasLocation).forEach((member) => bounds.extend([Number(member.latitude), Number(member.longitude)]));
  if (bounds.isValid()) {
    state.map.flyToBounds(bounds.pad(0.15), {
      animate: true,
      duration: 0.65,
      maxZoom: 16,
      padding: [32, 32],
    });
  }
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
  let impact = impacts.find((item) => impactObjectKey(item) === state.selectedImpactKey);
  if (!impact) return;
  if (impact.object_type === "RoadRoute") impact = impactRouteForTab(impact, state.selectedImpactTab);
  const entry = state.featureIndex.get(featureIndexKey(impact.object_type, impact.object_id));
  if (!entry) return;
  entry.layer.setPopupContent?.(impactPopupHtml(impact));
  document.getElementById("selectedObject").innerHTML = impactDetailHtml(impact, entry.feature?.properties || {});
  if (impact.object_type === "RoadRoute" && impactObjectKey(state.selected) === state.selectedImpactKey) {
    void updateImpactRoadFocus(impact);
  }
}

function clearImpactObjectSelection(options = {}) {
  clearImpactRoadFocus();
  const selectedKey = state.selectedImpactKey;
  state.selectedImpactKey = null;
  state.impactFocusSeq += 1;
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
  if (objectType === "Road") return "道路路段";
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

function impactStatusLabel(impact) {
  return {
    forecast_overlap: "与预测湿网格相交",
    nearby_flood: "邻近积水，需核查",
    structure_overlap_unverified: "桥上/隧道路段相交，路面高程待核查",
    bridge_approach_inundated: "桥头影响区积水，桥面高程待核查",
    bridge_influence_zone: "桥梁影响区积水，桥面高程待核查",
  }[impact?.impact_status] || "";
}

function impactDepthLabel(impact) {
  if (impact?.depth_basis === "nearby_forecast_cells") return "邻近网格最大水深";
  if (impact?.depth_basis === "intersecting_forecast_cells") return "相交网格最大水深";
  if (impact?.object_type === "RoadRoute") return "受影响路段对应网格最大水深";
  return impact?.object_type === "Bridge" && impact?.directly_inundated === false
    ? "邻近洪泛区最大水深"
    : "水深";
}

function impactPassabilityLabel(status) {
  return {
    inspection_required: "需现场核查",
    likely_impassable: "可能无法通行",
    not_assessed: "整体通行状态未判定",
  }[status] || "";
}

function linearImpactEvidenceHtml(impact) {
  if (!["Road", "EvacuationRoute"].includes(impact.object_type) || !impact.impact_status) return "";
  if (impact.impact_status === "nearby_flood") {
    return `<div class="popup-meta">完整线形未与达标湿网格相交，最近距网格边界 ${formatImpactNumber(impact.nearest_distance_m, 1)} m；不能据此认定路面已淹。</div>`;
  }
  const ids = impact.intersecting_mesh_cell_ids || [];
  return `<div class="popup-meta">相交网格 ${ids.length} 个：${escapeHtml(ids.slice(0, 10).join("、"))}${ids.length > 10 ? "…" : ""}<br>
    相交线形累计 ${formatImpactNumber(impact.overlap_length_m, 1)} m；水深为模型网格值，通行状态需另行判定。</div>`;
}

function impactPopupHtml(impact) {
  const depthLabel = impactDepthLabel(impact);
  const passability = impactStatusLabel(impact) || impactPassabilityLabel(impact.passability_status);
  return `
    <div class="popup-title">${escapeHtml(impact.name || impact.object_id)}</div>
    <div class="popup-meta">${escapeHtml(impactTypeLabel(impact.object_type))} ${escapeHtml(impact.object_id)}</div>
    <div class="popup-depth">${formatImpactNumber(impact.depth_m, 2)} <span>m ${escapeHtml(depthLabel)}</span></div>
    ${linearImpactEvidenceHtml(impact)}
    <div class="popup-meta">${escapeHtml(passability || impactRiskLabel(impact.risk_level))} · 流速 ${formatImpactNumber(impact.velocity_mps, 2)} m/s · 距网格 ${formatImpactNumber(impact.distance_m, 1)} m</div>
  `;
}

function impactDetailHtml(impact, props) {
  const depthLabel = impactDepthLabel(impact);
  const passability = impactStatusLabel(impact) || impactPassabilityLabel(impact.passability_status);
  return `
    <div class="impact-selected-summary">
      <strong>${escapeHtml(passability || impactRiskLabel(impact.risk_level))}</strong>
      <span>${escapeHtml(depthLabel)} ${formatImpactNumber(impact.depth_m, 2)} m</span>
      <span>流速 ${formatImpactNumber(impact.velocity_mps, 2)} m/s</span>
    </div>
    ${linearImpactEvidenceHtml(impact)}
    ${impact.object_type === "RoadRoute" ? `<div class="muted">已收录 ${impact.recorded_segment_count} 段中 ${impact.affected_segment_count} 段受影响（与湿网格相交），另有 ${impact.nearby_segment_count || 0} 段仅邻近积水；不代表整条道路不可通行。</div>
      <details open><summary>受影响路段（点击查看详情）</summary>${(impact.segment_impacts || []).map((segment) => roadObjectButton("Road", segment.object_id, `${segment.object_id} · ${formatImpactNumber(segment.depth_m, 2)} m`)).join("")}</details>` : ""}
    ${impact.nearby_segment_impacts?.length ? `<details open><summary>仅邻近积水的路段（需核查）</summary>${impact.nearby_segment_impacts.map((segment) => roadObjectButton("Road", segment.object_id, `${segment.object_id} · 距网格 ${formatImpactNumber(segment.nearest_distance_m, 1)} m`)).join("")}</details>` : ""}
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
    assess_flood_emergency: "单次应急研判",
    analyze_inundation_impacts: "分析淹没影响",
    ui_show_objects: "地图显示",
    ui_show_event_marker: "地图标记事件",
    ui_hide_objects: "隐藏指定对象",
    ui_hide_forecast: "隐藏淹没结果",
    ui_reset_map: "重置整个地图",
    find_nearby_objects: "查询附近对象",
    get_flood_status: "查询淹没状态",
    plan_route: "规划路线",
    refine_object_set: "筛选候选集合",
    compare_evacuation_sites: "比较安置方案",
    review_route: "复核既有路线",
    ui_focus_object: "地图定位",
    ui_open_emergency_directive_editor: "生成应急指令初稿",
    ui_set_inundation_alert: "设置流域淹没警戒",
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
  settlePendingQuestion();
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
  const item = addMessage("agent", data.question || "需要补充信息。");
  state.pendingQuestion = { ...data, element: item };
  const options = Array.isArray(data.options) ? data.options : [];
  if (options.length) {
    const choices = document.createElement("div");
    choices.className = "question-options";
    options.forEach((option) => {
      const label = String(option?.label || "").trim();
      if (!label) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "question-option";
      const title = document.createElement("strong");
      title.textContent = label;
      button.appendChild(title);
      if (option?.description) {
        const description = document.createElement("small");
        description.textContent = String(option.description);
        button.appendChild(description);
      }
      button.addEventListener("click", () => submitQuestionAnswer(label));
      choices.appendChild(button);
    });
    item.appendChild(choices);
  }
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "question-cancel";
  cancel.textContent = "取消这个问题";
  cancel.addEventListener("click", () => {
    settlePendingQuestion();
    runConfirm(false);
  });
  item.appendChild(cancel);
  scrollChat();
}

function submitQuestionAnswer(answer) {
  if (!answer || state.activeStream || !state.pendingQuestion) return;
  const input = document.getElementById("chatInput");
  input.value = answer;
  input.form?.requestSubmit();
}

function settlePendingQuestion() {
  if (state.agentNotification?.needsInput) dismissAgentNotification();
  const item = state.pendingQuestion?.element;
  item?.querySelectorAll(".question-option, .question-cancel").forEach((button) => {
    button.disabled = true;
  });
  state.pendingQuestion = null;
}

async function runConfirm(approved) {
  if (state.agentNotification?.needsInput) dismissAgentNotification();
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
    btn.setAttribute("aria-pressed", String(active));
  });
}

function syncFilteredLayerButtons() {
  document.querySelectorAll("[data-facility]").forEach((btn) => {
    const type = btn.dataset.facility;
    const active = Array.from(state.layerMeta.values()).some((meta) => (
      meta.objectType === "Facility" && meta.filters?.facility_type === type
    ));
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", String(active));
  });
  document.querySelectorAll("[data-station]").forEach((btn) => {
    const type = btn.dataset.station;
    const active = filteredLayerKeys("Station", "station_type", type).length > 0;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", String(active));
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

const BASIN_RAIN_FIELDS = [
  ["interval1_rainfall_mm", "区间 1", "381 km²", "#1f7a5c"],
  ["interval2_rainfall_mm", "区间 2", "85 km²", "#2878b9"],
  ["reservoir_rainfall_mm", "水库上游", "36 km²", "#a15f13"],
];

function setBasinTab(tab) {
  state.basinTab = tab;
  document.querySelectorAll("[data-basin-tab]").forEach((button) => {
    const active = button.dataset.basinTab === tab;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    document.getElementById(button.getAttribute("aria-controls")).hidden = !active;
  });
}

function basinNumber(value, digits = 2) {
  const number = finiteTelemetryNumber(value);
  return number === null ? "--" : number.toFixed(digits);
}

function basinPeak(points, field) {
  const valid = points.filter((row) => finiteTelemetryNumber(row?.[field]) !== null);
  return valid.length ? valid.reduce((best, row) => Number(row[field]) > Number(best[field]) ? row : best) : null;
}

function basinTable(headers, rows) {
  return `<table class="basin-table"><thead><tr>${headers.map(h => `<th scope="col">${h}</th>`).join("")}</tr></thead><tbody>${rows.map(cells => `<tr>${cells.map((cell, index) => index === 0 ? `<th scope="row">${cell}</th>` : `<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function renderBasinWorkbench(observation) {
  const future = observation?.rainfall_forecast || [];
  const hours = future.length;
  const period = !observation || hours === 24 ? "未来 24h" : `剩余 ${hours}h`;
  const rainRows = BASIN_RAIN_FIELDS.map(([field, label, area, color]) => {
    const values = future.map(row => finiteTelemetryNumber(row[field]));
    const complete = values.length && values.every(value => value !== null);
    const peak = basinPeak(future, field);
    return [`<span class="basin-dot" style="background:${color}"></span>${label} <small>${area}</small>`,
      `<strong>${basinNumber(observation?.[field])}</strong>`,
      complete ? values.reduce((a, b) => a + b, 0).toFixed(2) : "--",
      `${basinNumber(peak?.[field])}<small>${peak ? escapeHtml(formatRainfallChartTime(peak.valid_time)) : "--"}</small>`];
  });
  document.getElementById("basinRainTable").innerHTML = basinTable(
    ["分区 / 控制面积", "当前雨量", `${period}累计`, "最大时段 / 时刻"], rainRows);
  document.getElementById("basinRainNote").textContent = observation
    ? `面雨量输入 · mm / 时段；累计不含当前时段。${hours < 24 ? "输入尾段不足 24 小时。" : ""}测站雨量在 marker 中查看。`
    : "等待降雨输入；三个分区分别驱动产流。";
  renderBasinRainChart(observation);
  const flows = observation?.boundary_flow_forecast?.series || [];
  const currentTime = observation?.simulation_time || observation?.observed_at;
  const flowRows = BOUNDARY_FLOW_KEYS.map(key => {
    const current = observation?.boundaries?.[key]?.flow_m3s;
    const points = observation ? [{ valid_time: currentTime, value: current }, ...flows.map(row => ({ valid_time: row.valid_time, value: row.boundaries?.[key]?.flow_m3s }))] : [];
    const peak = basinPeak(points, "value");
    const source = key === "tonggu" ? "区间2 × 0.946" : key === "upstream" ? "水库计算泄流" : "分区面雨量产流";
    return [`<span class="basin-dot" style="background:${BOUNDARY_FLOW_COLORS[key]}"></span>${BOUNDARY_FLOW_LABELS[key]}<small>${source}</small>`, `<strong>${basinNumber(current)}</strong>`, basinNumber(peak?.value), peak ? escapeHtml(formatRainfallChartTime(peak.valid_time)) : "--"];
  });
  document.getElementById("basinFlowTable").innerHTML = basinTable(["CNN 边界 / 来源", "当前流量", "窗口峰值", "峰值时刻"], flowRows);
  const threshold = observation?.boundary_flow_forecast?.first_threshold_exceeded_at;
  document.getElementById("basinFlowNote").textContent = observation
    ? `派生流量 · m³/s · ${flows.length}h窗口含当前点；四边界合计首次超 230：${threshold ? formatRainfallChartTime(threshold) : "未超过"}`
    : "等待产流与水库调度计算。";
  const decision = observation?.reservoir_dispatch;
  const label = DISPATCH_STATES[decision?.state]?.label || "等待调度数据";
  const reservoirFuture = observation?.reservoir_forecast?.series || [];
  const reservoirWindow = observation ? [observation, ...reservoirFuture] : [];
  const dispatchRows = [["reservoir_inflow_m3s", "入库流量 · m³/s"], ["reservoir_release_m3s", "计算泄流 · m³/s"], ["reservoir_level_m", "时段末水位 · m"]].map(([field, name]) => {
    const peak = basinPeak(reservoirWindow, field);
    return [name, `<strong>${basinNumber(observation?.[field])}</strong>`, basinNumber(peak?.[field])];
  });
  document.getElementById("basinDispatchSummary").innerHTML = `<div class="basin-dispatch-state"><strong>${escapeHtml(label)}</strong><span>模拟调度 · 演进当前</span></div>
    <p class="basin-note">${escapeHtml(decision?.constraint || "开始演进后查看模拟调度状态。")}</p>
    ${basinTable(["水库指标", "演进当前", `未来${reservoirFuture.length}h窗口最大`], dispatchRows)}
    <div class="basin-note">判定原因和完整过程通过水库 marker 查看。</div>`;
}

function renderBasinRainChart(observation) {
  const element = document.getElementById("basinRainChart");
  if (!observation) { element.innerHTML = ""; return; }
  const past = state.basinRainfallHistory;
  const future = observation.rainfall_forecast || [];
  const points = [...past, ...future];
  const maximum = Math.max(1, ...points.flatMap(row => BASIN_RAIN_FIELDS.map(([field]) => Number(row[field]) || 0)));
  const x = i => 22 + i / Math.max(1, points.length - 1) * 294;
  const y = v => 40 - v / maximum * 32;
  const path = (rows, field, offset) => rows.map((row, i) => finiteTelemetryNumber(row[field]) === null ? "" : `${i ? "L" : "M"}${x(i + offset).toFixed(2)},${y(Number(row[field])).toFixed(2)}`).join(" ");
  const lines = BASIN_RAIN_FIELDS.map(([field, label, , color]) => `<path d="${path(past, field, 0)}" stroke="${color}"/><path d="${path(past.length ? [past.at(-1), ...future] : future, field, Math.max(0, past.length - 1))}" stroke="${color}" stroke-dasharray="3 3"><title>${label} · 未来输入</title></path>`).join("");
  element.innerHTML = `<span>三分区雨量 · 实线已回放 / 虚线未来输入</span><svg viewBox="0 0 320 54" role="img" aria-label="三分区面雨量过程"><g fill="none" stroke-width="1.3">${lines}<path d="M${x(Math.max(0, past.length - 1))},3V42" stroke="#64748b" stroke-dasharray="2 3"/></g><text x="0" y="10">${maximum.toFixed(1)}</text><text x="22" y="53">${escapeHtml(formatMockClock(points[0]?.valid_time))}</text><text x="316" y="53" text-anchor="end">${escapeHtml(formatMockClock(points.at(-1)?.valid_time))}</text></svg>`;
}

function renderImpactReadiness() {
  const timeline = currentHydrodynamicTimelineContext();
  const ready = ["time_slice", "envelope"].includes(timeline.mode) && Boolean(state.hydrodynamicTimeline.layer);
  const button = document.getElementById("runImpactAnalysis");
  const busy = Boolean(state.impactRefreshController);
  button.disabled = !ready || busy;
  button.textContent = busy ? "分析中…" : state.impactAnalysis?.lastResult ? "重新分析当前范围" : "分析当前范围";
  const scope = state.impactAnalysis?.lastResult?.analysis_scope;
  document.getElementById("impactReadiness").textContent = ready
    ? `淹没预测 ${state.hydrodynamicTimeline.forecastVersion || ""} 已就绪${scope?.mode === "selected" ? ` · 指定 ${scope.matched_count} 个对象` : ""}`
    : "尚无淹没预测";
  if (!state.impactRequested && !state.impactAnalysis) {
    document.getElementById("impactCount").textContent = "--";
    document.getElementById("impactNearbyCount").textContent = "--";
    document.getElementById("impactStatus").textContent = ready ? "未分析" : "等待预测";
    document.getElementById("impactList").innerHTML = `<p class="impact-empty">${ready ? "淹没预测已就绪，尚未进行对象级影响分析。点击上方按钮分析当前范围。" : "加载淹没预测后，可对道路、桥梁等对象进行影响分析。"}</p>`;
    if (ready) setImpactScopeLabel(document.getElementById("impactTimeLabel"), timeline.current_hydrodynamic_time_h, timeline.current_hydrodynamic_valid_at, timeline.mode === "envelope");
  }
}
