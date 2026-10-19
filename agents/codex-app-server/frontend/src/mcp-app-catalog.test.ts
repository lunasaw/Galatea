import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mcpApps } from './mcp-app-catalog';

test('launch directory exposes model-visible entrypoints, never App-only write tools', () => {
  const tools = {
    open: { _meta: { ui: { resourceUri: 'ui://workbench' } } },
    hiddenWrite: { _meta: { ui: { resourceUri: 'ui://workbench', visibility: ['app'] } } },
    hiddenEmpty: { _meta: { ui: { resourceUri: 'ui://workbench', visibility: [] } } },
    legacy: { _meta: { 'ui/resourceUri': 'ui://legacy' } },
    invalid: { _meta: { ui: { resourceUri: 'https://invalid' } } },
  };
  assert.deepEqual(mcpApps([{ name: 'app', tools }]).map(x => x.toolName), ['open', 'legacy']);
});
