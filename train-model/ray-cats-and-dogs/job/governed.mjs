#!/usr/bin/env node
/** Publish separately, then plan and submit through the Galatea controller. */
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, relative } from 'node:path';

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = resolve(projectRoot, '../..');
const moduleUrl = (name) => new URL(`../../../packages/galatea-governance/src/${name}`, import.meta.url);
const { loadProjectManifest } = await import(moduleUrl('policies/project.ts'));
const { GalateaController } = await import(moduleUrl('tools/controller.ts'));
const { ProjectProcessService } = await import(moduleUrl('services/project-process.ts'));
const { RayJobsService } = await import(moduleUrl('services/ray.ts'));
const { MlflowService } = await import(moduleUrl('services/mlflow.ts'));
const [mode, configPath, releaseManifest, permissionPreset, attempt, candidateRunId] = process.argv.slice(2);
if (!['plan', 'submit'].includes(mode) || !configPath || !releaseManifest) {
    throw new Error('Usage: node job/governed.mjs plan|submit configs/smoke.yaml /absolute/release.json [danger-full-access]');
}
if (mode === 'submit' && permissionPreset !== 'danger-full-access') {
    throw new Error('Submission requires explicit full-access execution authority');
}
if (!attempt || !/^[a-zA-Z0-9_-]+$/.test(attempt)) {
    throw new Error('Provide an explicit attempt ID as the fifth argument (plan may use "-" for permission preset)');
}
const clusterProfile = process.env.GALATEA_RAY_PROFILE?.trim();
const rayAddress = process.env.RAY_ADDRESS?.trim();
const rayApiServer = (process.env.RAY_API_SERVER_ADDRESS ?? process.env.RAY_DASHBOARD_URL)?.trim();
const mlflowTrackingUri = process.env.MLFLOW_TRACKING_URI?.trim();
if (!clusterProfile || !/^[a-zA-Z0-9_-]+$/.test(clusterProfile)) {
    throw new Error('Set GALATEA_RAY_PROFILE to the approved Ray cluster profile (for example dev or prod)');
}
if (!rayAddress || !/^[^/\s]+:\d+$/.test(rayAddress)) {
    throw new Error('Set RAY_ADDRESS to the selected cluster GCS host:port; implicit cluster discovery is disabled');
}
for (const [name, value] of [['RAY_API_SERVER_ADDRESS', rayApiServer], ['MLFLOW_TRACKING_URI', mlflowTrackingUri]]) {
    let url;
    try { url = new URL(value); } catch { /* Report the variable name without exposing its value. */ }
    if (!url || !['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
        throw new Error(`Set ${name} to a target HTTP API URL without embedded credentials, query, or fragment`);
    }
}
const releaseRoot = resolve(repoRoot, '../platform-data/ray-cats-and-dogs-release');
const manifest = await loadProjectManifest(resolve(projectRoot, 'galatea.project.yaml'));
const projectProcess = new ProjectProcessService({ timeoutMs: 300_000 });
const mlflow = new MlflowService({ baseUrl: mlflowTrackingUri });
const controller = new GalateaController({
    projectRoot, releaseRoot,
    manifest,
    process: projectProcess,
    ray: new RayJobsService({ baseUrl: rayApiServer }),
    mlflow,
});
const role = configPath.includes('champion') ? 'champion' : configPath.includes('smoke') ? 'smoke' : 'trial';
if (role === 'champion' && mode === 'submit' && !/^[a-f0-9]{32}$/.test(candidateRunId ?? '')) {
    throw new Error('Champion requires the selected eligible Trial Run ID as the sixth argument');
}
if (role !== 'champion' && candidateRunId) throw new Error('Candidate binding applies only to Champion');
const input = { configPath, releaseManifestPath: relative(releaseRoot, releaseManifest), role, attempt };
const planned = await controller.planRun(input);
if (!planned.ok) throw new Error(JSON.stringify(planned));
const target = { clusterProfile, rayAddress, rayApiServer, mlflowTrackingUri };
const evidenceRoot = resolve(repoRoot, '../platform-data/ray-cats-and-dogs-evidence');
await mkdir(evidenceRoot, { recursive: true });
await writeFile(resolve(evidenceRoot, `${role}-${attempt}-plan.json`), JSON.stringify({ target, planned }, null, 2) + '\n');
if (mode === 'plan') {
    console.log(JSON.stringify({ target, planned }, null, 2));
} else {
    const evidence = planned.data.evidence;
    const authorization = { kind: 'full-access', permissionPreset, stage: evidence.stage, artifactId: evidence.artifactId, evidenceDigest: evidence.digest };
    let candidate;
    let candidateAuthorization;
    if (role === 'champion') {
        const { freezeCandidate } = await import('./finalization.mjs');
        candidate = await freezeCandidate({ controller, mlflow, process: projectProcess, manifest, projectRoot, planned: planned.data, candidateRunId });
        const bound = candidate.evidence;
        candidateAuthorization = { kind: 'full-access', permissionPreset, stage: bound.stage, artifactId: bound.artifactId, evidenceDigest: bound.digest };
        await writeFile(resolve(evidenceRoot, `${role}-${attempt}-freeze.json`), JSON.stringify(candidate, null, 2) + '\n');
    }
    const submitted = await controller.submitJob({
        ...input, authorization,
        ...(candidate ? { candidateRunId, candidateAuthorization } : {}),
    });
    await writeFile(resolve(evidenceRoot, `${role}-${attempt}-submission.json`), JSON.stringify({ target, authorization, candidateAuthorization, submitted }, null, 2) + '\n');
    if (!submitted.ok) throw new Error(JSON.stringify(submitted));
    console.log(JSON.stringify({ target, submitted }, null, 2));
}
