from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from domains.flood.runtime.workspace import WorkspaceManager
from domains.flood.runtime.repository import FloodRepository
from domains.flood.runtime.service import FloodRuntimeService
from domains.flood.runtime.object_sets import read_object_set, save_object_set
from domains.flood.runtime.route_store import save_planned_route, read_planned_routes
from server.directives import DirectiveStore
from server.presentation.directive_tools import build_directive_editor_result, tool_result_to_directive_event
from server.presentation.map_actions import MapActionBuilder
from tests.test_map_actions import ONTOLOGY


class WorkflowEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.manager = WorkspaceManager(Path(self.directory.name))
        self.manager.begin_session()
        patched = patch('domains.flood.runtime.workspace.WORKSPACES', self.manager)
        patched.start(); self.addCleanup(patched.stop)
        self.repo = FloodRepository()
        self.service = FloodRuntimeService(self.repo)

    def test_query_filter_compare_and_display_share_a_bounded_set(self):
        nearby = self.service.find_nearby_objects('EvacuationUnit', '43', radius_m=1000, limit=10)
        self.assertEqual(nearby['page_set']['count'], 10)
        self.assertGreater(nearby['matching_set']['count'], 10)
        filtered = self.service.refine_object_set(nearby['matching_set']['object_set_id'], {'capacity_person__gt': 200})
        source = read_object_set(nearby['matching_set']['object_set_id'])
        result = read_object_set(filtered['object_set_id'])
        self.assertTrue(set(result['object_ids']) <= set(source['object_ids']))
        self.assertIn('shelter_235', result['object_ids'])
        self.assertEqual(filtered['parent_set_id'], source['object_set_id'])
        comparison = self.service.compare_evacuation_sites('43', filtered['object_set_id'])
        self.assertEqual(comparison['required_capacity'], 163)
        self.assertFalse(comparison['route_checked'])
        self.assertIn(comparison['recommended_site_id'], result['object_ids'])
        builder = MapActionBuilder(ONTOLOGY, self.repo)
        shown = json.loads(builder.show_objects({'objects': [{'object_type': 'EvacuationSite', 'object_set_id': filtered['object_set_id']}]}, ['EvacuationSite']))
        self.assertEqual(set(shown['map_actions'][0]['object_ids']), set(result['object_ids']))
        empty = self.service.refine_object_set(filtered['object_set_id'], {'capacity_person__gt': 999999})
        shown = json.loads(builder.show_objects({'objects': [{'object_type': 'EvacuationSite', 'object_set_id': empty['object_set_id']}]}, ['EvacuationSite']))
        self.assertEqual(shown['map_actions'], [])
        replacement = json.loads(builder.show_objects({'objects': [filtered['map_update']]}, ['EvacuationSite']))
        self.assertEqual(set(replacement['map_actions'][0]['replace_object_ids']), set(source['object_ids']))
        empty_replacement = json.loads(builder.show_objects({'objects': [empty['map_update']]}, ['EvacuationSite']))
        self.assertEqual(empty_replacement['map_actions'][0]['type'], 'hide_objects')
        self.assertEqual(set(empty_replacement['map_actions'][0]['object_ids']), set(result['object_ids']))
        self.manager.begin_session()
        with self.assertRaises(ValueError): read_object_set(filtered['object_set_id'])

    def test_scope_cannot_expand_when_ids_and_filters_intersect(self):
        builder = MapActionBuilder(ONTOLOGY, self.repo)
        shown = json.loads(builder.show_objects({'objects': [{'object_type':'EvacuationSite', 'object_ids':['shelter_235'], 'filters':{'evacuation_site_id__in':['shelter_232']}}]}, ['EvacuationSite']))
        self.assertIn('error', shown)

    def test_explicit_shelter_candidates_do_not_require_a_saved_set(self):
        ids = ['shelter_235', 'shelter_232']
        result = self.service.compare_evacuation_sites('43', object_ids=ids)
        self.assertEqual({row['object_id'] for row in result['candidates']}, set(ids))
        self.assertIn(result['recommended_site_id'], ids)
        self.assertIn('error', self.service.compare_evacuation_sites('43'))
        self.assertIn('error', self.service.compare_evacuation_sites('43', result['eligible_set']['object_set_id'], object_ids=ids))
        empty = self.service.compare_evacuation_sites('43', object_ids=[])
        self.assertEqual(empty['candidates'], [])
        self.assertIsNone(empty['recommended_site_id'])

    def test_query_and_recommendation_survive_set_storage_failure(self):
        with patch('domains.flood.runtime.service.save_object_set', side_effect=OSError('disk full')):
            result = self.service.find_nearby_objects('EvacuationUnit', '43', radius_m=1000)
        self.assertIsNone(result['matching_set'])
        self.assertEqual(len(result['matching_object_ids']), result['total_matched'])
        self.assertGreater(result['returned_count'], 0)
        with patch('domains.flood.runtime.evacuation_options.save_object_set', side_effect=OSError('disk full')):
            recommendation = self.service.compare_evacuation_sites('43', object_ids=result['object_ids'])
        self.assertEqual(recommendation['status'], 'completed')
        self.assertIsNone(recommendation['eligible_set'])
        self.assertEqual(len(recommendation['candidates']), result['returned_count'])

    def test_map_set_filters_create_exact_subset_and_empty_replacement(self):
        original = save_object_set('EvacuationSite', ['shelter_232', 'shelter_235'])
        builder = MapActionBuilder(ONTOLOGY, self.repo)
        args = {'objects': [{'object_type': 'EvacuationSite', 'object_set_id': original['object_set_id'],
                            'filters': {'evacuation_site_id': 'shelter_235'}}]}
        result = json.loads(builder.show_objects(args, ['EvacuationSite']))
        selection = result['selections'][0]
        self.assertEqual(read_object_set(selection['object_set_id'])['object_ids'], ['shelter_235'])
        self.assertEqual(result['map_actions'][0]['object_ids'], ['shelter_235'])
        args['objects'][0].update(replace_object_set_id=original['object_set_id'], filters={'capacity_person__gt': 999999})
        result = json.loads(builder.show_objects(args, ['EvacuationSite']))
        self.assertEqual(result['map_actions'][0]['type'], 'hide_objects')
        self.assertEqual(set(result['map_actions'][0]['object_ids']), {'shelter_232', 'shelter_235'})

    def test_describe_respects_both_saved_scope_and_filters(self):
        from oag.harness import Harness
        from oag.ontology.loader import load_domain
        from server.chat.agent_factory import configure_agent_query_tools
        ontology, repository, registry = load_domain(Path(__file__).resolve().parents[1] / 'domains/flood')
        harness = Harness(ontology, repository, registry, None, 'test')
        configure_agent_query_tools(harness)
        selected = save_object_set('EvacuationSite', ['shelter_235'])
        args = {'object_type': 'EvacuationSite', 'column': 'capacity_person', 'object_set_id': selected['object_set_id']}
        result = json.loads(harness.execute_tool('describe', args).content)
        self.assertEqual(result['count'], 1)
        empty = json.loads(harness.execute_tool('describe', {**args, 'filters': {'evacuation_site_id__in': ['shelter_232']}}).content)
        self.assertEqual(empty['count'], 0)

    def test_route_versions_remain_available_for_independent_review_after_replanning(self):
        first = {'evacuation_route_id':'planned_first','start_object_type':'EvacuationUnit','start_object_id':'43',
                 'origin_unit_id':'43','destination_site_id':'shelter_235','profile':'foot','blocked_depth_m':0.15,
                 'geometry':json.dumps({'type':'LineString','coordinates':[[111.15,24.38],[111.16,24.38]]})}
        save_planned_route(first)
        save_planned_route({**first,'evacuation_route_id':'planned_second'})
        self.assertEqual([row['evacuation_route_id'] for row in read_planned_routes()], ['planned_second'])
        self.assertEqual(self.repo.query_by_id('EvacuationRoute','planned_first')['evacuation_route_id'],'planned_first')
        self.assertTrue(self.service.review_route('planned_first')['passable'])
        self.manager.update_manifest(status='paused',simulation_time='2026-07-03T08:00:00+08:00')
        self.assertIsNone(self.service.review_route('planned_first')['passable'])

    def test_draft_and_manual_issuance_work_without_forecast_or_route_evidence(self):
        self.manager.update_manifest(status='paused', simulation_time='2026-07-03T08:00:00+08:00')
        self.assertIsNone(self.service.review_route('40')['passable'])
        result = build_directive_editor_result({
            "title": "新民村转移准备", "content": "请组织人员核查转移路线与安置点。",
            "recipients": "新民村委会",
        })
        event = tool_result_to_directive_event(result)
        self.assertEqual(event["type"], "directive_draft")
        draft = event["draft"]
        self.assertNotIn("basis", draft)
        store = DirectiveStore(self.manager)
        self.assertEqual(store.list_issued()["directives"], [])
        self.manager.update_manifest(simulation_time='2026-07-03T09:00:00+08:00')
        issued = store.issue({**draft, "workspace_id": self.manager.active_id}, {
            "observed_at": "2026-07-03T09:00:00+08:00", "forecast_version": 2,
        })
        self.assertEqual(issued["content"], draft["content"])
        self.assertEqual(issued["simulation_time"], "2026-07-03T09:00:00+08:00")
        self.assertEqual(issued["forecast_version"], "v002")
        self.assertNotIn("basis", issued)

    def test_review_preserves_an_explicit_zero_depth_threshold(self):
        from domains.flood.runtime import evacuation_options
        row = {"evacuation_route_id":"zero", "blocked_depth_m":0,
               "geometry":json.dumps({"type":"LineString","coordinates":[[111.0,24.0],[111.01,24.0]]})}
        save_planned_route(row)
        wet = {"depth_m":0.1, "geometry":{"type":"Polygon","coordinates":[[[110.99,23.99],[111.02,23.99],[111.02,24.01],[110.99,24.01],[110.99,23.99]]]}}
        with patch.object(evacuation_options, "resolve_routing_context", return_value={"available":True,"constraint_source":"forecast","time_h":0}), patch.object(evacuation_options, "query_forecast_cells", return_value=[wet]):
            self.assertFalse(self.service.review_route("zero")["passable"])

    def test_full_domain_result_survives_trace_preview_and_mailbox_is_released(self):
        from server.chat.side_effects import AgentSideEffects
        effects = AgentSideEffects([])
        effects.begin_domain_results('chat')
        full = {'analysis_scope': {'requested_object_ids': [f'site_{i}' for i in range(100)]}, 'impacts': []}
        effects.capture_tool_event({'session_id':'chat','tool_name':'analyze_inundation_impacts','result':json.dumps(full)})
        self.assertEqual(effects.pop_domain_results('chat')[0]['result'], full)
        self.assertEqual(effects.pop_domain_results('chat'), [])
        effects.end_domain_results('chat')
        effects.capture_tool_event({'session_id':'chat','tool_name':'analyze_inundation_impacts','result':json.dumps(full)})
        self.assertEqual(effects.pop_domain_results('chat'), [])

    def test_full_polygon_route_check_catches_small_crossings_and_respects_holes(self):
        from domains.flood.runtime.route_safety import build_flood_avoidance_areas, path_intersects_areas, point_in_areas
        # This 2m-wide wet patch lies between the old 20m sample positions.
        ring = [[111.00004,24.00000],[111.00006,24.00000],[111.00006,24.00010],[111.00004,24.00010],[111.00004,24.00000]]
        areas = build_flood_avoidance_areas([{'depth_m':1,'geometry':{'type':'Polygon','coordinates':[ring]}}],0.3)
        self.assertTrue(path_intersects_areas([[111,24.00005],[111.0002,24.00005]],areas['feature_collection']))
        exterior = [[0,0],[1,0],[1,1],[0,1],[0,0]]
        hole = [[0.2,0.2],[0.2,0.8],[0.8,0.8],[0.8,0.2],[0.2,0.2]]
        areas = build_flood_avoidance_areas([{'depth_m':1,'geometry':{'type':'Polygon','coordinates':[exterior,hole]}}],0.3)
        self.assertFalse(point_in_areas((0.5,0.5),areas['feature_collection']))
        self.assertFalse(path_intersects_areas([[0.3,0.3],[0.7,0.7]],areas['feature_collection']))
