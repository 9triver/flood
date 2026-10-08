from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from tests import test_forecast_context as context_tests
from domains.flood.runtime.forecast_context import get_flood_status
from domains.flood.runtime.service import FloodRuntimeService
from oag.harness import Harness
from oag.loop.tool_executor import ToolExecutor
from oag.ontology.loader import load_domain
from oag.runtime.events import TextEvent
from server.agent_runs import AgentRun
from server.chat.analysis_context import capture_analysis_context, analysis_scope, normalize_analysis_tool, SLICE_TOOLS
from server.chat.service import FloodChatService
from server.chat.side_effects import AgentSideEffects


class ChatAnalysisContextTests(unittest.TestCase):
    write_json = staticmethod(context_tests.ForecastContextTests.write_json)
    start = context_tests.ForecastContextTests.start

    @classmethod
    def setUpClass(cls):
        cls.ontology, cls.repository, cls.registry = load_domain(Path(__file__).resolve().parents[1] / 'domains/flood')

    def setUp(self):
        context_tests.ForecastContextTests.setUp(self)
        self.start()
        self.copy_version('v001')
        self.selected = self.selection(2)
        self.harness = Harness(self.ontology, self.repository, self.registry, None, 'test')
        self.harness.hooks.register('pre_tool_call', normalize_analysis_tool)

    def copy_version(self, version):
        dest = self.root / 'forecasts' / version
        dest.mkdir(exist_ok=True)
        for source in (self.root / 'forecasts/latest').iterdir():
            shutil.copy2(source, dest / source.name)
        self.write_json(dest / 'forecast.json', {**self.metadata, 'forecast_id': version})

    def selection(self, hour, version='v001'):
        return {'workspace_id': self.first, 'hydrodynamic_timeline': {
            'active': True, 'mode': 'time_slice', 'forecast_id': 'latest', 'forecast_version': version,
            'current_hydrodynamic_time_h': hour,
        }}

    def call(self, args=None, session='chat'):
        result = self.harness.execute_tool('get_flood_status', args or {}, session_id=session)
        return json.loads(result.content)

    def test_reported_selected_235_hour_frame_works_without_zero_hour_slice(self):
        self.metadata.update(valid_from='2026-07-03T09:00:00+08:00', valid_to='2026-07-04T09:00:00+08:00')
        self.write_json(self.root / 'boundary_flows/latest_forecast_input.json', {'summary': {
            'boundary_flow_id': 'input-1', 'window_start': self.metadata['valid_from'], 'window_end': self.metadata['valid_to']}})
        self.write_json(self.root / 'forecasts/latest/time_steps.json', {'time_steps_h': [0.5, 1, 23.5]})
        self.copy_version('v003')
        self.write_json(self.root / 'forecasts/latest.json', {'forecast_id': 'v003'})
        self.start(9)
        selected = self.selection(23.5, 'v003')
        selected['hydrodynamic_timeline']['current_hydrodynamic_valid_at'] = '2026-07-04T08:30:00+08:00'
        self.assertEqual(get_flood_status()['status'], 'time_unavailable')
        with analysis_scope(capture_analysis_context(selected)):
            result = self.call({'view': 'current'})
            self.assertTrue(result['has_inundation'])
            self.assertEqual(result['forecast_version'], 'v003')
            self.assertEqual(result['time_h'], 23.5)
            self.assertEqual(result['analysis_time_at'], '2026-07-04T08:30:00+08:00')
            self.assertTrue(self.call({'view': 'simulation_current'})['blocked'])

    def test_explicit_time_simulation_clock_and_envelope_override_selected_frame(self):
        with analysis_scope(capture_analysis_context(self.selected)):
            self.assertEqual(self.call()['time_h'], 2)
            self.assertEqual(self.call({'view': 'time_slice', 'time_h': 1})['time_h'], 1)
            self.assertEqual(self.call({'time_h': 0})['time_h'], 0)
            self.assertFalse(self.call({'view': 'simulation_current'})['has_inundation'])
            envelope = self.call({'view': 'envelope'})
            self.assertIsNone(envelope['analysis_time_at'])
            self.assertTrue(envelope['has_inundation'])
            self.assertTrue(self.call({'view': 'envelope', 'time_h': 1})['blocked'])

    def test_map_current_uses_the_same_frozen_frame_and_forecast(self):
        analysis = capture_analysis_context(self.selected)
        self.selected['hydrodynamic_timeline']['current_hydrodynamic_time_h'] = 1
        with analysis_scope(analysis):
            for view, expected in [('current', 2), ('simulation_current', 0)]:
                filters = {'view': view}
                args = {'objects': [{'object_type': 'InundationForecastCell', 'filters': filters}]}
                self.assertEqual(normalize_analysis_tool({'tool_name': 'ui_show_objects', 'args': args}).action, 'allow')
                self.assertEqual(args['objects'][0]['filters'], {'forecast_id': 'v001', 'view': 'time_slice', 'time_h': expected})

    def test_map_timeline_defaults_and_automatic_events_remain_distinct(self):
        with analysis_scope(capture_analysis_context(self.selected)):
            args = {'objects': [{'object_type': 'InundationForecastCell'}]}
            self.assertEqual(normalize_analysis_tool({'tool_name': 'ui_show_objects', 'args': args}).action, 'allow')
            self.assertNotIn('filters', args['objects'][0])
            automatic = {'objects': [{'object_type': 'HydrodynamicGridCell', 'filters': {'forecast_id': 'latest', 'view': 'timeline'}}]}
            normalize_analysis_tool({'tool_name': 'ui_show_objects', 'args': automatic, 'session_id': 'event-1'})
            self.assertEqual(automatic['objects'][0]['filters']['forecast_id'], 'latest')

    def test_map_can_browse_forecasts_created_after_the_question(self):
        analyses = [capture_analysis_context(self.selected)]
        self.manager.begin_session()
        analyses.append(capture_analysis_context({}))
        for analysis in analyses:
            with analysis_scope(analysis):
                for filters in ({'forecast_id': 'latest', 'view': 'timeline'},
                                {'forecast_id': 'v002', 'view': 'time_slice', 'time_h': 1},
                                {'forecast_id': 'v002', 'view': 'envelope'}):
                    args = {'objects': [{'object_type': 'InundationForecastCell', 'filters': dict(filters)}]}
                    self.assertEqual(normalize_analysis_tool({'tool_name': 'ui_show_objects', 'args': args}).action, 'allow')
                    self.assertEqual(args['objects'][0]['filters'], filters)

    def test_map_forecast_validation_does_not_affect_ordinary_objects_or_partially_rewrite_batch(self):
        with analysis_scope(capture_analysis_context(self.selected)):
            args = {'objects': [{'object_type': 'Road', 'object_ids': ['1']},
                                {'object_type': 'InundationForecastCell', 'filters': {'view': 'current'}},
                                {'object_type': 'InundationForecastCell', 'filters': {'forecast_id': 'v002', 'view': 'current'}}]}
            before = json.dumps(args)
            self.assertEqual(normalize_analysis_tool({'tool_name': 'ui_show_objects', 'args': args}).action, 'block')
            self.assertEqual(json.dumps(args), before)
        self.manager.begin_session()
        with analysis_scope(capture_analysis_context({})):
            args = {'objects': [{'object_type': 'Road'}, {'object_type': 'HydrodynamicGridCell'}]}
            self.assertEqual(normalize_analysis_tool({'tool_name': 'ui_show_objects', 'args': args}).action, 'allow')

    def test_all_slice_tools_and_full_sequence_tools_share_version(self):
        with analysis_scope(capture_analysis_context(self.selected)):
            for name in SLICE_TOOLS | {'analyze_latest_evacuation_time', 'assess_flood_emergency'}:
                args = {}
                self.assertEqual(normalize_analysis_tool({'tool_name': name, 'args': args}).action, 'allow')
                self.assertEqual(args['forecast_id'], 'v001')
                if name in SLICE_TOOLS:
                    self.assertEqual(args['time_h'], 2)
                    self.assertEqual(args['view'], 'time_slice')
                else:
                    self.assertNotIn('time_h', args)

    def test_dispatch_trial_freezes_t1_and_baseline_without_redefining_t0(self):
        analysis = capture_analysis_context(self.selected)
        self.selected['hydrodynamic_timeline']['current_hydrodynamic_time_h'] = 1
        with analysis_scope(analysis):
            args = {'settings': {'mode': 'OUTFLOW', 'target_outflow_m3s': 20}}
            self.assertEqual(normalize_analysis_tool({'tool_name': 'simulate_longtan_dispatch', 'args': args}).action, 'allow')
            self.assertEqual(args['forecast_id'], 'v001')
            self.assertEqual(args['time_h'], 2)
            self.assertNotIn('initial_level_m', args['settings'])
            self.assertNotIn('view', args)
            query = {}
            self.assertEqual(normalize_analysis_tool({'tool_name': 'get_longtan_dispatch_plan', 'args': query}).action, 'allow')
            self.assertEqual(query, {'forecast_id': 'v001'})
            wrong = {'forecast_id': 'v002'}
            self.assertEqual(normalize_analysis_tool({'tool_name': 'simulate_longtan_dispatch', 'args': wrong}).action, 'block')

    def test_dispatch_tools_are_registered_for_user_trials_only(self):
        tool = self.harness.tools.get('simulate_longtan_dispatch')
        self.assertIsNotNone(tool)
        self.assertEqual(tool.policy.timeout_seconds, 360)
        self.assertFalse(tool.policy.worker_allowed)
        self.assertFalse(tool.policy.idempotent)
        self.assertFalse(tool.requires_confirmation)
        self.assertNotIn('initial_level_m', tool.parameters['properties']['settings']['properties'])
        self.assertEqual(tool.parameters['properties']['object_ids']['items'], {'type': 'string'})
        self.assertIsNone(self.harness.tools.get('apply_longtan_dispatch'))
        for policy in self.ontology.event_policies.values():
            self.assertNotIn('simulate_longtan_dispatch', policy.allowed_tools)

    def test_moving_timeline_latest_pointer_and_clock_does_not_move_request(self):
        analysis = capture_analysis_context(self.selected)
        self.selected['hydrodynamic_timeline']['current_hydrodynamic_time_h'] = 1
        self.copy_version('v002')
        self.write_json(self.root / 'forecasts/latest.json', {'forecast_id': 'v002'})
        np.save(self.root / 'forecasts/v002/depth_series.npy', np.zeros((3, 2), dtype=np.float32))
        self.start(9)
        with analysis_scope(analysis):
            self.assertEqual(self.call()['time_h'], 2)
            self.assertEqual(self.call()['forecast_version'], 'v001')
            self.assertTrue(self.call()['has_inundation'])
            self.assertEqual(self.call({'view': 'simulation_current'})['time_h'], 0)
            self.assertTrue(self.call({'forecast_id': 'v002'})['blocked'])

    def test_cached_results_are_revalidated_when_input_or_workspace_changes(self):
        with analysis_scope(capture_analysis_context(self.selected)):
            self.assertTrue(self.call()['has_inundation'])
            self.write_json(self.root / 'forecasts/v001/forecast.json', {**self.metadata, 'forecast_input_id': 'old'})
            self.assertTrue(self.call()['blocked'])
        analysis = capture_analysis_context(self.selected)
        self.manager.begin_session()
        with analysis_scope(analysis):
            self.assertTrue(self.call()['blocked'])

    def test_invalid_map_context_and_missing_exact_frame_never_fall_back(self):
        for change in ({'forecast_version': ''}, {'current_hydrodynamic_time_h': None},
                       {'current_hydrodynamic_time_h': 1.25},
                       {'current_hydrodynamic_valid_at': '2026-07-03T09:00:00+08:00'}):
            selection = self.selection(2)
            selection['hydrodynamic_timeline'].update(change)
            with analysis_scope(capture_analysis_context(selection)):
                self.assertTrue(self.call()['blocked'], change)
        selection = {**self.selected, 'workspace_id': 'old'}
        with analysis_scope(capture_analysis_context(selection)):
            self.assertTrue(self.call()['blocked'])
        self.assertEqual(get_flood_status('time_slice', 1.25)['status'], 'time_unavailable')

    def test_without_selected_frame_current_uses_frozen_simulation_time(self):
        for selected in ({}, {'hydrodynamic_timeline': {'active': False}},
                         {'hydrodynamic_timeline': {'active': True, 'mode': 'none'}},
                         {'hydrodynamic_timeline': {'active': True, 'mode': 'envelope', 'forecast_version': 'v001'}}):
            with analysis_scope(capture_analysis_context(selected)):
                self.assertEqual(self.call()['time_h'], 0)
        self.manager.update_manifest(status='ready', simulation_time=None)
        with analysis_scope(capture_analysis_context({})):
            self.assertEqual(self.call(session='initial')['status'], 'initial_dry')
            self.start()
            self.assertTrue(self.call(session='initial')['blocked'])

    def test_parallel_tool_batch_carries_scope_and_other_sessions_are_isolated(self):
        calls = [SimpleNamespace(id=str(i), function=SimpleNamespace(name='get_flood_status')) for i in range(2)]
        state = SimpleNamespace(session_id='parallel', messages=[], cache_namespace='parallel',
                                genai_trace_id='', genai_root_span_id='', genai_parent_span_id='')
        with analysis_scope(capture_analysis_context(self.selected)):
            results = ToolExecutor(self.harness).execute_batch([(call, {}) for call in calls], state)
        self.assertTrue(all(json.loads(result.content)['time_h'] == 2 for _, _, result in results))
        contexts = [capture_analysis_context(self.selection(hour)) for hour in (1, 2)]
        def request(i):
            with analysis_scope(contexts[i]):
                return self.call(session=f'isolated-{i}')['time_h']
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(request, (0, 1))), [1, 2])
        self.assertEqual(self.call(session='automatic')['time_h'], 0)
        with analysis_scope(contexts[0]):
            self.assertEqual(self.call(session='event-1')['time_h'], 0)

    def test_chat_and_pending_question_keep_original_analysis(self):
        harness = self.harness
        captured = []
        class Agent:
            pending = None
            def pending_tool_name(self, session_id): return self.pending
            def chat_stream(self, message, session_id, allowed_tools=None):
                captured.append(message)
                captured.append(json.loads(harness.execute_tool('get_flood_status', {}, session_id=session_id).content))
                self.pending = 'ask_user'
                yield TextEvent(content='请补充范围')
            def confirm_tool(self, session_id, approved, answer=None):
                captured.append(json.loads(harness.execute_tool('get_flood_status', {}, session_id=session_id).content))
                self.pending = None
                yield TextEvent(content='完成')
        service = FloodChatService(Agent(), self.ontology, AgentSideEffects([]))
        first = AgentRun('first', 'session', '总结当前情况', self.selected)
        self.selected['hydrodynamic_timeline']['current_hydrodynamic_time_h'] = 1
        service.stream_chat(first)
        service.stream_chat(AgentRun('second', 'session', 'X706', self.selected))
        self.assertIn('本轮分析时刻', captured[0])
        self.assertEqual([captured[1]['time_h'], captured[2]['time_h']], [2, 2])
        self.assertEqual(service._pending_analysis, {})

    def test_review_and_compare_propagate_forecast_to_data_reads(self):
        from domains.flood.runtime import evacuation_options
        from domains.flood.runtime.object_sets import save_object_set
        from tests.test_impact_analysis import StaticResolver
        repo = StaticResolver({'EvacuationRoute': [{'evacuation_route_id': 'r', 'geometry': {
            'type': 'LineString', 'coordinates': [[111, 24], [111.01, 24]]}}],
            'EvacuationUnit': [{'evacuation_unit_id': 'u', 'longitude': 111, 'latitude': 24, 'population': 1}],
            'EvacuationSite': [{'evacuation_site_id': 's', 'longitude': 111, 'latitude': 24, 'capacity_person': 10}]})
        repo.query_by_id = lambda kind, ident: next((row for row in repo.query(kind, {}) if row.get({'EvacuationRoute': 'evacuation_route_id', 'EvacuationUnit': 'evacuation_unit_id', 'EvacuationSite': 'evacuation_site_id'}[kind]) == ident), None)
        service = FloodRuntimeService(repo)
        with patch.object(evacuation_options, 'query_forecast_cells', return_value=[]) as cells:
            result = service.review_route('r', 'time_slice', 2, 'v001')
            self.assertTrue(result['passable'])
            self.assertEqual(cells.call_args.args[0], {'forecast_id': 'v001', 'time_h': 2})
        selected = save_object_set('EvacuationSite', ['s'])
        with patch.object(evacuation_options, 'analyze_inundation_impacts', return_value={}) as impacts:
            result = service.compare_evacuation_sites('u', selected['object_set_id'], view='time_slice', time_h=2, forecast_id='v001')
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(impacts.call_args.kwargs['forecast_id'], 'v001')
            self.assertEqual(impacts.call_args.kwargs['time_h'], 2)
