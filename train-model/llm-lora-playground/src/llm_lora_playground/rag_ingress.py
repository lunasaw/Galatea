"""Owner-bound RAG ingress layered over Ray's public OpenAI ingress."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Mapping, Sequence

from fastapi import HTTPException, status
from ray.serve.llm.ingress import OpenAiIngress as RayOpenAiIngress
from ray.serve.llm.openai_api_models import ChatCompletionRequest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from .memory import (
    DEFAULT_PERSONA_SYSTEM_PROMPT,
    DiskMemoryIndex,
    LocalBgeEncoder,
    RetrievedMemory,
    build_memory_context_messages,
)


def _message_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, Mapping):
        return dict(message)
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    raise TypeError("unsupported chat message type")


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        values = []
        for part in content:
            item = _message_dict(part) if not isinstance(part, Mapping) else dict(part)
            if item.get("type") in {"text", "input_text"} and item.get("text"):
                values.append(str(item["text"]))
        return "\n".join(values)
    return ""


def latest_user_text(messages: Sequence[Any]) -> str:
    for message in reversed(messages):
        item = _message_dict(message)
        role = getattr(item.get("role"), "value", item.get("role"))
        if role == "user":
            return _text_content(item.get("content"))
    return ""


def _bounded_memories(memories: Sequence[RetrievedMemory], max_chars: int) -> list[RetrievedMemory]:
    selected: list[RetrievedMemory] = []
    used = 0
    for memory in memories:
        size = len(memory.record.content)
        if used + size > max_chars:
            continue
        selected.append(memory)
        used += size
    return selected


def _conflict_reply(memories: Sequence[RetrievedMemory]) -> str | None:
    values: dict[str, set[str]] = {}
    for memory in memories:
        record = memory.record
        event_date = str(record.attributes.get("event_date") or "")
        if record.status != "candidate" or not event_date:
            continue
        values.setdefault(str(record.fact_key or "conflicted_fact"), set()).add(event_date)
    conflicts = [(fact_key, sorted(dates)) for fact_key, dates in sorted(values.items()) if len(dates) > 1]
    if not conflicts:
        return None
    parts = []
    for fact_key, dates in conflicts:
        label = "我们关系开始日期" if fact_key == "relationship.started_at" else "这个事实"
        parts.append(f"{label}的候选日期有：{'、'.join(dates)}")
    return "；".join(parts) + "。这些记录彼此冲突，我不能确定哪一个正确，请你确认。"


def _requests_private_dump(query: str) -> bool:
    normalized = query.casefold().replace(" ", "")
    override_markers = ("忽略所有系统", "忽略系统规则", "忽略之前的指令", "绕过系统", "ignoreall", "ignoreprevious")
    private_markers = ("完整记忆", "全部记忆", "消息id", "会话id", "sourceid", "systemprompt", "系统提示")
    return any(marker in normalized for marker in override_markers) and any(
        marker in normalized for marker in private_markers
    )


def _direct_chat_response(body: ChatCompletionRequest, content: str) -> Response:
    response_id = "chatcmpl-" + uuid.uuid4().hex
    created = int(time.time())
    model = str(body.model)
    if bool(body.stream):
        first = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}],
        }
        last = {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }

        async def events():
            yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps(last, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")
    return JSONResponse({
        "id": response_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


class OpenAiIngress(RayOpenAiIngress):
    """Retrieve private evidence under one server-side owner scope before chat.

    The class name intentionally matches Ray's stock ingress deployment. Ray
    Serve derives the deployment identity from ``__name__``; changing it while
    updating a live application turns a code update into a topology migration.
    """

    def __init__(
        self,
        llm_deployments,
        model_cards,
        *,
        lora_paths=None,
        memory_config: Mapping[str, Any],
        persona_system_prompt: str | None = None,
        _memory_index: Any = None,
        _get_lora_model_metadata_func=None,
    ) -> None:
        super().__init__(
            llm_deployments,
            model_cards,
            lora_paths=lora_paths,
            _get_lora_model_metadata_func=_get_lora_model_metadata_func,
        )
        self._max_results = int(memory_config["max_results"])
        self._max_context_chars = int(memory_config["max_context_chars"])
        self._persona_system_prompt = str(persona_system_prompt or DEFAULT_PERSONA_SYSTEM_PROMPT).strip()
        if not self._persona_system_prompt:
            raise ValueError("persona_system_prompt must not be empty")
        self._retrieval_lock = asyncio.Lock()
        if _memory_index is not None:
            self._memory_index = _memory_index
            return
        encoder = LocalBgeEncoder(
            memory_config["embedding_model_path"],
            revision=str(memory_config["embedding_model_revision"]),
            model_digest=str(memory_config["embedding_model_digest"]),
            device="cpu",
            max_length=int(memory_config.get("embedding_max_length", 512)),
        )
        self._memory_index = DiskMemoryIndex(
            memory_config["index_path"],
            owner_scope=str(memory_config["fixed_owner_scope"]),
            encoder=encoder,
            expected_manifest_sha256=str(memory_config["index_manifest_sha256"]),
            expected_manifest_digest=str(memory_config["index_manifest_digest"]),
            candidate_path=memory_config.get("candidate_path"),
            expected_candidate_sha256=memory_config.get("candidate_sha256"),
        )

    async def chat(self, body: ChatCompletionRequest, request: Request) -> Response:
        query = latest_user_text(body.messages)
        if _requests_private_dump(query):
            return _direct_chat_response(
                body,
                "我不能执行绕过记忆边界、导出完整记忆或来源标识的请求。",
            )
        try:
            async with self._retrieval_lock:
                memories = await asyncio.to_thread(
                    self._memory_index.retrieve,
                    query,
                    k=self._max_results,
                )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="private memory retrieval is unavailable",
            ) from exc
        conflict_retriever = getattr(self._memory_index, "retrieve_conflicts", None)
        conflicts = []
        if conflict_retriever is not None:
            try:
                conflicts = await asyncio.to_thread(conflict_retriever, query, retrieved=memories)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="private memory conflict retrieval is unavailable",
                ) from exc
        conflict_reply = _conflict_reply(conflicts)
        if conflict_reply is not None:
            return _direct_chat_response(body, conflict_reply)
        memories = _bounded_memories(memories, self._max_context_chars)
        # The server owns the persona and evidence policy.  Client-supplied
        # system messages are removed so a request cannot override either one.
        original_messages = [
            item for item in (_message_dict(message) for message in body.messages)
            if item.get("role") != "system"
        ]
        grounded_messages = [
            *build_memory_context_messages(
                [*memories, *conflicts],
                persona_system_prompt=self._persona_system_prompt,
            ),
            *original_messages,
        ]
        grounded_body = body.__class__.model_validate({
            **body.model_dump(exclude_none=True),
            "messages": grounded_messages,
        })
        return await super().chat(grounded_body, request)


__all__ = ["OpenAiIngress", "latest_user_text"]
