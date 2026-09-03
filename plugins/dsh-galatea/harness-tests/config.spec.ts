import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { loadOverlayPatches } from '@deepseek-ai/dsh-app-boot'

const patchPath = fileURLToPath(new URL('../cordis.patch.yml', import.meta.url))

describe('dsh-galatea bundle overlay', () => {
  it('parses through the Harness overlay loader with the expected plugin row', () => {
    const patches = loadOverlayPatches('dsh-galatea', patchPath)

    expect(patches).toHaveLength(3)
    expect(patches[2]).toMatchObject({
      insert: [
        {
          id: 'dsh-galatea',
          name: 'dsh-galatea',
          inject: ['tools', 'approval', 'sessionProjections', 'systemPrompt'],
          config: {
            projects: [
              {
                id: 'ray-cats-and-dogs',
                projectRoot: '/data/ai/chenzhangyue/code/galatea/train-model/ray-cats-and-dogs',
                releaseRoot: '/data/ai/chenzhangyue/code/galatea/platform-data/ray-cats-and-dogs-release',
              },
              {
                id: 'ray-handwritten-digits',
                projectRoot: '/data/ai/chenzhangyue/code/galatea/train-model/ray-handwritten-digits',
                releaseRoot: '/data/ai/chenzhangyue/code/galatea/platform-data/ray-handwritten-digits-release',
              },
              {
                id: 'ray-kaggle-house-prices',
                projectRoot: '/data/ai/chenzhangyue/code/galatea/train-model/ray-kaggle-house-prices',
                releaseRoot: '/data/ai/chenzhangyue/code/galatea/platform-data/ray-kaggle-house-prices-release',
              },
            ],
            defaultProject: 'ray-cats-and-dogs',
            rayTokenEnv: { __jsExpr: 'process.env.GALATEA_RAY_TOKEN_ENV' },
            mlflowTokenEnv: { __jsExpr: 'process.env.GALATEA_MLFLOW_TOKEN_ENV' },
          },
        },
      ],
    })
  })
})
