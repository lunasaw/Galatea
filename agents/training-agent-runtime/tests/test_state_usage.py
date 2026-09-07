import importlib.util
import tempfile
import unittest
from pathlib import Path

class StateUsageTests(unittest.TestCase):
    def test_state_atomic_roundtrip_and_exclusive_lock(self):
        self.assertIsNotNone(importlib.util.find_spec('training_agent.state'), 'state implementation missing')
        from training_agent.state import Store
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            with store.lock():
                with self.assertRaises(RuntimeError):
                    with Store(Path(directory)).lock(): pass
                store.save('campaign', {'schema_version': 1, 'campaign_id': 'campaign'})
                self.assertEqual(store.load('campaign')['campaign_id'], 'campaign')
                store.path('campaign').write_text('{broken')
                with self.assertRaises(ValueError): store.load('campaign')
                with self.assertRaises(ValueError): store.path('../escape')

    def test_cumulative_usage_delta_and_unknown(self):
        self.assertIsNotNone(importlib.util.find_spec('training_agent.runtime.usage'), 'usage implementation missing')
        from training_agent.runtime.usage import usage_delta
        self.assertEqual(usage_delta({'thread_id':'a','total_tokens':100}, {'thread_id':'a','total_tokens':160}),60)
        self.assertEqual(usage_delta({'thread_id':'a','total_tokens':160}, {'thread_id':'a','total_tokens':160}),0)
        for after in (None, {'thread_id':'a','total_tokens':90}, {'thread_id':'b','total_tokens':160}):
            self.assertIsNone(usage_delta({'thread_id':'a','total_tokens':100}, after))

    def test_symlinked_campaign_cannot_escape_state_volume(self):
        from training_agent.state import Store
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            store=Store(root/'state')
            (store.root/'campaigns').mkdir()
            outside=root/'outside';outside.mkdir()
            (store.root/'campaigns'/'c').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(ValueError):
                store.save('c',{'schema_version':1,'campaign_id':'c'})
            self.assertFalse((outside/'state.json').exists())
