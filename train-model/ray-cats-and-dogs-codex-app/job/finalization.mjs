/** Freeze verified API artifacts before the controller submits a Champion. */
import { mkdir, open, readFile, lstat } from 'node:fs/promises';
import { dirname, join, parse } from 'node:path';
import { evidenceDigest } from '../../../packages/galatea-governance/src/contracts/index.ts';
import { deterministicSubmissionId } from '../../../packages/galatea-governance/src/services/ray.ts';

export const FINAL_PROTOCOL = 'ray-cats-dogs-frozen-checkpoint-final-v1';

async function secureDirectory(path) {
    let current = path;
    while (current !== parse(current).root) {
        try {
            if ((await lstat(current)).isSymbolicLink()) throw new Error('Governance state cannot use symlinks');
        } catch (error) {
            if (error.code !== 'ENOENT') throw error;
        }
        current = dirname(current);
    }
    await mkdir(path, { recursive: true, mode: 0o700 });
}

export async function freezeCandidate({ controller, mlflow, process, manifest, projectRoot, planned, candidateRunId }) {
    const built = await controller.buildStageEvidence({ runId: candidateRunId, stage: 'training-optimization' });
    if (!built.ok) throw new Error(JSON.stringify(built));
    const evidence = built.data.evidence;
    const checked = await process.run({
        projectRoot,
        argv: manifest.spec.entrypoints.checkConfig.map(arg => arg === '{config}' ? planned.configPath : arg),
    });
    if (checked.exitCode !== 0) throw new Error('Champion config check failed');
    const config = JSON.parse(checked.stdout).config;
    if (config.run.role !== 'champion' || !config.evaluation.evaluate_test || config.ray.num_workers !== 1) {
        throw new Error('Frozen final evaluation requires the selected single-worker Champion configuration');
    }
    const artifacts = {};
    let selection;
    for (const path of [
        'config/resolved-config.json', 'reports/model-selection.json',
        'checkpoints/best/best-model.pt', 'checkpoints/best/training-state.json',
    ]) {
        const artifact = await mlflow.getArtifact({ runId: candidateRunId, path });
        artifacts[path] = artifact.digest;
        if (path === 'reports/model-selection.json') selection = JSON.parse(Buffer.from(artifact.bytes).toString());
    }
    if (!/^[a-f0-9]{64}$/.test(selection.best_model_sha256)
        || artifacts['checkpoints/best/best-model.pt'] !== `sha256:${selection.best_model_sha256}`
        || selection.test_evaluated_during_selection !== false) {
        throw new Error('Trial best checkpoint does not match its selection evidence');
    }
    const executionIdentity = evidenceDigest({
        readinessIdentity: planned.identity, candidateRunId, candidateEvidenceDigest: evidence.digest,
    });
    const freeze = {
        schema_version: 'ray-cats-dogs/candidate-freeze/v1',
        project: manifest.metadata.name,
        candidate_run_id: candidateRunId,
        candidate_evidence_digest: evidence.digest,
        checkpoint_sha256: selection.best_model_sha256,
        artifact_digests: artifacts,
        split_sha256: planned.plan.dataset.split_sha256,
        content_sha256: planned.plan.dataset.content_sha256,
        protocol: FINAL_PROTOCOL,
        config_digest: planned.plan.configDigest,
        release_id: planned.releaseId,
        readiness_identity: planned.identity,
        readiness_digest: planned.evidence.digest,
        execution_identity: executionIdentity,
        submission_id: deterministicSubmissionId(manifest.metadata.name, 'champion', executionIdentity),
    };
    // Persistent project volume, independent of temporary Ray sessions/releases.
    const directory = join(dirname(config.data.cache_dir), 'governance', 'freezes');
    await secureDirectory(directory);
    const path = join(directory, `${executionIdentity.slice(7)}.json`);
    const payload = JSON.stringify(freeze, null, 2) + '\n';
    try {
        const handle = await open(path, 'wx', 0o600);
        try { await handle.writeFile(payload); await handle.sync(); } finally { await handle.close(); }
        const parent = await open(directory, 'r');
        try { await parent.sync(); } finally { await parent.close(); }
    } catch (error) {
        if (error.code !== 'EEXIST' || (await lstat(path)).isSymbolicLink() || await readFile(path, 'utf8') !== payload) throw error;
    }
    return { freeze, path, evidence, stageEvidence: built };
}
