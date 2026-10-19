"""Assemble a generic native Codex runtime and optional tool extensions."""
from __future__ import annotations

import asyncio

from .catalog import digest
from .console.server import ConsoleApp
from .events import EventLog
from .providers import ProviderSet
from .host import AgentHost
from .process import ManagedProcess
from .registry import ToolRegistry
from .state import AtomicJsonStore, OperationJournal, payload_digest
from .release import verify_release
from .stage0 import GateError, file_sha256


def build_host(config, *, development=False):
    catalog = config.catalog()
    providers = ProviderSet()
    store = AtomicJsonStore(config.state_dir)
    from .console.adapters.sqlite import SqliteConsoleObservationStore
    events = EventLog(SqliteConsoleObservationStore(config.state_dir / 'console.sqlite3')
                      if config.raw.get('console', {}).get('storage', {}).get('backend') == 'sqlite' else None)
    process = ManagedProcess(config, observer=events.observer, diagnostic=events.diagnostic)
    registry = ToolRegistry(catalog, OperationJournal(store))
    host = None
    accepted = {}

    async def startup_check():
        if development:
            from .local import source_manifest
            manifest = await asyncio.to_thread(source_manifest, config)
        else:
            manifest = await asyncio.to_thread(verify_release, config)
        providers.load(config.providers, catalog)
        registry.bind_handlers(providers.handlers)
        accepted.update(manifest)
        keys = ("runtime_binary_sha256", "runtime_compatibility_sha256",
                "effective_config_sha256", "agent_config_sha256", "extensions_sha256")
        host.bindings.update({key: manifest[key] for key in keys})

    async def start_process():
        manifest = accepted
        client = await process.start(server_request=host.handle_tool_call,
            notification=host.handle_notification, on_failure=host.runtime_failed)
        saved_roots = store.read('console/skill-roots.json') if development else None
        if saved_roots is not None:
            await client.peer.request('skills/extraRoots/set', {'extraRoots': saved_roots['roots']})
            host.skill_roots = saved_roots['roots']
        observed = await client.read_config(str(config.workspace_dir))
        if development:
            manifest['effective_config_sha256'] = digest(observed)
            host.bindings['effective_config_sha256'] = manifest['effective_config_sha256']
        if digest(observed) != manifest["effective_config_sha256"]:
            raise GateError("effective runtime config or config sources changed")
        if await asyncio.to_thread(images.initialize, observed['config']):
            await client.peer.request('skills/list', {'cwds': [str(config.workspace_dir)], 'forceReload': True})
        if development:
            from .runtime_upgrade import migrate_source_sessions
            await asyncio.to_thread(migrate_source_sessions, config, store, host.bindings)
        return client

    async def check_admission():
        if file_sha256(config.codex_home / 'config.toml') != accepted['codex_config_sha256']:
            raise GateError('Codex configuration changed')
        observed = await process.client.read_config(str(config.workspace_dir))
        if digest(observed) != accepted['effective_config_sha256']:
            raise GateError('effective configuration changed')

    async def configuration_changed():
        observed = await process.client.read_config(str(config.workspace_dir))
        accepted['codex_config_sha256'] = file_sha256(config.codex_home / 'config.toml')
        accepted['effective_config_sha256'] = digest(observed)
        host.bindings['effective_config_sha256'] = digest(observed)
        for session in list(host._sessions.values()):
            session['effective_config_sha256'] = digest(observed)
            host._save(session)

    def reconcile():
        providers.reconcile(registry, config_digest=payload_digest(host.bindings))

    host = AgentHost(registry=registry, event_log=events, store=store,
                     client_factory=start_process, reconciler=reconcile,
                     principal_id=config.principal_id, actions=set(config.actions),
                     max_active_turns=config.max_active_turns, workspace_dir=config.workspace_dir,
                     client_stop=process.stop, extensions_close=providers.close, admission_check=check_admission, startup_check=startup_check,
                     configuration_changed=configuration_changed if development else None)
    host.skill_roots = [str(root) for root in config.skill_roots]
    from .image_generation import ImageGenerationSettings
    images = ImageGenerationSettings(config, editable=development)
    integrations = None
    if config.raw.get('app_integrations', {}).get('enabled'):
        from .app_integrations import AppIntegrations
        integrations = AppIntegrations(host, events, config.raw['app_integrations'])
    return host, ConsoleApp(host, events, image_settings=images, integrations=integrations)
