import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { loadOverlayPatches } from '@deepseek-ai/dsh-app-boot'

const patchPath = fileURLToPath(new URL('../cordis.patch.yml', import.meta.url))

describe('dsh-galatea bundle overlay', () => {
  it('parses through the Harness overlay loader with the expected plugin row', () => {
    const patches = loadOverlayPatches('dsh-galatea', patchPath)

    expect(patches).toHaveLength(1)
    expect(patches[0]).toMatchObject({
      insert: [
        {
          id: 'dsh-galatea',
          name: 'dsh-galatea',
          inject: ['tools', 'approval'],
          config: {
            projectRoot: {
              __jsExpr: "process.env.GALATEA_PROJECT_ROOT ?? '/data/ai/chenzhangyue/code/galatea/train-model/ray-cats-and-dogs'",
            },
            manifestPath: {
              __jsExpr: "process.env.GALATEA_MANIFEST_PATH ?? 'galatea.project.yaml'",
            },
            rayTokenEnv: { __jsExpr: 'process.env.GALATEA_RAY_TOKEN_ENV' },
            mlflowTokenEnv: { __jsExpr: 'process.env.GALATEA_MLFLOW_TOKEN_ENV' },
          },
        },
      ],
    })
  })
})
