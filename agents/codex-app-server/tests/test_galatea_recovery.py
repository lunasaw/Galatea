"""Real Galatea domain service with fake external APIs; no training is executed."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(REPO / 'services/galatea-mcp/src'))
sys.path.insert(0, str(REPO / 'services/galatea-mcp/tests'))
import helpers
from codex_agent.catalog import Catalog
from codex_agent.galatea_adapter import GalateaAdapter
from codex_agent.registry import ToolContext, ToolRegistry
from codex_agent.state import AtomicJsonStore, OperationJournal


class AdapterRecoveryTests(helpers.ServiceFixture):
    def test_lost_submit_response_and_host_receipt_crash_never_resubmits(self):
        adapter = GalateaAdapter(self.service, principal=self.principal)
        catalog = Catalog.load(ROOT / 'config/contracts')
        journal = OperationJournal(AtomicJsonStore(self.store.root / 'host'))
        registry = ToolRegistry(catalog, journal, adapter.handlers(catalog.schemas))
        plan = self.plan()
        arguments = {'project_id':'p1','campaign_id':'campaign1','plan_id':plan, 'idempotency_key':'host-key'}
        context = ToolContext('s','t','turn','submit','agent', project_ids=frozenset({'p1'}),
                              campaign_ids=frozenset({'campaign1'}), actions=frozenset({'*'}))
        self.ray.lose_response = True
        first = registry.call(context, 'galatea_submit_job', arguments)
        self.assertTrue(first['ok'], first)
        operation_id = first['data']['operation_id']
        self.assertEqual(first['data']['execution'], 'unknown')
        journal.transition('submit', 'executing')  # Crash after backend commit, before Host receipt.
        restarted = ToolRegistry(catalog, journal, adapter.handlers(catalog.schemas))
        repeated = restarted.call(context, 'galatea_submit_job', arguments)
        self.assertEqual(repeated['error']['state_changed'], 'unknown')
        adapter.reconcile_all()
        observed = self.call('get_operation', operation_id=operation_id)
        self.assertEqual(observed['execution'], 'queued')
        restarted.reconcile_unknown(adapter.observe_receipt)
        recovered = restarted.call(context, 'galatea_submit_job', arguments)
        self.assertTrue(recovered['ok'], recovered)
        self.assertEqual(recovered['data']['operation_id'], operation_id)
        self.assertEqual(len(self.ray.jobs), 1)
