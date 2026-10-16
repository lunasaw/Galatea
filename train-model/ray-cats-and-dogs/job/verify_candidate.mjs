#!/usr/bin/env node
/** Read-only final integrity/quality verification; never invokes promotion. */
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadProjectManifest } from '../../../packages/galatea-governance/src/policies/project.ts';
import { GalateaController } from '../../../packages/galatea-governance/src/tools/controller.ts';
import { MlflowService } from '../../../packages/galatea-governance/src/services/mlflow.ts';
import { RayJobsService } from '../../../packages/galatea-governance/src/services/ray.ts';
import { ProjectProcessService } from '../../../packages/galatea-governance/src/services/project-process.ts';

const [runId] = process.argv.slice(2);
if (!/^[a-f0-9]{32}$/.test(runId ?? '')) throw new Error('Provide the Champion MLflow Run ID');
const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
for (const name of ['MLFLOW_TRACKING_URI', 'RAY_API_SERVER_ADDRESS']) {
    const url = new URL(process.env[name]);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
        throw new Error(`Invalid explicit ${name}`);
    }
}
const controller = new GalateaController({
    projectRoot, releaseRoot: resolve(projectRoot, '../../../platform-data/ray-cats-and-dogs-release'),
    manifest: await loadProjectManifest(resolve(projectRoot, 'galatea.project.yaml')),
    process: new ProjectProcessService(),
    mlflow: new MlflowService({ baseUrl: process.env.MLFLOW_TRACKING_URI }),
    ray: new RayJobsService({ baseUrl: process.env.RAY_API_SERVER_ADDRESS }),
});
const verified = await controller.verifyCandidate({ runId });
console.log(JSON.stringify(verified, null, 2));
if (!verified.ok) process.exitCode = 1;
