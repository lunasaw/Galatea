"""Identify explicit App presentations in public native tool records."""
import json


def presents_app(step):
    detail = step.get("detail") or {}
    result = step.get("result") or {}
    if step.get("type") == "mcpToolCall":
        uri = ((detail.get("mcpAppUi") or {}).get("resourceUri")
               or (detail.get("appContext") or {}).get("resourceUri")
               or detail.get("mcpAppResourceUri"))
        return (isinstance(uri, str) and uri.startswith("ui://") and bool(detail.get("server"))
                and not detail.get("error") and not (detail.get("result") or {}).get("isError")
                and detail.get("status") not in {"failed", "cancelled", "interrupted"})
    if step.get("type") != "tool" or any(row.get("success") is False for row in (step, detail, result)):
        return False
    for item in result.get("contentItems") or detail.get("contentItems") or []:
        if item.get("type") != "inputText":
            continue
        try:
            envelope = json.loads(item.get("text", ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(envelope, dict) or envelope.get("schema_version") != "codex-agent.tools/v1" or envelope.get("ok") is not True:
            continue
        data = envelope.get("data")
        view = data.get("app_view") if isinstance(data, dict) else None
        if (isinstance(view, dict) and isinstance(view.get("server"), str)
                and isinstance(view.get("resource_uri"), str) and view["resource_uri"].startswith("ui://")):
            return True
    return False
