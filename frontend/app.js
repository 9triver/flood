const state = {
  map: null,
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
  lastTrace: null,
};

const OBJECT_CONFIG = {
  River: { label: "珊瑚河", color: "#0e7490", swatch: "line" },
  Watershed: { label: "珊瑚河流域", color: "#1f2937", swatch: "fill" },
  Waterway: { label: "河道水系", color: "#0e7490", swatch: "line" },
  County: { label: "行政边界", color: "#7b8794", swatch: "line" },
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
  HistoricalFloodMark: { label: "历史洪痕", color: "#be123c", swatch: "point" },
  Cell: { label: "淹没范围", color: "#2f80c9", swatch: "fill" },
  ForecastCell: { label: "预测淹没", color: "#7c3aed", swatch: "fill" },
};

const ID_FIELDS = {
  River: "river_id",
  Watershed: "watershed_id",
  Waterway: "waterway_id",
  County: "county_id",
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
  HydroObservation: "observation_id",
  HistoricalFloodMark: "mark_id",
  Cell: "cell_id",
  ForecastCell: "forecast_cell_id",
};

document.addEventListener("DOMContentLoaded", async () => {
  initMap();
  bindEvents();
  await bootstrap();
  await loadObject("Watershed", {}, { fit: true });
  await loadObject("County", {}, { fit: false });
  addMessage("agent", "基础对象已加载。");
  startAutonomyStream();
  renderIcons();
});

function initMap() {
  state.map = L.map("map", {
    zoomControl: false,
    preferCanvas: true,
  }).setView([24.4, 111.35], 10);

  L.control.zoom({ position: "bottomleft" }).addTo(state.map);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(state.map);
}

async function bootstrap() {
  const res = await fetch("/api/bootstrap");
  state.bootstrap = await res.json();
  document.getElementById("contextPill").textContent = state.bootstrap.default_context;
  renderObjectList(state.bootstrap.mappable || []);
}

function renderObjectList(items) {
  const list = document.getElementById("objectList");
  const visible = ["River", "Watershed", "Waterway", "County", "ForecastCell", "HydroStation", "Road", "Reservoir", "Sluice", "Bridge", "HydraulicStructure", "Risk", "Place", "Route"];
  list.innerHTML = "";

  visible.forEach((objectType) => {
    const item = items.find((entry) => entry.object_type === objectType) || {};
    const config = OBJECT_CONFIG[objectType];
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
  document.getElementById("clearBtn").addEventListener("click", resetMap);
  document.querySelectorAll("[data-panel-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => activateAgentPane(btn.dataset.panelToggle));
  });
  document.getElementById("chatInput").addEventListener("focus", () => activateAgentPane("chat"));
  document.querySelectorAll("[data-facility]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const type = btn.dataset.facility;
      const labels = { school: "学校", hospital: "医院", government: "政府机构" };
      loadObject("Facility", { facility_type: type }, { fit: true, label: labels[type] });
    });
  });
  document.getElementById("chatForm").addEventListener("submit", onChatSubmit);
}

async function toggleObject(objectType) {
  const filters = objectType === "ForecastCell" ? { forecast_id: "latest" } : {};
  const key = layerKey(objectType, filters);
  if (state.layerGroups.has(key)) {
    removeLayer(key);
    return;
  }
  await loadObject(objectType, filters, { fit: false });
}

async function loadObject(objectType, filters = {}, options = {}) {
  const key = layerKey(objectType, filters);
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
  Object.entries(filters || {}).forEach(([name, value]) => params.set(name, value));
  if (options.simplify_tolerance) params.set("simplify_tolerance", options.simplify_tolerance);

  const res = await fetch(`/api/geojson?${params.toString()}`);
  if (!res.ok) throw new Error(await res.text());
  const geojson = await res.json();
  const layer = L.geoJSON(geojson, {
    style: (feature) => featureStyle(objectType, feature),
    pointToLayer: (feature, latlng) => L.circleMarker(latlng, pointStyle(objectType, feature)),
    onEachFeature: (feature, layerItem) => {
      indexFeature(objectType, feature, layerItem);
      layerItem.on("click", () => selectFeature(objectType, feature, layerItem));
      layerItem.bindPopup(popupHtml(objectType, feature));
    },
  }).addTo(state.map);

  state.layerGroups.set(key, layer);
  state.layerMeta.set(key, { objectType, filters, label: options.label || OBJECT_CONFIG[objectType]?.label || objectType });
  setObjectButtonActive(objectType, true);
  if (objectType === "Watershed") state.baseBounds = layer.getBounds();
  if (options.fit) fitLayer(layer);
  return layer;
}

function removeLayer(key) {
  const layer = state.layerGroups.get(key);
  const meta = state.layerMeta.get(key);
  if (meta) unindexLayer(meta.objectType, layer);
  if (layer) state.map.removeLayer(layer);
  state.layerGroups.delete(key);
  state.layerMeta.delete(key);
  if (meta) setObjectButtonActive(meta.objectType, hasObjectType(meta.objectType));
}

function resetMap() {
  clearFocus();
  clearHighlights();
  clearEventMarkers();
  for (const key of Array.from(state.layerGroups.keys())) {
    const meta = state.layerMeta.get(key);
    if (!["Watershed", "County"].includes(meta?.objectType)) {
      removeLayer(key);
    }
  }
  document.getElementById("contextPill").textContent = "基础态 · 领域对象地图";
  fitAll();
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
    if (data.label === "等待水文事件") return;
    addTrace("AUTO", data.label || "事件运行时", data.detail || "");
  });

  es.addEventListener("domain_event", (event) => {
    const data = parseEvent(event);
    renderDomainEvent(data);
  });

  es.addEventListener("agent_trace", (event) => {
    const data = parseEvent(event);
    if (shouldHideAutonomyTrace(data)) return;
    addTrace(data.tag || "AGENT", data.label || "智能体事件处理", data.detail || "");
  });

  es.addEventListener("map_actions", async (event) => {
    const data = parseEvent(event);
    if (data.context) document.getElementById("contextPill").textContent = data.context;
    await executeActions(data.map_actions || []);
    renderMetrics(data.result_cards || []);
  });

  es.onerror = () => {
    addTrace("AUTO", "闭环流断开", "5 秒后尝试重连。");
    es.close();
    state.autonomyStream = null;
    window.setTimeout(startAutonomyStream, 5000);
  };
}

function renderDomainEvent(data) {
  if (!data || !data.event_type) return;
  const tag = data.event_type === "HydroThresholdExceeded" ? "HYDRO" : "EVENT";
  addTrace(tag, data.title || data.event_type, eventDetail(data));
  setCyclePhase(eventPhase(data.event_type));
}

function eventDetail(data) {
  const payload = data.payload || {};
  if (data.event_type === "HydroThresholdExceeded") {
    return `${payload.station_name || data.source_id}: ${payload.metric_label || payload.metric} ${payload.value} ${payload.unit} / 阈值 ${payload.threshold} ${payload.unit}`;
  }
  if (data.event_type === "InundationGenerated") {
    return `预测单元 ${payload.forecast_cell_count || 0} 个，淹没面积 ${(Number(payload.inundated_area_km2 || 0)).toFixed(2)} km²`;
  }
  return data.severity || "";
}

function eventPhase(eventType) {
  return {
    HydroThresholdExceeded: "observe",
    InundationGenerated: "compute",
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
  if (objectType === "Cell" || objectType === "ForecastCell") {
    const depth = Number(feature.properties?.depth_m || feature.properties?.YMSS || 0);
    const color = objectType === "ForecastCell"
      ? depth > 1.2 ? "#4c1d95" : depth > 0.6 ? "#7c3aed" : "#c084fc"
      : depth > 1 ? "#14539a" : depth > 0.5 ? "#2f80c9" : "#7ab6df";
    return { color, weight: 0.5, fillColor: color, fillOpacity: objectType === "ForecastCell" ? 0.38 : 0.34 };
  }
  if (objectType === "Watershed") return { color: "#1f2937", weight: 1.3, fillColor: "#9bc4df", fillOpacity: 0.1 };
  if (objectType === "River") return { color: "#0e7490", weight: 4, opacity: 0.95 };
  if (objectType === "Waterway") return { color: "#0e7490", weight: 2.4, opacity: 0.9 };
  if (objectType === "County") return { color: "#7b8794", weight: 1.2, fillOpacity: 0 };
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
    const feature = layer.feature || {};
    if (layer.setStyle && objectType) {
      if (layer.setRadius) layer.setRadius(pointStyle(objectType, feature).radius);
      layer.setStyle(layer.setRadius ? pointStyle(objectType, feature) : featureStyle(objectType, feature));
    }
  });
  state.highlightedLayers = [];
}

async function highlightObjects(action = {}) {
  const objectType = action.object_type;
  const objectIds = (action.object_ids || []).map(String).filter(Boolean);
  if (!objectType || !objectIds.length) return false;
  await loadObject(objectType, action.filters || {}, { fit: false, label: action.label });
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
    params.set("selected", JSON.stringify(state.selected || {}));
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
    if (action.type === "load_object") {
      await loadObject(action.object_type, action.filters || {}, {
        fit: action.fit,
        label: action.label,
        simplify_tolerance: action.simplify_tolerance,
        refresh: action.refresh,
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
  item.className = "trace-item";
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

function shouldHideAutonomyTrace(data = {}) {
  return new Set(["EVENT", "RESULT", "SYSTEM", "CUT"]).has(data.tag);
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
    list_scenarios: "列出洪水情景",
    get_scenario_summary: "查看情景汇总",
    run_flood_forecast: "运行洪水预测",
    run_emergency_cycle: "运行闭环预警",
    analyze_risks: "分析风险",
    list_mappable_objects: "列出可绘制对象",
    export_objects_geojson: "导出对象 GeoJSON",
    ui_show_objects: "地图显示",
    ui_show_event_marker: "地图标记事件",
    ui_clear_map: "清空地图",
    ui_focus_object: "地图定位",
  };
  const parts = [];
  if (args.object_type) parts.push(args.object_type);
  if (Array.isArray(args.objects)) parts.push(args.objects.map((item) => item.object_type).filter(Boolean).join(", "));
  if (args.scenario_id) parts.push(args.scenario_id);
  if (args.return_period_year) parts.push(`${args.return_period_year}年一遇`);
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
