"""Persisted public-message revisions. Never exports reasoning or tool arguments."""

from .state import payload_digest


def export_events(store, binding, host_id, events):
    exported = []
    for event in events:
        frozen_path = (
            "app-export-events/"
            + payload_digest([host_id, binding["thread_id"], event.get("epoch"), event["seq"]])
            + ".json"
        )
        frozen = store.read(frozen_path)
        if frozen is not None:
            exported.append(frozen)
            continue
        payload = event.get("payload", {})
        params = payload.get("params", {})
        item = params.get("item", {})
        row = {
            k: event.get(k)
            for k in (
                "epoch",
                "seq",
                "session_id",
                "thread_id",
                "turn_id",
                "item_id",
                "observed_at",
            )
        }
        row.update(
            host_id=host_id,
            type=event.get("method") or event.get("kind"),
            summary="",
            status="observed",
            sequence_scope="host_global_filtered",
        )
        row["seq"] = int(row["seq"])
        row["thread_id"] = binding["thread_id"]
        row["session_id"] = binding["host_session_id"]
        method = event.get("method")
        message_id = item.get("id") or params.get("itemId") or event.get("item_id")
        if (
            item.get("type") in ("agentMessage", "userMessage")
            or method == "item/agentMessage/delta"
        ):
            path = (
                "app-public-messages/"
                + payload_digest([host_id, binding["thread_id"], event.get("epoch"), message_id])
                + ".json"
            )
            current = store.read(path) or {"text": "", "seq": 0, "role": "assistant"}
            if row["seq"] > current["seq"]:
                if method == "item/agentMessage/delta":
                    current["text"] += params.get("delta", "")
                else:
                    current["text"] = item.get("text") or "\n".join(
                        v.get("text", "") for v in item.get("content", []) if isinstance(v, dict)
                    )
                    current["role"] = "assistant" if item["type"] == "agentMessage" else "user"
                current.update(seq=row["seq"])
                store.write(path, current)
            row.update(
                type="public_message",
                message_id=message_id,
                role=current["role"],
                safe_content=current["text"],
                content_revision=current["seq"],
                delivery_state="completed" if method == "item/completed" else "streaming",
            )
        elif event.get("kind") == "message_accepted":
            public = payload.get("public", {})
            row.update(
                type="public_message",
                message_id=event.get("host_request_id") or event["event_id"],
                role="user",
                safe_content=public.get("text", ""),
                content_revision=row["seq"],
            )
        store.write(frozen_path, row)
        exported.append(row)
    return exported
