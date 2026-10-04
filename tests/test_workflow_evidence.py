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
from server.directive_basis import build_directive_basis, validate_directive_basis
from server.directives import DirectiveStore
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

    def test_route_versions_and_issued_evidence_survive_replanning(self):
        first = {'evacuation_route_id':'planned_first','start_object_type':'EvacuationUnit','start_object_id':'43',
                 'origin_unit_id':'43','destination_site_id':'shelter_235','profile':'foot','blocked_depth_m':0.15,
                 'geometry':json.dumps({'type':'LineString','coordinates':[[111.15,24.38],[111.16,24.38]]})}
        save_planned_route(first)
        basis = build_directive_basis('planned_first')
        self.assertTrue(basis['route_review']['passable'])
        directive = DirectiveStore(self.manager).issue({'title':'转移准备','content':'按已复核路线做好准备','recipients':'同古镇政府',
            'workspace_id':self.manager.active_id,'basis':basis}, {})
        save_planned_route({**first,'evacuation_route_id':'planned_second'})
        self.assertEqual([row['evacuation_route_id'] for row in read_planned_routes()], ['planned_second'])
        self.assertEqual(self.repo.query_by_id('EvacuationRoute','planned_first')['evacuation_route_id'],'planned_first')
        self.assertTrue(self.service.review_route('planned_first')['passable'])
        validate_directive_basis(basis)
        self.manager.update_manifest(status='paused',simulation_time='2026-07-03T08:00:00+08:00')
        with self.assertRaisesRegex(ValueError,'依据'): validate_directive_basis(basis)
        self.assertIsNone(self.service.review_route('planned_first')['passable'])
        self.assertEqual(DirectiveStore(self.manager).list_issued()['directives'][0], directive)

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
