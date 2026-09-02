import tsconfigPaths from '/data/ai/chenzhangyue/code/deepseek-harness/node_modules/vite-tsconfig-paths/dist/index.js'
import { defineConfig } from '/data/ai/chenzhangyue/code/deepseek-harness/node_modules/vitest/dist/config.js'

const harnessRoot = '/data/ai/chenzhangyue/code/deepseek-harness'

export default defineConfig({
  root: harnessRoot,
  plugins: [tsconfigPaths({ root: harnessRoot, projects: ['./tsconfig.base.json'] })],
  resolve: {
    alias: {
      '@deepseek-ai/cordis': `${harnessRoot}/vendor/cordis/src/index.ts`,
      '@deepseek-ai/schemastery': `${harnessRoot}/vendor/schemastery/src/index.ts`,
      '@deepseek-ai/dsh-app-boot': `${harnessRoot}/packages/boot/app-boot/src/index.ts`,
      '@deepseek-ai/dsh-agent': `${harnessRoot}/packages/core/agent/src/index.ts`,
      '@deepseek-ai/dsh-session': `${harnessRoot}/packages/core/session/src/index.ts`,
      '@deepseek-ai/dsh-system-prompt': `${harnessRoot}/packages/core/system-prompt/src/index.ts`,
      '@deepseek-ai/dsh-tools': `${harnessRoot}/packages/core/tools/src/index.ts`,
      '@deepseek-ai/dsh-user-approval': `${harnessRoot}/packages/interaction/user-approval/src/index.ts`,
      '@deepseek-ai/dsh-user-questions': `${harnessRoot}/packages/interaction/user-questions/src/index.ts`,
    },
  },
  test: {
    include: ['/data/ai/chenzhangyue/code/galatea/plugins/dsh-galatea/harness-tests/**/*.spec.ts'],
    pool: 'forks',
  },
})
