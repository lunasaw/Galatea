"""Runtime schemas shared by the Provider, MCP service and Host registration."""

from jsonschema import Draft202012Validator
from .common import TaskError

STRING = {"type": "string", "minLength": 1, "maxLength": 128}
TEXT = {"type": "string", "minLength": 1, "maxLength": 8000}
OBJECT = {"type": "object"}
ARRAY = {"type": "array", "maxItems": 100}
INT = {"type": "integer", "minimum": 0}


def schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


STRINGS = {"type": "array", "items": STRING, "maxItems": 100}
RULE = schema(
    {
        "id": STRING,
        "type": {
            "enum": [
                "artifact_exists",
                "schema_matches",
                "metric_threshold",
                "external_operation_terminal",
                "user_review_recorded",
            ]
        },
        "parameters": {
            "type": "object",
            "properties": {
                "artifact_role": STRING,
                "schema_ref": STRING,
                "metric": STRING,
                "operator": {"enum": [">=", "<="]},
                "value": {"type": "number"},
                "split": {"enum": ["train", "validation"]},
            },
            "additionalProperties": False,
        },
    },
    ["id", "type", "parameters"],
)
STAGE = schema(
    {
        "id": STRING,
        "title": TEXT,
        "kind": {
            "enum": [
                "data.inspect",
                "data.transform",
                "data.freeze",
                "code.prepare",
                "training.run",
                "result.verify",
                "result.review",
            ]
        },
        "depends_on": STRINGS,
        "required": {"type": "boolean"},
        "input_refs": STRINGS,
        "output_slots": {
            "type": "array",
            "items": schema(
                {"name": STRING, "role": STRING, "media_type": STRING},
                ["name", "role", "media_type"],
            ),
        },
        "acceptance": {"type": "array", "minItems": 1, "items": RULE},
        "execution": schema(
            {
                "mode": {"enum": ["skill", "tool", "external", "manual"]},
                "capability_ref": STRING,
                "skill_refs": STRINGS,
                "retry_policy": schema(
                    {
                        "max_attempts": {"type": "integer", "minimum": 1},
                        "requires_reconciliation": {"type": "boolean"},
                    },
                    ["max_attempts", "requires_reconciliation"],
                ),
            },
            ["mode", "capability_ref", "skill_refs", "retry_policy"],
        ),
        "replaces_stage_id": STRING,
    },
    [
        "id",
        "title",
        "kind",
        "depends_on",
        "required",
        "input_refs",
        "output_slots",
        "acceptance",
        "execution",
    ],
)
CANDIDATE = schema(
    {
        "path": TEXT,
        "role": STRING,
        "slot": STRING,
        "media_type": STRING,
        "external_ref": OBJECT,
        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    },
    ["role", "slot", "media_type"],
)


COMMANDS = {
    "replace_plan": (
        {
            "reason": TEXT,
            "stages": {"type": "array", "maxItems": 50, "items": STAGE},
        },
        ["reason", "stages"],
    ),
    "begin_stage": ({"stage_id": STRING}, ["stage_id"]),
    "prepare_external": (
        {
            "attempt_id": STRING,
            "execution_token": STRING,
            "route": STRING,
            "immutable_inputs": OBJECT,
        },
        ["attempt_id", "execution_token", "route", "immutable_inputs"],
    ),
    "bind_external": (
        {"intent_id": STRING, "operation_ref": OBJECT},
        ["intent_id", "operation_ref"],
    ),
    "report_result": (
        {
            "stage_id": STRING,
            "attempt_id": STRING,
            "execution_token": STRING,
            "result": OBJECT,
            "artifact_candidates": {
                "type": "array",
                "maxItems": 32,
                "items": CANDIDATE,
            },
            "evidence_refs": STRINGS,
        },
        ["stage_id", "attempt_id", "execution_token", "result", "artifact_candidates"],
    ),
    "wait": (
        {"attempt_id": STRING, "execution_token": STRING, "wait_spec": OBJECT},
        ["attempt_id", "execution_token", "wait_spec"],
    ),
    "propose_training": (
        {"stage_id": STRING, "proposal": OBJECT},
        ["stage_id", "proposal"],
    ),
    "request_replan": ({"reason": TEXT}, ["reason"]),
    "complete_task": (
        {"summary": TEXT, "artifact_refs": STRINGS},
        ["summary", "artifact_refs"],
    ),
    "fail_task": (
        {"reason_code": STRING, "evidence_refs": STRINGS},
        ["reason_code", "evidence_refs"],
    ),
    "skip_stage": ({"stage_id": STRING, "reason": TEXT}, ["stage_id", "reason"]),
    "retry_stage": ({"stage_id": STRING, "reason": TEXT}, ["stage_id", "reason"]),
    "request_control": (
        {
            "action": {
                "enum": ["start", "pause", "interrupt_agent", "resume", "cancel"]
            },
            "reason": TEXT,
        },
        ["action"],
    ),
    "submit_input": (
        {"request_id": STRING, "values": OBJECT},
        ["request_id", "values"],
    ),
    "record_review": (
        {"result_digest": STRING, "decision": {"enum": ["accept", "reject"]}},
        ["result_digest", "decision"],
    ),
}
CREATE = schema(
    {
        "goal": TEXT,
        "inputs": ARRAY,
        "constraints": OBJECT,
        "start": {"type": "boolean"},
        "parent_task_id": STRING,
        "command_id": STRING,
    },
    ["goal", "command_id"],
)
TASK_CONSTRAINTS = schema({
    "scope": {"enum": ["data_processing", "code_preparation", "governed_training", "analysis"]},
    "allow_training": {"type": "boolean"},
    "max_auto_continuations": {"type": "integer", "minimum": 0, "maximum": 100},
    "deadline_at": {"type": "string", "format": "date-time"},
})
SCHEMAS = {
    "open_workbench": schema({"task_id": STRING}),
    "get_workbench": schema({"task_id": STRING}),
    "get_task": schema({"task_id": STRING, "known_version": INT}, ["task_id"]),
    "get_current_task": schema({}),
    "start_task": schema(
        {"goal": TEXT, "inputs": {"type": "array", "items": schema({"path": TEXT}, ["path"])},
         "constraints": TASK_CONSTRAINTS, "command_id": STRING},
        ["goal", "command_id"],
    ),
    "list_tasks": schema({"cursor": INT, "limit": INT}),
    "list_projects": schema({"cursor": INT, "limit": INT}),
    "get_task_events": schema(
        {"task_id": STRING, "after_seq": INT, "limit": INT}, ["task_id"]
    ),
    "get_workbench_events": schema(
        {"task_id": STRING, "after_cursor": INT, "limit": INT}, ["task_id"]
    ),
    "get_stage_details": schema(
        {"task_id": STRING, "stage_id": STRING, "cursor": INT}, ["task_id", "stage_id"]
    ),
    "get_artifact": schema(
        {"task_id": STRING, "artifact_id": STRING, "view": STRING},
        ["task_id", "artifact_id"],
    ),
    "get_file_change": schema(
        {"task_id": STRING, "change_id": STRING, "cursor": INT},
        ["task_id", "change_id"],
    ),
    "get_training_proposal": schema(
        {"task_id": STRING, "proposal_id": STRING}, ["task_id", "proposal_id"]
    ),
    "get_command_receipt": schema({"command_id": STRING}, ["command_id"]),
    "get_activation": schema({"activation_id": STRING}, ["activation_id"]),
    "create_task": CREATE,
    "launch_project": schema(
        {"goal": TEXT, "inputs": ARRAY, "constraints": OBJECT, "command_id": STRING},
        ["goal", "inputs", "command_id"],
    ),
    "activate_project_task": schema(
        {"project_id": STRING, "task_id": STRING, "command_id": STRING},
        ["project_id", "task_id", "command_id"],
    ),
}
for name, (properties, required) in COMMANDS.items():
    props = {"task_id": STRING, "command_id": STRING, **properties}
    req = ["task_id", "command_id", *required]
    if name in {"replace_plan", "begin_stage", "propose_training", "complete_task"}:
        props["expected_plan_revision"] = INT
        req.append("expected_plan_revision")
    if name in {"request_control", "submit_input"}:
        props["expected_version"] = INT
        req.append("expected_version")
    SCHEMAS[name] = schema(props, req)
READ_ONLY = {name for name in SCHEMAS if name.startswith(("get_", "list_", "open_"))}
APP_TOOLS = (READ_ONLY - {"get_current_task"}) | {
    "create_task",
    "launch_project",
    "activate_project_task",
    "request_control",
    "submit_input",
    "record_review",
}
AGENT_TOOLS = {"create_task", "start_task", "get_task", "get_current_task"} | (
    set(COMMANDS) - {"request_control", "submit_input", "record_review"}
)
DESCRIPTIONS = {
    "open_workbench": "在当前聊天展示 Galatea 任务的动态阶段、训练进度、指标与结果。当用户要求训练、微调、评估或数据处理时，由 Agent 主动调用；使用 galatea_task.start_task 返回的 task_id。用户继续在聊天里沟通，无须进入 App 填表或创建另一会话。",
    "start_task": "用户在当前聊天提出训练、微调或数据处理需求时，建立并开始当前会话的任务。返回 app_view 后 Host 自动展示进度面板，无需另调打开工具。只授权当前 Agent 回合的准备工作；训练仍须精确方案确认与治理授权，Host 可按当前权限自动确认。不创建新会话，不启动后台 Agent。",
    "get_current_task": "读取当前可信会话的任务；无任务时返回 null。新会话不得复用其他会话或项目的产物。",
    "create_task": "在当前可信会话建立任务草稿；Agent 不可自行授权后台启动。",
    "replace_plan": "提交完整动态阶段 DAG 和修订原因；冻结已执行阶段，保留历史。",
    "begin_stage": "开始一个已就绪阶段，取得 Attempt 和执行令牌；串行执行。",
    "report_result": "报告受限产物候选，进入服务端验证；返回成功不代表阶段完成。",
    "prepare_external": "外部写入前持久化治理路线、不可变输入及幂等身份；不提交训练。",
    "bind_external": "绑定并核验已有治理 operation；提交响应未知时先对账。",
    "propose_training": "保存不可变资源方案及用户决定待办，不产生训练授权或 Attempt。",
    "complete_task": "验证全部必要交付后封存任务；需要有效产物引用。",
    "get_task": "读取权威任务、当前计划、输入摘要、能力与待办。任务状态只从此处读取，不使用原生 get_goal。",
}


def validate(name, args):
    if name not in SCHEMAS:
        raise TaskError("INVALID_COMMAND", "Unknown tool")
    errors = list(Draft202012Validator(SCHEMAS[name]).iter_errors(args))
    if errors:
        raise TaskError("INVALID_COMMAND", "Arguments do not match " + name + " schema")
