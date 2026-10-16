"""Administrator-configured Python tool extensions; never selected by the model."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import importlib.util
import inspect
import sys
import uuid


@dataclass
class ToolProvider:
    handlers: dict = field(default_factory=dict)
    reconcile: object = None
    observe_receipt: object = None
    close: object = None


class ProviderSet:
    def __init__(self):
        self.providers = []
        self.handlers = {}
        self._modules = []

    def load(self, definitions, catalog):
        try:
            for entry in definitions:
                spec = importlib.util.spec_from_file_location('codex_tool_' + uuid.uuid4().hex, entry['path'])
                if spec is None or spec.loader is None:
                    raise ValueError('provider must be a Python module file')
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                self._modules.append(spec.name)
                spec.loader.exec_module(module)
                provider = getattr(module, entry['factory'])(copy.deepcopy(entry['options']))
                if not isinstance(provider, ToolProvider):
                    raise ValueError('provider factory must return ToolProvider')
                self.providers.append(provider)
                if (not isinstance(provider.handlers, dict) or self.handlers.keys() & provider.handlers.keys()
                        or any(not callable(h) or inspect.iscoroutinefunction(h) for h in provider.handlers.values())):
                    raise ValueError('provider handlers must be unique synchronous callables')
                for hook in (provider.reconcile, provider.observe_receipt, provider.close):
                    if hook is not None and (not callable(hook) or inspect.iscoroutinefunction(hook)):
                        raise ValueError('provider hooks must be synchronous callables')
                self.handlers.update(provider.handlers)
            if self.handlers.keys() != catalog.schemas.keys():
                raise ValueError('providers must implement exactly the configured catalog')
        except BaseException:
            self.close()
            raise

    def reconcile(self, registry, *, config_digest=None):
        for provider in self.providers:
            if provider.reconcile:
                provider.reconcile()
        def observe(identity):
            # Catalog drift cannot turn a receipt from an older extension into
            # evidence for a different tool or configuration.
            if (identity.get('catalog_digest') != registry.catalog.digest or
                    (config_digest is not None and identity.get('config_digest') != config_digest)):
                return None
            for provider in self.providers:
                if identity['tool'] in provider.handlers and provider.observe_receipt:
                    return provider.observe_receipt(identity)
            return None
        registry.reconcile_unknown(observe)

    def close(self):
        providers, self.providers = self.providers, []
        self.handlers = {}
        errors = []
        for provider in reversed(providers):
            if provider.close:
                try:
                    provider.close()
                except Exception as exc:
                    errors.append(exc)
        for name in self._modules:
            sys.modules.pop(name, None)
        self._modules.clear()
        if errors:
            raise RuntimeError('tool provider cleanup failed') from errors[0]
