import json
import unittest
from pathlib import Path

from oag.harness import Harness
from oag.ontology.loader import load_domain
from oag.runtime import ToolUseContext
from server.presentation.map_tools import register_map_tools
from server.presentation.directive_tools import register_directive_tools


class ToolRuntimePolicyTests(unittest.TestCase):
    def setUp(self):
        ontology, repository, registry = load_domain(Path(__file__).resolve().parents[1] / 'domains/flood')
        self.harness = Harness(ontology, repository, registry, None, 'test')

    def test_inoperative_tools_are_not_exposed_or_recommended(self):
        register_map_tools(self.harness.tools, None, self.harness.ontology)
        register_directive_tools(self.harness.tools, self.harness.ontology)
        names = {item['function']['name'] for item in self.harness.build_tools()}
        self.assertEqual(len(names), 30)
        self.assertFalse(names & {'mutate', 'apply_rule', 'apply_rule_batch', 'dispatch_workers'})
        self.assertNotIn('使用 apply_rule/apply_rule_batch 工具应用规则', self.harness.build_system_prompt())

    def test_explicit_computations_execute_twice_without_confirmation_or_worker_access(self):
        for name, args in [('run_flood_forecast', {'force': True}), ('plan_route', {}),
                           ('simulate_longtan_dispatch', {'settings': {'mode': 'RULE'}}),
                           ('simulate_flood_scenario', {'rainfall_multiplier': 2})]:
            with self.subTest(tool=name):
                calls = []
                tool = self.harness.tools.get(name)
                def handler(args):
                    calls.append(dict(args))
                    return json.dumps({'status': 'completed', 'sequence': len(calls)})
                tool.handler = handler
                self.assertFalse(self.harness.execute_tool(name, dict(args)).needs_confirmation)
                second = self.harness.execute_tool(name, dict(args))
                self.assertEqual(json.loads(second.content)['sequence'], 2)
                blocked = self.harness.execute_tool(name, dict(args), context=ToolUseContext(source='worker'))
                self.assertTrue(blocked.blocked)
                self.assertEqual(len(calls), 2)

    def test_new_forecast_invalidates_previous_query_cache(self):
        value = {'version': 'v001'}
        self.harness.tools.get('query').handler = lambda args: json.dumps(value)
        def calculate(args):
            value['version'] = 'v002'
            return json.dumps(value)
        self.harness.tools.get('run_flood_forecast').handler = calculate
        first = self.harness.execute_tool('query', {'object_type': 'FloodForecast'})
        self.harness.execute_tool('run_flood_forecast', {'force': True})
        second = self.harness.execute_tool('query', {'object_type': 'FloodForecast'})
        self.assertEqual(json.loads(first.content)['version'], 'v001')
        self.assertEqual(json.loads(second.content)['version'], 'v002')
