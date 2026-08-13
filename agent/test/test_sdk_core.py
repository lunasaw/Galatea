"""Unit tests for Galatea SDK core skeleton capabilities."""

from __future__ import annotations

import unittest
from pathlib import Path

from claude_agent_sdk import ResultMessage

from agent.hooks import HookContext, HookEvent, HookInput, HookManager, HookOutput
from agent.policies import BudgetExceededError, BudgetPolicy, PermissionDeniedError, PermissionPolicy, PermissionRule
from agent.core import AgentSDKConfig, GalateaSDKRuntime, SDKRunResult
from agent.tools.executor import ToolExecutor, ToolRegistry, ToolSpec


class TestPermissionPolicy(unittest.TestCase):
    def test_deny_wins_and_wildcards_match(self):
        policy = PermissionPolicy.for_galatea(
            allowed_tools=["mcp__galatea-platform__inspect_*"],
            disallowed_tools=["Bash", "Write"],
        )

        self.assertEqual(
            policy.check_permission("mcp__galatea-platform__inspect_ray_status", {}),
            "allow",
        )
        self.assertEqual(policy.check_permission("Bash", {"command": "echo ok"}), "deny")
        self.assertEqual(policy.check_permission("mcp__galatea-platform__submit_job", {}), "deny")

    def test_rule_content_matches_input(self):
        policy = PermissionPolicy(mode="default", default_behavior="ask")
        policy.add_rule(PermissionRule("Read", "allow", "*.md"))
        self.assertEqual(policy.check_permission("Read", {"file_path": "README.md"}), "allow")
        self.assertEqual(policy.check_permission("Read", {"file_path": "data.csv"}), "ask")


class TestBudgetPolicy(unittest.TestCase):
    def test_budget_records_usage(self):
        policy = BudgetPolicy(max_budget_usd=0.10, max_tokens=100)
        policy.record_usage(0.03, 30)
        self.assertAlmostEqual(policy.remaining_budget_usd(), 0.07)
        self.assertEqual(policy.remaining_tokens(), 70)
        self.assertTrue(policy.check_budget())
        with self.assertRaises(BudgetExceededError):
            policy.record_usage(0.08, 1)


class TestHooks(unittest.IsolatedAsyncioTestCase):
    async def test_local_hook_manager_and_sdk_adapter(self):
        manager = HookManager(HookContext(session_id="s1", agent_type="test"))

        seen_sessions = []

        async def deny_hook(input_data: HookInput, context: HookContext) -> HookOutput:
            seen_sessions.append(context.session_id)
            return HookOutput(
                permission_decision="deny",
                permission_decision_reason="blocked",
                reason="unit test",
            )

        manager.add_hook(HookEvent.PRE_TOOL_USE, deny_hook, matcher="Bash")
        local_outputs = await manager.invoke_hooks(
            HookEvent.PRE_TOOL_USE,
            HookInput(HookEvent.PRE_TOOL_USE, {}, tool_name="Bash", tool_input={}),
        )
        self.assertEqual(local_outputs[0].permission_decision, "deny")
        self.assertEqual(seen_sessions[-1], "s1")

        sdk_hooks = manager.to_sdk_hooks()
        callback = sdk_hooks["PreToolUse"][0].hooks[0]
        sdk_output = await callback(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sdk-session",
                "tool_name": "Bash",
                "tool_input": {"command": "touch x"},
            },
            "toolu_123",
            {"signal": None},
        )
        self.assertEqual(
            sdk_output["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )
        self.assertEqual(seen_sessions[-1], "sdk-session")


class TestToolExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_direct_tool_execution_and_permission_denial(self):
        registry = ToolRegistry()

        async def echo(args):
            return {"content": [{"type": "text", "text": args["text"]}]}

        registry.register(ToolSpec("echo", "Echo text", {"text": str}, echo))
        policy = PermissionPolicy.for_galatea(allowed_tools=["echo"], disallowed_tools=[])
        executor = ToolExecutor(registry, policy)

        result = await executor.execute("echo", {"text": "hello"})
        self.assertFalse(result.is_error)
        self.assertEqual(result.content[0]["text"], "hello")

        denied = ToolExecutor(registry, PermissionPolicy.for_galatea(disallowed_tools=[]))
        with self.assertRaises(PermissionDeniedError):
            await denied.execute("echo", {"text": "no"})


class TestMcpServer(unittest.IsolatedAsyncioTestCase):
    async def test_galatea_mcp_server_lists_and_calls_tool(self):
        from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest
        from agent.tools.server import create_galatea_mcp_server

        server_config = create_galatea_mcp_server()
        server = server_config["instance"]
        list_response = await server.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
        tool_names = {tool.name for tool in list_response.root.tools}
        self.assertIn("list_training_projects", tool_names)

        call_request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="list_training_projects",
                arguments={"project_root": str(Path.cwd())},
            ),
        )
        call_response = await server.request_handlers[CallToolRequest](call_request)
        self.assertFalse(call_response.root.isError)
        self.assertIn("projects", call_response.root.content[0].text)



class TestRuntimeConfig(unittest.TestCase):
    def test_runtime_builds_safe_options_and_validates_result(self):
        from claude_agent_sdk import AgentDefinition

        runtime = GalateaSDKRuntime(
            AgentSDKConfig(
                project_root=Path.cwd(),
                allowed_tools=["mcp__galatea-platform__list_training_projects"],
                output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
                agents={"helper": AgentDefinition(description="helper", prompt="help")},
                max_budget_usd=1.0,
                auto_load_config=False,
            )
        )
        options = runtime.build_options()
        self.assertEqual(options.permission_mode, "dontAsk")
        self.assertIn("Bash", options.disallowed_tools)
        self.assertEqual(options.tools, ["Task"])
        self.assertIn("Task", options.allowed_tools)
        self.assertTrue(options.strict_mcp_config)
        self.assertIsNotNone(options.output_format)
        self.assertIn("PreToolUse", options.hooks)
        self.assertIn("PreCompact", options.hooks)

        result = SDKRunResult(
            messages=[],
            result_message=ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="s1",
                terminal_reason="completed",
                total_cost_usd=0.01,
                usage={"input_tokens": 2, "output_tokens": 3},
                structured_output={"ok": True},
            ),
            text="ok",
            structured_output={"ok": True},
        )
        runtime.validate_result(result)

        bad = SDKRunResult(
            messages=[],
            result_message=ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="s1",
                terminal_reason="completed",
                structured_output={"ok": "yes"},
            ),
            text="bad",
            structured_output={"ok": "yes"},
        )
        with self.assertRaises(Exception):
            runtime.validate_result(bad)


class TestStateAndWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_session_manager_create_resume_fork(self):
        from agent.state import MemorySessionStore, SessionManager

        manager = SessionManager(MemorySessionStore())
        await manager.create_session(
            "session-1",
            agent_type="training",
            project_name="ray-cats-and-dogs",
            metadata={"stage_run_id": "stage-1"},
        )
        session = await manager.resume_session("session-1")
        self.assertEqual(session["metadata"]["agent_type"], "training")

        forked_id = await manager.fork_session("session-1", "session-2")
        self.assertEqual(forked_id, "session-2")
        forked = await manager.resume_session("session-2")
        self.assertEqual(forked["metadata"]["forked_from"], "session-1")

    async def test_experiment_state_and_workflow(self):
        from agent.state import ExperimentStage, ExperimentState, ExperimentStateManager
        from agent.workflows import WorkflowState, WorkflowStateMachine
        from agent.workflows.orchestrator import DATA_TRAINING_WORKFLOW

        state = ExperimentState("exp-1", "ray-cats-and-dogs", "experiment")
        state.set_stage(ExperimentStage.DATA)
        state.add_artifact("manifest", "mlflow-artifacts:/manifest.json", ExperimentStage.DATA)
        state.record_stage_result(ExperimentStage.DATA, {"status": "success"})
        restored = ExperimentState.from_dict(state.to_dict())
        self.assertEqual(restored.get_artifact("manifest"), "mlflow-artifacts:/manifest.json")

        manager = ExperimentStateManager()
        await manager.save_state(restored)
        self.assertEqual(await manager.list_experiments("ray-cats-and-dogs"), ["exp-1"])

        machine = WorkflowStateMachine("wf-1", DATA_TRAINING_WORKFLOW)
        machine.start()
        self.assertEqual(machine.current_stage, "data")
        machine.complete_stage("data", {"status": "success"})
        transition = machine.transition_to("training")
        self.assertTrue(transition.allowed)
        machine.complete_stage("training", {"status": "success"})
        self.assertEqual(machine.state, WorkflowState.COMPLETED)



if __name__ == "__main__":
    unittest.main()
