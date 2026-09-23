"""Assembly helpers for a single Agent Host instance.

The module intentionally accepts an already-constructed Galatea Service. This
keeps credentials and backend construction in deployment code rather than in
the Console or model process.
"""
from __future__ import annotations

import asyncio

from .catalog import Catalog, digest
from .console.server import ConsoleApp
from .events import EventLog
from .galatea_adapter import GalateaAdapter
from .host import AgentHost
from .process import ManagedProcess
from .registry import ToolRegistry
from .state import AtomicJsonStore, OperationJournal
from .release import verify_release
from .stage0 import GateError, file_sha256


def build_host(config, service, principal, *, console_token: str, allowed_origins: set[str]):
    if (principal.principal_id, principal.project_ids, principal.campaign_ids, principal.actions) != (
            config.principal_id, config.project_ids, config.campaign_ids, config.actions):
        raise GateError("backend principal differs from Host configuration")
    catalog = Catalog.load(config.contracts.parent)
    adapter = GalateaAdapter(service, principal=principal)
    store = AtomicJsonStore(config.state_dir)
    events = EventLog(config.event_dir)
    process = ManagedProcess(config)
    registry = ToolRegistry(catalog, OperationJournal(store),
                            adapter.handlers(catalog.schemas))
    host = None
    accepted = {}

    async def startup_check():
        manifest = await asyncio.to_thread(verify_release, config)
        accepted.update(manifest)
        keys = ("runtime_binary_sha256", "runtime_compatibility_sha256",
                "effective_config_sha256", "agent_config_sha256")
        host.bindings.update({key: manifest[key] for key in keys})

    async def start_process():
        manifest = accepted
        client = await process.start(server_request=host.handle_tool_call,
            notification=host.handle_notification, on_failure=host.runtime_failed)
        observed = await client.read_config(str(config.workspace_dir))
        if digest(observed) != manifest["effective_config_sha256"]:
            raise GateError("effective runtime config or config sources changed")
        return client

    async def check_admission():
        if file_sha256(config.codex_home / 'config.toml') != accepted['codex_config_sha256']:
            raise GateError('Codex configuration changed')
        observed = await process.client.read_config(str(config.workspace_dir))
        if digest(observed) != accepted['effective_config_sha256']:
            raise GateError('effective configuration changed')

    def reconcile():
        adapter.reconcile_all()
        registry.reconcile_unknown(adapter.observe_receipt)

    host = AgentHost(registry=registry, event_log=events, store=store,
                     client_factory=start_process, reconciler=reconcile,
                     principal_id=principal.principal_id, project_ids=set(principal.project_ids),
                     campaign_ids=set(principal.campaign_ids), actions=set(principal.actions),
                     max_active_turns=config.max_active_turns, workspace_dir=config.workspace_dir,
                     client_stop=process.stop, admission_check=check_admission, startup_check=startup_check)
    return host, ConsoleApp(host, events, token=console_token, allowed_origins=allowed_origins)
