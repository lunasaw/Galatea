#!/usr/bin/env node
/** Bound cancellation through the existing project Controller, never raw Ray stop. */
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const {loadProjectManifest}=await import('../../../packages/galatea-governance/src/policies/project.ts');
const {GalateaController}=await import('../../../packages/galatea-governance/src/tools/controller.ts');
const {ProjectProcessService}=await import('../../../packages/galatea-governance/src/services/project-process.ts');
const {RayJobsService}=await import('../../../packages/galatea-governance/src/services/ray.ts');
const {MlflowService}=await import('../../../packages/galatea-governance/src/services/mlflow.ts');
const [project,receiptFile,reason]=process.argv.slice(2);
if(!project||!receiptFile||!reason)throw new Error('Usage: project_stop.mjs PROJECT_ROOT SUBMISSION_RECEIPT REASON');
const receipt=JSON.parse(await readFile(receiptFile,'utf8'));
if(!receipt.submitted?.ok)throw new Error('A successful governed submission receipt is required');
const {target,submitted:{data}}=receipt;
const controller=new GalateaController({projectRoot:resolve(project),releaseRoot:resolve(project,'../../../platform-data'),
 manifest:await loadProjectManifest(resolve(project,'galatea.project.yaml')),process:new ProjectProcessService(),
 ray:new RayJobsService({baseUrl:target.rayApiServer}),mlflow:new MlflowService({baseUrl:target.mlflowTrackingUri})});
const result=await controller.stopJob({submissionId:data.submissionId,idempotencyKey:data.idempotencyKey,reason});
console.log(JSON.stringify(result));if(!result.ok)process.exitCode=1;
