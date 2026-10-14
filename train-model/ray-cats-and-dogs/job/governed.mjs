#!/usr/bin/env node
/** Publish separately, then plan and submit through the Galatea controller. */
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, relative } from 'node:path';

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = resolve(projectRoot, '../..');
const moduleUrl = (name) => new URL(`../../../plugins/dsh-galatea/src/${name}`, import.meta.url);
const { loadProjectManifest } = await import(moduleUrl('policies/project.ts'));
const { GalateaController } = await import(moduleUrl('tools/controller.ts'));
const { ProjectProcessService } = await import(moduleUrl('services/project-process.ts'));
const { RayJobsService } = await import(moduleUrl('services/ray.ts'));
const { MlflowService } = await import(moduleUrl('services/mlflow.ts'));
const [mode, configPath, releaseManifest, permissionPreset, attempt] = process.argv.slice(2);
if (!['plan', 'submit'].includes(mode) || !configPath || !releaseManifest) {
    throw new Error('Usage: node job/governed.mjs plan|submit configs/smoke.yaml /absolute/release.json [danger-full-access]');
}
if (mode === 'submit' && permissionPreset !== 'danger-full-access') {
    throw new Error('Submission requires explicit full-access execution authority');
}
if (!attempt || !/^[a-zA-Z0-9_-]+$/.test(attempt)) {
    throw new Error('Provide an explicit attempt ID as the fifth argument (plan may use "-" for permission preset)');
}
const releaseRoot = resolve(repoRoot, '../platform-data/ray-cats-and-dogs-release');
const controller = new GalateaController({
    projectRoot, releaseRoot,
    manifest: await loadProjectManifest(resolve(projectRoot, 'galatea.project.yaml')),
    process: new ProjectProcessService({ timeoutMs: 300_000 }),
    ray: new RayJobsService({ baseUrl: 'http://10.26.0.140:8265' }),
    mlflow: new MlflowService({ baseUrl: 'http://10.26.0.140:18103' }),
});
const role = configPath.includes('smoke') ? 'smoke' : 'trial';
const input = { configPath, releaseManifestPath: relative(releaseRoot, releaseManifest), role, attempt };
const planned = await controller.planRun(input);
if (!planned.ok) throw new Error(JSON.stringify(planned));
const evidenceRoot = resolve(repoRoot, '../platform-data/ray-cats-and-dogs-evidence');
await mkdir(evidenceRoot, { recursive: true });
await writeFile(resolve(evidenceRoot, `${role}-${attempt}-plan.json`), JSON.stringify(planned, null, 2) + '\n');
if (mode === 'plan') {
    console.log(JSON.stringify(planned, null, 2));
} else {
    const evidence = planned.data.evidence;
    const authorization = { kind: 'full-access', permissionPreset, stage: evidence.stage, artifactId: evidence.artifactId, evidenceDigest: evidence.digest };
    const submitted = await controller.submitJob({ ...input, authorization });
    await writeFile(resolve(evidenceRoot, `${role}-${attempt}-submission.json`), JSON.stringify({ authorization, submitted }, null, 2) + '\n');
    if (!submitted.ok) throw new Error(JSON.stringify(submitted));
    console.log(JSON.stringify(submitted, null, 2));
}
