const state = {
  map: null,
  layerGroups: new Map(),
  layerMeta: new Map(),
  selected: null,
  bootstrap: null,
  baseBounds: null,
  sessionId: getSessionId(),
  activeStream: null,
  activeRunId: null,
};

const OBJECT_CONFIG = {
  River: { label: "珊瑚河流域", color: "#1f2937", swatch: "fill" },
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
  Cell: { label: "淹没范围", color: "#2f80c9", swatch: "fill" },
};

const ID_FIELDS = {
  River: "river_id",
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
  Cell: "cell_id",
};

document.addEventListener("DOMContentLoaded", async () => {
  initMap();
  bindEvents();
  await bootstrap();
  await loadObject("River", {}, { fit: true });
  await loadObject("County", {}, { fit: false });
  addMessage("agent", "基础对象已加载。");
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
  const visible = ["River", "County", "Road", "Reservoir", "Sluice", "Bridge", "HydraulicStructure", "Place", "Route"];
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
  const key = layerKey(objectType, {});
  if (state.layerGroups.has(key)) {
    removeLayer(key);
    return;
  }
  await loadObject(objectType, {}, { fit: false });
}

async function loadObject(objectType, filters = {}, options = {}) {
  const key = layerKey(objectType, filters);
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
      layerItem.on("click", () => selectFeature(objectType, feature, layerItem));
      layerItem.bindPopup(popupHtml(objectType, feature));
    },
  }).addTo(state.map);

  state.layerGroups.set(key, layer);
  state.layerMeta.set(key, { objectType, filters, label: options.label || OBJECT_CONFIG[objectType]?.label || objectType });
  setObjectButtonActive(objectType, true);
  if (objectType === "River") state.baseBounds = layer.getBounds();
  if (options.fit) fitLayer(layer);
  return layer;
}

function removeLayer(key) {
  const layer = state.layerGroups.get(key);
  const meta = state.layerMeta.get(key);
  if (layer) state.map.removeLayer(layer);
  state.layerGroups.delete(key);
  state.layerMeta.delete(key);
  if (meta) setObjectButtonActive(meta.objectType, hasObjectType(meta.objectType));
}

function resetMap() {
  for (const key of Array.from(state.layerGroups.keys())) {
    const meta = state.layerMeta.get(key);
    if (!["River", "County"].includes(meta?.objectType)) {
      removeLayer(key);
    }
  }
  document.getElementById("contextPill").textContent = "基础态 · 领域对象地图";
  fitAll();
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

function featureStyle(objectType, feature) {
  if (objectType === "Cell") {
    const depth = Number(feature.properties?.depth_m || feature.properties?.YMSS || 0);
    const color = depth > 1 ? "#14539a" : depth > 0.5 ? "#2f80c9" : "#7ab6df";
    return { color, weight: 0.5, fillColor: color, fillOpacity: 0.34 };
  }
  if (objectType === "River") return { color: "#1f2937", weight: 1.3, fillColor: "#9bc4df", fillOpacity: 0.1 };
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
  return {
    radius: objectType === "Bridge" ? 4.5 : 5.5,
    color: "#ffffff",
    weight: 1.5,
    fillColor: color,
    fillOpacity: 0.92,
  };
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
  layerItem.openPopup();
}

function detailHtml(objectType, props) {
  const keys = Object.keys(props).filter((key) => props[key] !== "" && props[key] !== null && key !== "geometry");
  const rows = keys.slice(0, 8).map((key) => `<div><strong>${escapeHtml(key)}</strong>: ${escapeHtml(String(props[key]))}</div>`);
  return `<div class="muted"><strong>${escapeHtml(OBJECT_CONFIG[objectType]?.label || objectType)}</strong>${rows.join("")}</div>`;
}

async function onChatSubmit(event) {
  event.preventDefault();
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

  es.addEventListener("reasoning", (event) => {
    const data = parseEvent(event);
    addTrace("THINK", "模型思考", compactText(data.content || ""));
  });

  es.addEventListener("debug", (event) => {
    const data = parseEvent(event);
    addTrace("DBG", data.stage || "debug", compactText(data.content || ""));
  });

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
    addTrace("DONE", "Agent 完成", "");
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
      });
    }
    if (action.type === "focus_selected") {
      fitAll();
    }
  }
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
  const item = document.createElement("div");
  item.className = "trace-item";
  item.innerHTML = `
    <div class="trace-label"><span>${escapeHtml(label || "")}</span><span class="trace-tag">${escapeHtml(tag)}</span></div>
    ${detail ? `<div class="trace-detail">${escapeHtml(String(detail))}</div>` : ""}
  `;
  wrap.appendChild(item);
  wrap.scrollTop = wrap.scrollHeight;
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
    analyze_risks: "分析风险",
    list_mappable_objects: "列出可绘制对象",
    export_objects_geojson: "导出对象 GeoJSON",
  };
  const parts = [];
  if (args.object_type) parts.push(args.object_type);
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
