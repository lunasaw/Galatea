"""Strict administrator configuration and versioned public tool schemas."""
from __future__ import annotations
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Ident = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')]
Sha = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Pos = Annotated[int, Field(strict=True, gt=0)]
Nonneg = Annotated[int, Field(strict=True, ge=0)]
Role = Literal['baseline', 'trial', 'champion', 'evaluate']


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Resources(Strict):
    cpus: Pos
    gpus: Nonneg
    memory_bytes: Pos
    workers: Pos
    seconds: Pos
    cleanup_seconds: Nonneg = 30

    def cost(self):
        duration = self.seconds + self.cleanup_seconds
        return {'cpu_seconds': self.cpus * self.workers * duration,
                'gpu_seconds': self.gpus * self.workers * duration}


class ObjectRef(Strict):
    bucket: str
    key: str
    version_id: str
    sha256: Sha
    size_bytes: Nonneg

    @model_validator(mode='after')
    def immutable(self):
        if not self.bucket or not self.key or self.version_id in {'', 'null'}:
            raise ValueError('immutable object version required')
        return self


class Dataset(Strict):
    dataset_id: Ident
    manifest_digest: Sha
    split_digest: Sha
    preprocessing: str
    holdout_identity: Sha
    holdout_untouched: bool
    views: dict[str, ObjectRef]

    @model_validator(mode='after')
    def split(self):
        if set(self.views) != {'train', 'validation', 'test'}:
            raise ValueError('three frozen views required')
        keys = {(r.bucket, r.key, r.version_id) for r in self.views.values()}
        if len(keys) != 3:
            raise ValueError('views must be distinct objects')
        if self.holdout_identity != self.views['test'].sha256:
            raise ValueError('holdout identity must equal immutable population content SHA-256')
        return self


class Release(Strict):
    path: str
    sha256: Sha
    code_revision: str
    environment_digest: Sha
    entrypoint: list[str]
    deadline_enforced: bool


class Configuration(Strict):
    path: str
    sha256: Sha
    seed: Nonneg
    resources: Resources


class Objective(Strict):
    metric: str
    direction: Literal['min', 'max']


class Gate(Objective):
    threshold: float


class Project(Strict):
    project_id: Ident
    root: str
    task: str
    objective: Objective
    metric_definition: str
    evaluation_protocol: str
    experiment_id: str
    dataset: Dataset
    releases: dict[Ident, Release]
    configs: dict[Ident, Configuration]
    evaluation_isolated: bool
    quality_gates: list[Gate]
    artifact_paths: list[str]
    model_artifact_path: str = "model/model.json"


class Slot(Strict):
    step_id: Ident
    role: Role
    config_ids: list[Ident]
    release_ids: list[Ident]
    max_attempts: Annotated[int, Field(strict=True, ge=1, le=3)] = 1


class Budget(Strict):
    cpu_seconds: Pos
    gpu_seconds: Nonneg
    max_trials: Annotated[int, Field(strict=True, ge=0, le=100)]


class CampaignSpec(Strict):
    campaign_id: Ident
    project_id: Ident
    request_revision: Pos
    expires_at: Pos
    approved_by: Annotated[str, Field(min_length=1)]
    slots: Annotated[list[Slot], Field(min_length=3, max_length=103)]
    budget: Budget

    @model_validator(mode='after')
    def bounded_slots(self):
        if len({s.step_id for s in self.slots}) != len(self.slots):
            raise ValueError('duplicate slots')
        for role in ['baseline', 'champion', 'evaluate']:
            if sum(s.role == role for s in self.slots) != 1:
                raise ValueError('one baseline/champion/evaluate slot required')
        if sum(s.role == 'trial' for s in self.slots) > self.budget.max_trials:
            raise ValueError('too many trial slots')
        if next(s for s in self.slots if s.role == 'evaluate').max_attempts != 1:
            raise ValueError('evaluation cannot retry')
        return self


ID_SCHEMA = {'type': 'string', 'pattern': r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'}
STR = {'type': 'string', 'minLength': 1, 'maxLength': 1024}
SCOPE = {'project_id': ID_SCHEMA, 'campaign_id': ID_SCHEMA}
PAGE = {'cursor': {'type': ['string', 'null'], 'maxLength': 2048},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}


def schema(properties, optional=()):
    return {'type': 'object', 'additionalProperties': False, 'properties': properties,
            'required': [name for name in properties if name not in optional]}


TOOLS = {
    'get_capabilities': schema({'protocol_version': {'const': 'galatea.tools/v1'}}, ['protocol_version']),
    'list_projects': schema(PAGE, PAGE),
    'inspect_project': schema({'project_id': ID_SCHEMA}),
    'get_campaign': schema(SCOPE),
    'list_operations': schema({**SCOPE, **PAGE}, PAGE),
    'get_operation': schema({**SCOPE, 'operation_id': ID_SCHEMA}),
    'plan_run': schema({**SCOPE, 'step_id': ID_SCHEMA, 'attempt': {'type': 'integer', 'minimum': 1, 'maximum': 3},
                        'release_id': ID_SCHEMA, 'config_id': ID_SCHEMA,
                        'role': {'enum': ['baseline', 'trial', 'champion', 'evaluate']}}),
    'submit_job': schema({**SCOPE, 'plan_id': ID_SCHEMA, 'idempotency_key': ID_SCHEMA}),
    'observe_job': schema({**SCOPE, 'operation_id': ID_SCHEMA}),
    'stop_job': schema({**SCOPE, 'operation_id': ID_SCHEMA, 'reason': STR}),
    'cancel_campaign': schema({**SCOPE, 'reason': STR}),
    'query_runs': schema({**SCOPE, **PAGE, 'role': {'enum': ['baseline', 'trial', 'champion', 'evaluate']}}, [*PAGE, 'role']),
    'get_metric_history': schema({**SCOPE, 'run_id': ID_SCHEMA, 'metric': STR, **PAGE}, PAGE),
    'get_artifact': schema({**SCOPE, 'run_id': ID_SCHEMA, 'artifact_ref': STR}),
    'compare_runs': schema({**SCOPE, 'run_ids': {'type': 'array', 'items': ID_SCHEMA, 'minItems': 1, 'maxItems': 100, 'uniqueItems': True}}),
    'freeze_candidate': schema({**SCOPE, 'run_id': ID_SCHEMA, 'evidence_digest': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}),
    'verify_candidate': schema({**SCOPE, 'candidate_id': ID_SCHEMA}),
}
TOOLS = {'galatea_' + key: value for key, value in TOOLS.items()}
