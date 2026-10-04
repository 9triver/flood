import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from server.events.runtime import EventRuntime
from domains.flood.runtime.workspace import WorkspaceManager


def payload(chunk):
    return json.loads(next(line[6:] for line in chunk.decode().splitlines() if line.startswith('data: ')))


class EventNotificationTest(unittest.TestCase):
    def test_replay_flag_and_chain_identity_survive_reconnect_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory))
            manager.create()
            with patch('domains.flood.runtime.workspace.WORKSPACES', manager), patch('server.events.runtime.WORKSPACES', manager):
                runtime = EventRuntime(SimpleNamespace(agent=None))
                runtime.ensure_started = lambda: None
                runtime._processing_event_id = 'forecast-1'
                runtime._append_output('agent_trace', {'tag':'TEXT','label':'智能体结论','detail':'first'})
                stream = runtime.stream(1)
                first = payload(next(stream))
                self.assertTrue(first['replayed'])
                runtime._append_output('agent_trace', {'tag':'TEXT','label':'智能体结论','detail':'followup'})
                live = payload(next(stream))
                self.assertFalse(live['replayed'])
                self.assertEqual(live['notification_key'], first['notification_key'])
                self.assertNotEqual(live['output_id'], first['output_id'])
                reconnect = runtime.stream(1)
                self.assertTrue(payload(next(reconnect))['replayed'])
                replay = payload(next(reconnect))
                self.assertTrue(replay['replayed'])
                self.assertEqual(replay['output_id'], live['output_id'])
                runtime.outputs.clear()
                runtime._processing_event_id = 'forecast-2'
                runtime._append_output('agent_trace', {'tag':'TEXT','label':'智能体结论','detail':'new forecast'})
                fresh = payload(next(stream))
                self.assertFalse(fresh['replayed'])
                self.assertEqual(fresh['notification_key'], 'forecast-2')
                stream.close()
                reconnect.close()
                self.assertNotIn('replayed', runtime.outputs[0]['data'])
