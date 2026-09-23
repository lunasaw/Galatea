"""Bounded, real app-server + local mock Responses acceptance. No platform calls.

The standard-library HTTP server is a test fixture, never a production Console.
Only synthetic prompts and receipts are sent or recorded by this probe.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from codex_agent.catalog import Catalog, digest
from codex_agent.compatibility import RuntimeContract
from codex_agent.stage0 import (
    GateError, REQUIRED_CHECKS, assert_tool_inventory, assert_tool_surface,
    file_sha256, package_manifest, tool_surface_differences,
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def sse(items: list[dict], response_id: str) -> bytes:
    events = [{"type": "response.created", "response": {"id": response_id}}]
    events.extend({"type": "response.output_item.done", "item": item} for item in items)
    events.append({"type": "response.completed", "response": {"id": response_id,
                  "status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}})
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()


class MockModel:
    def __init__(self):
        self.requests = []
        self.errors = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size <= 0 or size > 4 * 1024 * 1024:
                        raise ValueError("Invalid mock request size")
                    if self.headers.get("Content-Encoding", "identity") != "identity":
                        raise ValueError("Compressed mock requests are disabled by the fixture config")
                    body = json.loads(self.rfile.read(size))
                    owner.requests.append(body)
                    index = len(owner.requests)
                    if index == 1:
                        items = [{"type": "function_call", "call_id": "probe-call",
                                  "namespace": "galatea", "name": "galatea_get_capabilities",
                                  "arguments": '{"protocol_version":"galatea.tools/v1"}'}]
                    else:
                        items = [{"id": f"msg-{index}", "type": "message", "role": "assistant",
                                  "content": [{"type": "output_text", "text": "Synthetic probe complete."}]}]
                    output = sse(items, f"resp-{index}")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(output)))
                    self.end_headers()
                    self.wfile.write(output)
                except Exception as exc:
                    owner.errors.append(type(exc).__name__ + ": " + str(exc))
                    self.send_error(400)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class ProbeRPC:
    def __init__(self, process):
        self.process = process
        self.next_id = 0
        self.events = []
        self.seen_responses = set()

    async def send(self, payload: dict):
        self.process.stdin.write(json.dumps(payload).encode() + b"\n")
        await self.process.stdin.drain()

    async def read(self):
        line = await asyncio.wait_for(self.process.stdout.readline(), 30)
        if not line:
            raise GateError("app-server stdout EOF")
        message = json.loads(line)
        self.events.append(message)
        if "id" in message and "method" not in message:
            if message["id"] in self.seen_responses:
                raise GateError("Duplicate JSON-RPC response id")
            self.seen_responses.add(message["id"])
        return message

    async def request(self, method: str, params: dict):
        self.next_id += 1
        request_id = self.next_id
        await self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = await self.read()
            if message.get("id") == request_id and "method" not in message:
                return message
            if "id" in message and "method" in message:
                raise GateError("Unexpected server request during probe setup")

    async def result(self, method: str, params: dict):
        response = await self.request(method, params)
        if "error" in response:
            raise GateError(f"{method}: {response['error']}")
        return response["result"]


@asynccontextmanager
async def process(binary: Path, directory: Path, *, experimental=True):
    codex_dir = directory / "codex-home"
    proc_home = directory / "process-home"
    workspace = directory / "workspace"
    for path in (codex_dir, proc_home, workspace):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(proc_home), "CODEX_HOME": str(codex_dir),
           "LANG": "C.UTF-8"}
    with (directory / "stderr.log").open("ab") as error_log:
        child = await asyncio.create_subprocess_exec(
            str(binary), "app-server", "--listen", "stdio://", "--strict-config",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=error_log,
            cwd=workspace, env=env, start_new_session=True, limit=4 * 1024 * 1024,
        )
        rpc = ProbeRPC(child)
        try:
            await rpc.result("initialize", {"clientInfo": {"name": "galatea-stage0", "version": "0.1.0"},
                                            "capabilities": {"experimentalApi": experimental}})
            await rpc.send({"method": "initialized"})
            yield rpc
        finally:
            if child.returncode is None:
                child.stdin.close()
                try:
                    await asyncio.wait_for(child.wait(), 5)
                except asyncio.TimeoutError:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(child.wait(), 5)
                    except asyncio.TimeoutError:
                        os.killpg(child.pid, signal.SIGKILL)
                        await child.wait()
            # The probe owns this process group; clean surviving helpers as well.
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            write_json(directory / f"rpc-{child.pid}.json", rpc.events)


def mock_config(port: int) -> str:
    disabled = ["apps", "plugins", "remote_plugin", "recommended_plugins", "tool_suggest",
                "multi_agent", "multi_agent_v2", "shell_tool", "unified_exec", "view_image",
                "sleep_tool", "goals", "hooks", "image_generation", "browser_use", "computer_use",
                "code_mode", "code_mode_only", "code_mode_host", "workspace_dependencies",
                "skill_mcp_dependency_install", "skill_search", "enable_request_compression",
                "current_time_reminder", "token_budget", "deferred_executor"]
    return '\n'.join([
        'model = "mock-model"', 'model_provider = "mock_provider"', 'web_search = "disabled"',
        'approval_policy = "never"', 'sandbox_mode = "read-only"',
        '[model_providers.mock_provider]', 'name = "Local synthetic acceptance model"',
        f'base_url = "http://127.0.0.1:{port}/v1"', 'wire_api = "responses"',
        'requires_openai_auth = false', 'supports_websockets = false',
        'request_max_retries = 0', 'stream_max_retries = 0',
        '[features]', *(f'{name} = false' for name in disabled),
        '[orchestrator.skills]', 'enabled = false',
        '[orchestrator.mcp]', 'enabled = false',
        '[skills]', 'include_instructions = false',
        '[skills.bundled]', 'enabled = false',
        '[tools.experimental_request_user_input]', 'enabled = false',
        '[tools.update_plan]', 'enabled = false', '',
    ])


async def complete_turn(rpc: ProbeRPC, thread_id: str, *, success: bool) -> tuple[bool, dict]:
    await rpc.result("turn/start", {"threadId": thread_id,
                                   "input": [{"type": "text", "text": "Run synthetic protocol fixture.",
                                              "text_elements": []}]})
    called = False
    receipt = {"schema_version": "galatea.tools/v1", "request_id": "req-synthetic-probe", "ok": success}
    receipt.update({"data": {"probe": True}} if success else {
        "error": {"category": "forbidden", "retryable": False, "state_changed": False,
                  "operation_id": None, "next_action": "review-authorization"}})
    while True:
        message = await rpc.read()
        if message.get("method") == "item/tool/call":
            params = message["params"]
            if (params["threadId"] != thread_id or params["namespace"] != "galatea"
                    or params["tool"] != "galatea_get_capabilities" or params["callId"] != "probe-call"
                    or params["arguments"] != {"protocol_version": "galatea.tools/v1"} or called):
                raise GateError("Dynamic call binding mismatch")
            called = True
            await rpc.send({"id": message["id"], "result": {"success": success,
                           "contentItems": [{"type": "inputText", "text": json.dumps(receipt)}]}})
        elif "id" in message and "method" in message:
            raise GateError("Unexpected runtime capability request")
        elif message.get("method") == "turn/completed":
            if message["params"]["turn"]["status"] != "completed":
                raise GateError("Probe turn did not complete successfully")
            return called, receipt


async def probe_case(binary: Path, directory: Path, catalog: list[dict], *, success: bool,
                     compatibility: RuntimeContract, actions: set[str]) -> dict:
    directory.mkdir(parents=True, mode=0o700)
    with MockModel() as model:
        (directory / "codex-home").mkdir(mode=0o700)
        (directory / "codex-home/config.toml").write_text(mock_config(model.server.server_port))
        result = {"tool_call_received": False, "receipt_seen_by_model": False,
                  "resumed_in_new_process": False, "surface_errors": [], "inventory_errors": [],
                  "original_schema_differences": []}
        try:
            async with process(binary, directory) as rpc:
                config = await rpc.result("config/read", {"includeLayers": True})
                write_json(directory / "effective-config.json", config)
                result["effective_config_sha256"] = digest(config)
                started = await rpc.result("thread/start", {"cwd": str(directory / "workspace"),
                    "approvalPolicy": "never", "sandbox": "read-only", "environments": [],
                    "dynamicTools": catalog})
                thread_id = started["thread"]["id"]
                called, receipt = await complete_turn(rpc, thread_id, success=success)
                result["tool_call_received"] = called
                read = await rpc.result("thread/read", {"threadId": thread_id, "includeTurns": True})
                write_json(directory / "thread-read.json", read)
                result["first_pid"] = rpc.process.pid
            async with process(binary, directory) as rpc:
                await rpc.result("thread/resume", {"threadId": thread_id})
                resumed_config = await rpc.result("config/read", {"includeLayers": True})
                write_json(directory / "resume-effective-config.json", resumed_config)
                result["resume_effective_config_sha256"] = digest(resumed_config)
                await complete_turn(rpc, thread_id, success=success)
                result["resumed_in_new_process"] = result["first_pid"] != rpc.process.pid
            for index, request in enumerate(model.requests):
                try:
                    assert_tool_inventory(request, catalog)
                except GateError as exc:
                    result["inventory_errors"].append({"request": index, "error": str(exc)})
                try:
                    compatibility.assert_request(request, actions)
                except GateError as exc:
                    result["surface_errors"].append({"request": index, "error": str(exc)})
                result["original_schema_differences"].append({"request": index,
                    "differences": tool_surface_differences(request, catalog)})
            outputs = [item for req in model.requests[1:] for item in req.get("input", [])
                       if item.get("type") == "function_call_output" and item.get("call_id") == "probe-call"]
            for item in outputs:
                content = item["output"]
                if isinstance(content, str):
                    result["receipt_seen_by_model"] |= json.loads(content) == receipt
                elif isinstance(content, list):
                    result["receipt_seen_by_model"] |= any(
                        block.get("type") == "input_text" and json.loads(block["text"]) == receipt
                        for block in content)
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            result["model_requests"] = len(model.requests)
            result["mock_errors"] = model.errors
            write_json(directory / "model-requests.json", model.requests)
            write_json(directory / "tool-surfaces.json", [{"tools": req.get("tools")} for req in model.requests])
            write_json(directory / "result.json", result)
        return result


def probe_runtime(package: Path, output: Path, contracts: Path) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime = output / "runtime"
    if runtime.exists():
        raise GateError("Probe output already contains a runtime; use a new directory")
    source_tree = package_manifest(package)
    shutil.copytree(package, runtime)
    copied_tree = package_manifest(runtime)
    if copied_tree != source_tree:
        raise GateError("Runtime copy did not preserve the package tree")
    package_info = json.loads((runtime / "codex-package.json").read_text())
    if package_info.get("variant") != "codex" or package_info.get("entrypoint") != "bin/codex":
        raise GateError("This probe accepts only the unified codex package layout")
    binary = runtime / package_info["entrypoint"]
    version = subprocess.check_output([str(binary), "--version"], text=True, timeout=15).strip()
    if version != "codex-cli " + package_info["version"]:
        raise GateError("Runtime version differs from package metadata")
    subprocess.run([str(binary), "app-server", "generate-json-schema", "--experimental",
                    "--out", str(output / "schema")], check=True, capture_output=True, timeout=30)
    catalog = Catalog.load(contracts)
    write_json(output / "package-tree.json", copied_tree)
    manifest = {"codex_version": version, "package": package_info,
                "runtime_binary_sha256": file_sha256(binary),
                "runtime_package_manifest_sha256": file_sha256(runtime / "codex-package.json"),
                "runtime_package_tree_sha256": copied_tree["sha256"],
                "app_server_protocol_schema_sha256": package_manifest(output / "schema")["sha256"],
                "dynamic_tool_catalog_sha256": catalog.digest,
                "contract_sha256": file_sha256(contracts / "tools.json"),
                "source_provenance": "unverified-development-fixture"}
    compatibility = RuntimeContract.load(contracts / "runtime-compatibility.json", catalog)
    compatibility.assert_runtime(manifest)
    manifest["runtime_compatibility_sha256"] = compatibility.digest
    write_json(output / "manifest.json", manifest)

    async def run():
        checks = {}
        negative = output / "experimental-negative"
        negative.mkdir()
        (negative / "codex-home").mkdir()
        (negative / "codex-home/config.toml").write_text(mock_config(1))
        try:
            async with process(binary, negative, experimental=False) as rpc:
                reply = await rpc.request("thread/start", {"environments": [],
                                          "dynamicTools": catalog.dynamic_tools({"galatea_get_capabilities"})})
                checks["experimental_negative"] = (reply.get("error", {}).get("code") == -32600
                    and "experimentalApi" in reply.get("error", {}).get("message", ""))
        except Exception as exc:
            checks["experimental_negative"] = False
            checks["negative_error"] = str(exc)
        cases = {}
        for name, actions, success in [("full", {"*"}, True), ("subset", {"galatea_get_capabilities"}, False)]:
            cases[name] = await probe_case(binary, output / name, catalog.dynamic_tools(actions), success=success,
                                           compatibility=compatibility, actions=actions)
            case = cases[name]
            checks[name] = (not case.get("error") and not case["surface_errors"]
                            and not case["mock_errors"] and case["model_requests"] >= 3
                            and case["tool_call_received"] and case["receipt_seen_by_model"]
                            and case["resumed_in_new_process"])
        return {"protocol_status": "passed" if all(value is True for value in checks.values()) else "failed",
                "stage0_status": "blocked", "checks": checks, "cases": cases, "manifest": manifest}

    report = asyncio.run(run())
    write_json(output / "protocol-report.json", report)
    gate_checks = {name: {"expected": "Automated, release-bound evidence required",
                         "observed": "Not exercised by the protocol probe", "evidence_path": None,
                         "runtime_version": version, "git_revision": None, "status": "not_run"}
                   for name in REQUIRED_CHECKS}

    def evidence(name, status, expected, observed, path):
        gate_checks[name].update(status=status, expected=expected, observed=observed,
                                 evidence_path=path, evidence_sha256=file_sha256(output / path))

    evidence("runtime_package", "passed", "Exact copy of all package files and executable bits",
             copied_tree["sha256"], "package-tree.json")
    evidence("protocol_schema", "passed", "Generate experimental schemas with the copied binary",
             manifest["app_server_protocol_schema_sha256"], "manifest.json")
    evidence("catalog", "passed", "Versioned descriptions, annotations and original input schemas",
             catalog.digest, "manifest.json")
    evidence("experimental_negative", "passed" if report["checks"]["experimental_negative"] else "failed",
             "Without experimentalApi, reject thread/start.dynamicTools with -32600", report["checks"],
             "protocol-report.json")
    positive = all(case["tool_call_received"] for case in report["cases"].values())
    evidence("experimental_positive", "passed" if positive else "failed",
             "With experimentalApi, create Thread and receive item/tool/call", positive, "protocol-report.json")
    for name, case in report["cases"].items():
        for phase, index in (("new", 0), ("resume", 2)):
            errors = [error for error in case["surface_errors"] if error["request"] == index]
            passed = case["model_requests"] > index and not errors and not case.get("error")
            evidence(f"{name}_{phase}_surface", "passed" if passed else "failed",
                     "Exact principal tool membership and frozen compatible wire schema",
                     {"inventory_matches": not case["inventory_errors"], "schema_differences": errors},
                     f"{name}/tool-surfaces.json")
        check_name = "dynamic_round_trip" if name == "full" else "dynamic_error_round_trip"
        round_trip = case["tool_call_received"] and case["receipt_seen_by_model"]
        evidence(check_name, "passed" if round_trip else "failed",
                 "Model receives the exact success/error envelope through native dynamic tool response",
                 round_trip, f"{name}/result.json")
    report["stage0"] = {"status": "blocked", "checks": gate_checks, "manifest": manifest}
    write_json(output / "report.json", report)
    write_json(output / "stage0.json", report["stage0"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = probe_runtime(args.package, args.output, Path(__file__).resolve().parents[1] / "config/contracts")
    print(json.dumps({"protocol_status": report["protocol_status"], "stage0_status": report["stage0_status"],
                      "checks": report["checks"], "evidence": str(args.output / "report.json")}, indent=2))
    return 1 if report["protocol_status"] != "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
