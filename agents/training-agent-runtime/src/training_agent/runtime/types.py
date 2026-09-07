from dataclasses import dataclass

@dataclass
class TurnRequest:
    campaign_id: str
    request_revision: int
    decision_seq: int
    workspace: str
    thread_id: str | None
    skill_path: str
    prompt: str
    output_schema: dict

@dataclass
class TurnOutcome:
    thread_id: str | None
    turn_id: str | None
    status: str
    final_json: dict | None = None
    usage_snapshot: dict | None = None
    error: str | None = None
