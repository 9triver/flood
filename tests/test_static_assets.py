from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_DIR / "server" / "static"


class StaticAssetTest(unittest.TestCase):
    def test_product_title_is_consistent(self):
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        title = "基于大模型的水路联动应急智能体集群应用"

        self.assertIn(f"<title>{title}</title>", index)
        self.assertIn(f'content="{title}"', index)
        self.assertIn("document.title = state.bootstrap.title", app)

    def test_launch_cover_introduces_product_and_opens_workbench(self):
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn('class="launch-cover" id="launchCover"', index)
        self.assertIn('id="launchCoverTitle"', index)
        self.assertIn("基于大模型的水路联动", index)
        self.assertIn("应急智能体集群应用", index)
        self.assertIn('id="enterWorkbenchBtn"', index)
        self.assertIn('id="coverReturnBtn"', index)
        self.assertIn('id="appShell" inert', index)
        self.assertIn("function initLaunchCover()", app)
        self.assertIn("function collapseWorkbenchForLaunchCover()", app)
        self.assertIn("collapseWorkbenchForLaunchCover();", app)
        self.assertIn("setTelemetryPanelOpen(false);", app)
        self.assertIn("setAgentDrawerOpen(false);", app)
        self.assertIn("function fitWatershedForLaunchCover", app)
        self.assertIn("paddingTopLeft: [leftPadding, 64]", app)
        self.assertIn("paddingBottomRight: [48, 48]", app)
        self.assertIn("fitWatershedForLaunchCover(false);", app)
        self.assertIn("state.baseBounds.getCenter()", app)
        self.assertIn("fittedZoom + 0.25", app)
        self.assertIn("zoomSnap: 0.25", app)
        self.assertIn("objectType === \"Watershed\" && state.launchCoverVisible", app)
        self.assertIn("fitWatershedForLaunchCover(true, { animate: false });", app)
        self.assertIn('appShell.inert = visible;', app)
        self.assertIn('state.map?.invalidateSize({ animate: false });', app)
        self.assertNotIn('class="launch-network"', index)
        self.assertIn("智能体集群就绪", index)
        self.assertIn('/app.js?v=12', index)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)

    def test_hydrodynamic_timeline_avoids_redundant_work(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("initialFilters.time_h = formatHydrodynamicHour", app)
        self.assertIn("hydrodynamicFilterSignature(this.options.resultFilters)", app)
        self.assertIn("this._abortPendingTileRequests();", app)
        self.assertIn("signal: controller.signal", app)
        self.assertIn("scheduleHydrodynamicTimelineIndex", app)
        self.assertIn("if (timeline.layer?.isLoading?.()) return;", app)
        self.assertIn("if (state.hydrodynamicTimeline.playing) return;", app)
        self.assertIn("bounds: hydrodynamicLayerBounds(state.hydrodynamicResultMeta, true)", app)

    def test_issued_directives_open_in_read_only_editor(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn('data-view-directive="${escapeHtml(directive.directive_id || "")}"', app)
        self.assertNotIn('<details class="directive-history-item">', app)
        self.assertNotIn("directive-history-detail", app)
        self.assertIn("function openIssuedDirective(directiveId)", app)
        self.assertIn("setDirectiveEditorReadOnly(true);", app)
        self.assertIn("document.getElementById(id).readOnly = readOnly", app)
        self.assertIn('document.getElementById("directivePriority").disabled = readOnly', app)
        self.assertIn('document.getElementById("directiveIssueBtn").hidden = readOnly', app)
        self.assertIn('id="directiveCopyBtn" type="button" hidden', index)
        self.assertIn(".directive-draft-toast.is-readonly", styles)

    def test_playback_processing_is_driven_by_backend_runtime_state(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn('state.playbackProcessing = phase === "processing"', app)
        self.assertIn('setTelemetryState("处理中", "processing")', app)
        self.assertIn("const icon = state.playbackProcessing", app)
        self.assertIn('? "activity"', app)
        self.assertNotIn("pausePlaybackAfterInundation", app)
        self.assertNotIn("playbackAutoPauseArmed", app)
        self.assertIn(".playback-toggle.is-processing", styles)
        self.assertIn('id="playbackAutoPauseSwitch" type="checkbox" checked', index)
        self.assertIn('playbackAutoPauseEnabled: true', app)
        self.assertIn('fetch("/api/autonomy/auto-pause"', app)
        self.assertIn('setPlaybackAutoPauseControl(data.auto_pause_enabled)', app)
        self.assertIn('state.playbackStepPending = false;\n    updateTelemetryRuntimeStatus(data);', app)
        self.assertNotIn(
            'state.playbackStepPending = Boolean(data.forecast_triggered)',
            app,
        )
        self.assertIn(
            'const stepUnavailable = state.runtimeStatus?.step_available === false;',
            app,
        )
        self.assertIn('.playback-auto-pause-track', styles)

    def test_direct_lucide_references_exist_in_local_bundle(self):
        bundle = (STATIC_DIR / "vendor" / "lucide.js").read_text(encoding="utf-8")
        available = set(re.findall(r"^\s*'([a-z0-9-]+)':", bundle, re.MULTILINE))
        referenced: set[str] = set()
        for filename in ("index.html", "app.js"):
            source = (STATIC_DIR / filename).read_text(encoding="utf-8")
            referenced.update(re.findall(r'data-lucide="([a-z0-9-]+)"', source))

        self.assertEqual(set(), referenced - available)

    def test_frontend_libraries_are_served_locally(self):
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn('/styles.css?v=11', index)
        self.assertIn('/app.js?v=12', index)

        self.assertIn('/vendor/leaflet/leaflet.css?v=1.9.4', index)
        self.assertIn('/vendor/leaflet/leaflet.js?v=1.9.4', index)
        self.assertIn('/vendor/marked/marked.min.js?v=12.0.2', index)
        self.assertNotIn("unpkg.com", index)
        self.assertNotIn("cdn.jsdelivr.net", index)

        expected_files = (
            "vendor/leaflet/leaflet.css",
            "vendor/leaflet/leaflet.js",
            "vendor/leaflet/LICENSE",
            "vendor/leaflet/images/layers.png",
            "vendor/leaflet/images/layers-2x.png",
            "vendor/leaflet/images/marker-icon.png",
            "vendor/leaflet/images/marker-icon-2x.png",
            "vendor/leaflet/images/marker-shadow.png",
            "vendor/marked/marked.min.js",
            "vendor/marked/LICENSE.md",
        )
        for relative_path in expected_files:
            self.assertTrue((STATIC_DIR / relative_path).is_file(), relative_path)

    def test_domain_os_products_can_drive_existing_gis_views(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("await startDomainOSBridge();", app)
        self.assertIn('new EventSource(`/api/domain/events/stream?', app)
        self.assertIn('filters: { product_id: selected }', app)
        self.assertIn("setHydrodynamicEnvelopeView();", app)
        self.assertIn("assessment_product_id: selected", app)

    def test_impact_list_reuses_domain_map_symbols(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn("objectIconInfo(impact.object_type, impact)", app)
        self.assertIn("FloodMapSymbols?.render(iconInfo.icon)", app)
        self.assertIn('class="impact-list-symbol object-symbol-', app)
        school_color = re.search(
            r"\.object-symbol-school\s*\{\s*--object-symbol-color:\s*([^;]+);",
            styles,
        )
        hospital_color = re.search(
            r"\.object-symbol-hospital\s*\{\s*--object-symbol-color:\s*([^;]+);",
            styles,
        )
        self.assertIsNotNone(school_color)
        self.assertIsNotNone(hospital_color)
        self.assertNotEqual(school_color.group(1), hospital_color.group(1))

    def test_domain_object_popups_remain_open_until_user_closes_them(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("function objectPopupOptions(objectType)", app)
        self.assertIn("autoClose: false", app)
        self.assertIn("closeOnClick: false", app)
        self.assertIn("closeButton: true", app)
        self.assertIn("objectPopupOptions(objectType)", app)
        self.assertIn('objectPopupOptions("Reservoir")', app)
        self.assertIn('objectPopupOptions("River")', app)
        self.assertIn("function syncStationPopupOpenState()", app)
        self.assertNotIn("state.map?.closePopup();", app)
        self.assertNotIn("removeLayer(state.selectedImpactLayerKey);", app)

    def test_station_types_use_distinct_local_map_symbols(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        symbols = (STATIC_DIR / "map-symbols.js").read_text(encoding="utf-8")
        available = set(re.findall(
            r"^\s*'([a-z0-9-]+)':", symbols, re.MULTILINE,
        ))
        expected = {
            "flash_flood": ("station-flash-flood", "cloud-lightning", "山洪测站"),
            "meteorological": ("station-meteorological", "cloud-sun", "气象测站"),
            "hydrological": ("station-hydrological", "gauge", "水文测站"),
            "reservoir": ("station-reservoir", "dam", "水库测站"),
        }

        self.assertEqual(4, len({item[1] for item in expected.values()}))
        for station_type, (key, icon, label) in expected.items():
            self.assertIn(icon, available)
            self.assertIn(
                f'{station_type}: {{ key: "{key}", icon: "{icon}", label: "{label}" }}',
                app,
            )
        self.assertIn(
            '? `${props.name} · ${info.label}${rainfallLabel}${reservoirLabel}`',
            app,
        )
        self.assertIn(
            '<div class="popup-meta">${escapeHtml(stationType)} · 编码 ${escapeHtml(id)}</div>',
            app,
        )
        self.assertIn('if (objectType === "Station") return stationDetailHtml(props);', app)
        self.assertIn('["类型", stationType]', app)

    def test_station_rainfall_updates_with_playback_and_loads_layer_once(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn(
            "observation.station_rainfall_forecast,",
            app,
        )
        self.assertIn(
            "ensureStationRainfallLayer(observation.station_rainfall);",
            app,
        )
        self.assertIn("stationRainfall: new Map()", app)
        self.assertIn("stationRainfallHistory: new Map()", app)
        self.assertIn("stationRainfallForecast: new Map()", app)
        self.assertIn("stationRainfallLayerInitialized: false", app)
        self.assertIn("function recordStationRainfallHistory(readings, observedAt)", app)
        self.assertIn("function normalizeStationRainfallForecast(forecasts)", app)
        self.assertIn("function ensureStationRainfallLayer(readings)", app)
        self.assertIn("function refreshStationTelemetryMarkers()", app)
        self.assertIn('if (entry.objectType !== "Station") return;', app)
        self.assertIn(
            'void loadObject("Station", { station_type: "meteorological"',
            app,
        )
        self.assertIn("state.stationRainfallLayerInitialized = false;", app)
        self.assertIn("station-rainfall-badge", app)
        self.assertIn("function stationRainfallBadgeSide(stationId)", app)
        self.assertIn("function stationRainfallChartHtml(stationId)", app)
        self.assertIn("当前模拟观测", app)
        self.assertIn("未来 ${forecastHours} 小时累计", app)
        self.assertIn(".station-rainfall-badge {", styles)
        self.assertIn(".station-rainfall-badge.is-left {", styles)
        self.assertIn(".station-rainfall-chart rect.is-observed {", styles)
        self.assertIn(".station-rainfall-chart rect.is-forecast {", styles)
        self.assertIn(".station-rainfall-now {", styles)
        self.assertIn('state.map.on("popupopen", (event) => {', app)
        self.assertIn("function syncStationPopupOpenState()", app)
        self.assertIn("window.requestAnimationFrame(syncStationPopupOpenState)", app)
        self.assertIn("function keepPopupInsideMap(popup)", app)
        self.assertIn(".map-stage.is-station-popup-open .conclusion-toast-region {", styles)
        self.assertIn("autoPanPaddingTopLeft: L.point(18, 18)", app)
        self.assertNotIn("综合雨量模拟分解值", app)
        self.assertNotIn(".station-rainfall-note {", styles)

    def test_reservoir_station_updates_with_playback_and_renders_forecast_charts(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn("normalizeReservoirForecast(observation?.reservoir_forecast)", app)
        self.assertIn("updateReservoirTelemetry(observation, simulationTime);", app)
        self.assertIn("ensureReservoirStationLayer(observation);", app)
        self.assertIn("reservoirTelemetry: null", app)
        self.assertIn("reservoirTelemetryHistory: []", app)
        self.assertIn("reservoirForecast: null", app)
        self.assertIn("reservoirAssessment: null", app)
        self.assertIn("reservoirStationLayerInitialized: false", app)
        self.assertIn("function recordReservoirTelemetryHistory(current, observedAt)", app)
        self.assertIn("function normalizeReservoirForecast(forecast)", app)
        self.assertIn("function ensureReservoirStationLayer(observation)", app)
        self.assertIn(
            'void loadObject("Station", { station_type: "reservoir"',
            app,
        )
        self.assertIn("function reservoirLevelChartHtml()", app)
        self.assertIn("function reservoirFlowChartHtml()", app)
        self.assertIn("function reservoirForecastAssessmentHtml()", app)
        self.assertIn("function reservoirAlertText(alert)", app)
        self.assertIn('"{triggered_at}",', app)
        self.assertIn("水库水位", app)
        self.assertIn("入库与泄洪流量", app)
        self.assertIn("未来 ${escapeHtml(String(assessment.window_hours || 24))} 小时最高水位", app)
        self.assertIn(".station-reservoir-badge {", styles)
        self.assertIn(".station-reservoir-level-band.is-normal", styles)
        self.assertIn(".station-reservoir-level-line.is-forecast", styles)
        self.assertIn(".station-reservoir-flow-line.inflow", styles)
        self.assertIn(".station-reservoir-flow-line.release", styles)
        self.assertIn(".station-reservoir-alert.is-critical", styles)
        self.assertIn(
            '".station-rainfall-panel, .station-reservoir-panel"',
            app,
        )
        self.assertIn(
            ".map-stage.is-station-popup-open .map-toolbar-center {",
            styles,
        )

    def test_map_time_context_synchronizes_forecast_layers_and_telemetry(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn('mapTimeContext: {', app)
        self.assertIn('function setMapTimeContext(context = {})', app)
        self.assertIn('function mapRainfallForContext()', app)
        self.assertIn('rainfallSeries: []', app)
        self.assertIn('function hydrodynamicRainfallSeries(forecast, resultVersion)', app)
        self.assertIn(
            'normalizeTimedSeries(forecast?.rainfall_series, ["rainfall_mm"])',
            app,
        )
        map_rainfall_start = app.index("function mapRainfallForContext()")
        map_rainfall_end = app.index(
            "\nfunction ensureStationRainfallLayer", map_rainfall_start,
        )
        map_rainfall = app[map_rainfall_start:map_rainfall_end]
        self.assertIn("state.hydrodynamicTimeline.rainfallSeries", map_rainfall)
        self.assertNotIn("state.rainfallForecast", map_rainfall)
        self.assertIn('function interpolateTimedPoint(series, validAt, numericFields)', app)
        self.assertIn('mode: envelope ? "envelope" : "time_slice"', app)
        self.assertIn('params.set("time_h", String(timeline.current_hydrodynamic_time_h))', app)
        self.assertNotIn('id="hydroEnvelopeBtn"', index)
        self.assertNotIn('function setHydrodynamicEnvelopeMode()', app)
        self.assertNotIn('.hydro-envelope-toggle', styles)
        self.assertIn('data-lucide="activity"', index)
        self.assertIn('逐帧预览预测结果', index)

    def test_situation_workbench_combines_current_and_future_context(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn('observation.rainfall_forecast', app)
        self.assertIn('observation.boundary_flow_forecast', app)
        self.assertIn('function renderForecastWindowSummary(observation)', app)
        self.assertIn('flow-history-line is-forecast', app)
        self.assertIn('flow-history-now', app)
        self.assertIn('flow-history-threshold', app)
        self.assertIn('未来24小时预测', index)
        self.assertIn('四边界流量 · 实况 / 预测', index)
        self.assertEqual(4, index.count('class="telemetry-forecast-value"'))
        self.assertIn('class="telemetry-header-context"', index)
        self.assertIn('class="telemetry-time-context telemetry-forecast-window"', index)
        self.assertNotIn('class="telemetry-forecast-head"', index)
        self.assertIn('function forecastAlertSummaryLabel(alert, hasAssessment)', app)
        self.assertIn('.telemetry-forecast-strip {', styles)
        self.assertNotIn('.telemetry-forecast-head {', styles)
        self.assertIn('grid-template-columns: repeat(4, minmax(0, 1fr));', styles)
        self.assertIn('.telemetry-forecast-value {', styles)
        self.assertIn('.flow-history-line.is-forecast {', styles)

    def test_situation_workbench_labels_time_and_impact_scope(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn('<span>当前演进时刻</span>', index)
        self.assertIn('class="impact-panel-context"', index)
        self.assertIn('title="当前影响分析范围"', index)
        self.assertIn('id="situationEvolutionSummary"', index)
        self.assertIn('id="situationForecastSummary"', index)
        self.assertIn('当前分析范围内的受影响对象数', index)
        self.assertIn('id="impactCount"', index)
        self.assertIn('>0 个</output>', index)
        self.assertIn('return actual ? `预测 ${offset} · ${actual}`', app)
        self.assertIn('count.textContent = `${impacts.length} 个`;', app)
        self.assertIn('count.textContent = "--";', app)
        self.assertIn('function setImpactScopeLabel(', app)
        self.assertIn('function renderSituationSummary()', app)
        self.assertIn('function evolutionPlaybackStatusLabel()', app)
        self.assertNotIn('function setSituationSummary(', app)
        self.assertIn('status.textContent = "分析中";', app)
        self.assertIn('status.textContent = impacts.length ? "已分析" : "未发现";', app)
        self.assertNotIn(
            'setSituationSummary(`${time.textContent} · 受影响对象', app,
        )
        timeline_start = app.index("function setHydrodynamicTimelineIndex(index)")
        timeline_end = app.index(
            "\nfunction toggleHydrodynamicTimelinePlayback", timeline_start,
        )
        self.assertNotIn("telemetryTime", app[timeline_start:timeline_end])
        observation_start = app.index("function renderMockObservation(event)")
        observation_end = app.index("\nfunction clearMockTelemetry", observation_start)
        self.assertIn("telemetryTime", app[observation_start:observation_end])
        self.assertIn('.telemetry-time-context {', styles)
        self.assertIn('.situation-summary {', styles)
        self.assertIn('.impact-panel-context {', styles)
        self.assertIn(
            'minmax(120px, 1fr) auto minmax(400px, 640px)', styles,
        )
        self.assertIn(
            '34px auto minmax(180px, 1fr) minmax(132px, auto)', styles,
        )
        responsive_start = styles.index("@container (max-width: 1120px)")
        responsive_end = styles.index(
            "@container (max-width: 900px)", responsive_start,
        )
        self.assertNotIn(
            ".playback-toggle-copy", styles[responsive_start:responsive_end],
        )
        for label in (
            "开始演进", "继续演进", "暂停演进", "事件处理中", "开始新演进",
        ):
            self.assertIn(label, app)

    def test_map_management_omits_internal_supporting_layers(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        config_start = app.index("const OBJECT_LAYER_GROUPS = [")
        config_end = app.index("\nconst ID_FIELDS", config_start)
        layer_groups = app[config_start:config_end]

        self.assertNotIn('"HydraulicStructure"', layer_groups)
        self.assertNotIn('"EvacuationRoute"', layer_groups)
        self.assertIn('label: "水系与监测"', layer_groups)
        self.assertIn('label: "风险与应急"', layer_groups)
        self.assertIn('label: "洪水预测"', layer_groups)
        self.assertIn('label: "行政边界"', layer_groups)
        self.assertLess(layer_groups.index('"ForecastResult"'), layer_groups.index('"HydrodynamicGridCell"'))
        self.assertIn('ForecastResult: { label: "预测淹没范围"', app)

    def test_facility_filters_toggle_their_map_layers(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("async function toggleFacility(type)", app)
        self.assertIn('dataKey: "facility"', app)
        self.assertIn('options: [["school", "学校"], ["hospital", "医院"], ["government", "政府"]]', app)
        self.assertIn("syncFilteredLayerButtons();", app)

    def test_station_filter_supports_four_station_types_without_all(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("async function toggleStation(type)", app)
        self.assertIn('dataKey: "station"', app)
        for value, label in (
            ("flash_flood", "山洪"),
            ("meteorological", "气象"),
            ("hydrological", "水文"),
            ("reservoir", "水库"),
        ):
            self.assertIn(f'["{value}", "{label}"]', app)
        station_filter_start = app.index("function createStationFilterControl()")
        station_filter_end = app.index("\nfunction createFacilityFilterControl()", station_filter_start)
        self.assertNotIn('["all", "全部"]', app[station_filter_start:station_filter_end])

    def test_map_management_uses_grouped_single_column_list(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn('className = "object-group"', app)
        self.assertIn('class="object-group-title"', app)
        self.assertNotIn('className = "object-group-toggle"', app)
        self.assertNotIn('data-lucide="eye"', app)
        self.assertIn("grid-template-columns: 28px minmax(0, 1fr);", styles)
        self.assertIn(".object-group-items", styles)

    def test_map_management_uses_consistent_type_scale(self):
        styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        selectors = {
            ".object-row {": "font-size: 14px;",
            ".basemap-option span {": "font-size: 14px;",
            ".object-filter-label {": "font-size: 13px;",
            ".segmented button {": "font-size: 13px;",
            ".basemap-option small {": "font-size: 11px;",
        }
        for selector, declaration in selectors.items():
            start = styles.index(selector)
            end = styles.index("}", start)
            self.assertIn(declaration, styles[start:end])

    def test_satellite_is_the_default_basemap(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn('const DEFAULT_BASEMAP_KEY = "satellite";', app)
        self.assertIn('basemapKey: DEFAULT_BASEMAP_KEY', app)
        self.assertIn('data-basemap="satellite" role="radio" aria-checked="true"', index)
        self.assertIn('data-basemap="standard" role="radio" aria-checked="false"', index)

    def test_river_watershed_and_reservoir_are_default_object_layers(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        config_start = app.index("const DEFAULT_OBJECT_LAYERS = [")
        config_end = app.index("\n];", config_start)
        default_layers = app[config_start:config_end]

        self.assertIn('{ objectType: "Watershed", fit: true }', default_layers)
        self.assertIn('{ objectType: "River", fit: false }', default_layers)
        self.assertIn('{ objectType: "Reservoir", fit: false }', default_layers)
        self.assertIn("await loadDefaultObjectLayers();", app)
        self.assertIn('return { reservoir_id: "longtan" };', app)

        reset_start = app.index("function resetMap()")
        reset_end = app.index("\nfunction clearHydrodynamicResults()", reset_start)
        self.assertIn(
            '["River", "Watershed", "Reservoir"].includes(meta?.objectType)',
            app[reset_start:reset_end],
        )

    def test_map_management_reuses_domain_object_icons(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        symbols = (STATIC_DIR / "map-symbols.js").read_text(encoding="utf-8")

        self.assertIn("function layerObjectIcon(objectType", app)
        self.assertIn("objectIconInfo(objectType, feature)", app)
        self.assertIn("FloodMapSymbols?.render(info.icon)", app)
        self.assertIn('}, "layer-filter-icon")', app)
        self.assertNotIn("function objectSwatch", app)
        self.assertIn("'layers':", symbols)
        self.assertIn("'map':", symbols)

    def test_evolution_controls_belong_to_situation_workbench(self):
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        toolbar_start = index.index('<div class="map-toolbar">')
        workbench_start = index.index('id="situationWorkbench"')
        controls_start = index.index('class="situation-playback-controls"')
        toolbar_markup = index[toolbar_start:workbench_start]
        workbench_markup = index[workbench_start:]

        self.assertLess(toolbar_start, workbench_start)
        self.assertLess(workbench_start, controls_start)
        self.assertNotIn('id="playbackToggleBtn"', toolbar_markup)
        self.assertIn('id="playbackToggleBtn"', workbench_markup)
        self.assertNotIn('id="telemetryPanelBtn"', index)

    def test_evolution_controls_are_rendered_from_playback_phase(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("function updatePlaybackControls(data = {})", app)
        self.assertIn('btn.hidden = !state.playbackPaused', app)
        self.assertIn('? "开始新演进"', app)


if __name__ == "__main__":
    unittest.main()
