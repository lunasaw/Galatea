"""Unit tests for Galatea SDK core skeleton capabilities."""

from __future__ import annotations

import asyncio
import unittest
import tempfile
from pathlib import Path

from claude_agent_sdk import ResultMessage, tool

from agent.hooks import HookManager
from agent.policies import BudgetExceededError, BudgetPolicy, PermissionDeniedError, PermissionPolicy, PermissionRule
from agent.core import AgentSDKConfig, GalateaSDKRuntime, SDKRunResult
from agent.skills import SkillRegistry
from agent.tools.executor import DeterministicMcpToolExecutor, SdkMcpToolRegistry


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
        self.assertEqual(policy.check_permission("mcp__galatea-platform__submit_job", {}), "defer")

    def test_rule_content_matches_input(self):
        policy = PermissionPolicy()
        policy.add_rule(PermissionRule("Read", "allow", "*.md"))
        self.assertEqual(policy.check_permission("Read", {"file_path": "README.md"}), "allow")
        self.assertEqual(policy.check_permission("Read", {"file_path": "data.csv"}), "defer")

    def test_scoped_skill_rules_match_exact_and_prefix(self):
        policy = PermissionPolicy.for_galatea(
            allowed_tools=[
                "Skill(ray)",
                "Skill(mlflow-optimize-models:*)",
            ],
            disallowed_tools=[],
        )

        self.assertEqual(policy.check_permission("Skill", {"skill": "ray"}), "allow")
        self.assertEqual(policy.check_permission("Skill", {"skill": "/ray"}), "allow")
        self.assertEqual(
            policy.check_permission("Skill", {"skill": "mlflow-optimize-models advanced"}),
            "allow",
        )
        self.assertEqual(policy.check_permission("Skill", {"skill": "unknown"}), "defer")


class TestRuntimeFoundationBoundary(unittest.TestCase):
    def test_runtime_does_not_expose_prompt_command_layer(self):
        from agent.runtime import GalateaRuntime

        runtime = GalateaRuntime(project_root=Path.cwd(), auto_load_config=False)

        self.assertFalse(hasattr(runtime, "command_registry"))
        self.assertFalse(hasattr(runtime, "build_command_plan"))

    def test_runtime_defaults_to_platform_inspection_tools_only(self):
        from agent.runtime import GalateaRuntime

        runtime = GalateaRuntime(project_root=Path.cwd(), auto_load_config=False)
        options = runtime.sdk_runtime.build_options()

        self.assertIn("mcp__galatea-platform__inspect_ray_status", options.allowed_tools)
        self.assertNotIn("Bash(git push:*)", options.allowed_tools)
        self.assertNotIn("Bash", options.allowed_tools)

    def test_runtime_preserves_explicit_empty_allowlist(self):
        from agent.runtime import GalateaRuntime

        runtime = GalateaRuntime(
            project_root=Path.cwd(),
            allowed_tools=[],
            auto_load_config=False,
        )
        self.assertEqual(runtime.sdk_runtime.build_options().allowed_tools, [])

    def test_read_only_claude_code_tools_exclude_mutation_and_git_automation(self):
        from agent.runtime import claude_code_read_only_allowed_tools

        tools = claude_code_read_only_allowed_tools()

        self.assertIn("Read", tools)
        self.assertIn("Glob", tools)
        self.assertIn("mcp__galatea-platform__list_training_projects", tools)
        self.assertNotIn("Bash", tools)
        self.assertNotIn("Write", tools)
        self.assertNotIn("Bash(git push:*)", tools)


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
    async def test_hook_manager_preserves_sdk_callback_without_adapter(self):
        manager = HookManager()
        seen_sessions = []

        async def deny_hook(input_data, tool_use_id, sdk_context):
            del sdk_context
            seen_sessions.append(input_data["session_id"])
            return {
                "reason": "unit test",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "blocked",
                },
            }

        manager.add_hook("PreToolUse", deny_hook, matcher="Bash")

        sdk_hooks = manager.to_sdk_hooks()
        callback = sdk_hooks["PreToolUse"][0].hooks[0]
        self.assertIs(callback, deny_hook)
        sdk_output = await callback(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sdk-session",
                "transcript_path": "/tmp/transcript.jsonl",
                "cwd": "/tmp",
                "tool_name": "Bash",
                "tool_input": {"command": "touch x"},
                "tool_use_id": "toolu_123",
            },
            "toolu_123",
            {"signal": None},
        )
        self.assertEqual(
            sdk_output["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )
        self.assertEqual(seen_sessions[-1], "sdk-session")

    async def test_custom_hooks_extend_instead_of_replacing_safety_hooks(self):
        async def stop_hook(input_data, tool_use_id, sdk_context):
            del input_data, tool_use_id, sdk_context
            return {}

        custom = HookManager()
        custom.add_hook("Stop", stop_hook)
        runtime = GalateaSDKRuntime(
            AgentSDKConfig(project_root=Path.cwd(), auto_load_config=False),
            hook_manager=custom,
        )
        hooks = runtime.build_options().hooks
        self.assertIn("Stop", hooks)
        self.assertIn("PreToolUse", hooks)
        self.assertIn("PermissionRequest", hooks)


class TestToolExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_direct_tool_execution_and_permission_denial(self):
        @tool("echo", "Echo text", {"text": str})
        async def echo(args):
            return {"content": [{"type": "text", "text": args["text"]}]}

        registry = SdkMcpToolRegistry([echo])
        policy = PermissionPolicy.for_galatea(allowed_tools=["echo"], disallowed_tools=[])
        executor = DeterministicMcpToolExecutor(registry, policy)

        result = await executor.execute("echo", {"text": "hello"})
        self.assertFalse(result.is_error)
        self.assertEqual(result.content[0]["text"], "hello")

        denied = DeterministicMcpToolExecutor(
            registry,
            PermissionPolicy.for_galatea(disallowed_tools=[]),
        )
        with self.assertRaises(PermissionDeniedError):
            await denied.execute("echo", {"text": "no"})


class TestMcpServer(unittest.IsolatedAsyncioTestCase):
    async def test_galatea_mcp_server_lists_and_calls_tool(self):
        from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest
        from agent.tools.server import create_galatea_mcp_server

        server_config = create_galatea_mcp_server()
        server = server_config["instance"]
        list_response = await server.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
        listed_tools = {tool.name: tool for tool in list_response.root.tools}
        tool_names = set(listed_tools)
        self.assertIn("list_training_projects", tool_names)
        self.assertTrue(listed_tools["list_training_projects"].annotations.readOnlyHint)
        self.assertFalse(listed_tools["list_training_projects"].annotations.destructiveHint)

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


class TestSkills(unittest.TestCase):
    def test_skill_registry_discovers_claude_codex_and_plugin_skills(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            project_skill = project_root / ".claude" / "skills" / "reviewer"
            project_skill.mkdir(parents=True)
            (project_skill / "SKILL.md").write_text(
                """---
name: Review Helper
description: Review project changes with repository conventions.
allowed-tools: Read,Grep
paths: "agent/**, train-model/**"
---

# Review Helper
Use this for reviews.
""",
                encoding="utf-8",
            )

            codex_skill = project_root / ".codex" / "skills" / "ray"
            codex_skill.mkdir(parents=True)
            (codex_skill / "SKILL.md").write_text(
                """---
name: ray
description: Ray workflow guidance.
---

# Ray
Use this for Ray work.
""",
                encoding="utf-8",
            )
            plugin_manifest = project_root / ".claude-plugin"
            plugin_manifest.mkdir()
            (plugin_manifest / "plugin.json").write_text(
                '{"name": "galatea-skills", "skills": "./.codex/skills"}\n',
                encoding="utf-8",
            )

            registry = SkillRegistry(project_root)
            discovered = {
                skill.name: skill
                for skill in registry.discover(
                    include_legacy_codex=True,
                    include_plugin=True,
                )
            }

            self.assertIn("reviewer", discovered)
            self.assertIn("ray", discovered)
            self.assertIn("galatea-skills:ray", discovered)
            self.assertEqual(discovered["reviewer"].description, "Review project changes with repository conventions.")

            report = registry.preflight(["reviewer"], include_plugin=False)
            self.assertTrue(report.is_valid)
            self.assertEqual(report.requested, ("reviewer",))
            self.assertEqual(report.missing, ())

    def test_runtime_delegates_skill_rules_and_discovery_defaults_to_sdk(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            skill_dir = project_root / ".claude" / "skills" / "reviewer"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: reviewer\ndescription: Review code.\n---\n# Reviewer\n",
                encoding="utf-8",
            )

            runtime = GalateaSDKRuntime(
                AgentSDKConfig(
                    project_root=project_root,
                    tools=[],
                    allowed_tools=[],
                    disallowed_tools=[],
                    skills=["reviewer"],
                    max_budget_usd=1.0,
                    auto_load_config=False,
                )
            )

            options = runtime.build_options()
            self.assertEqual(options.skills, ["reviewer"])
            self.assertEqual(options.tools, [])
            self.assertIsNone(options.setting_sources)
            self.assertNotIn("Skill(reviewer)", options.allowed_tools)

            from claude_agent_sdk._internal.transport.subprocess_cli import (
                SubprocessCLITransport,
            )

            transport = SubprocessCLITransport(prompt="test", options=options)
            sdk_allowed_tools, sdk_setting_sources = transport._apply_skills_defaults()
            self.assertIn("Skill(reviewer)", sdk_allowed_tools)
            self.assertEqual(sdk_setting_sources, ["user", "project"])
            self.assertEqual(
                runtime.permission_policy.check_permission("Skill", {"skill": "reviewer"}),
                "defer",
            )

    def test_predefined_agents_preload_relevant_skills(self):
        from agent.agents import DATA_PREPARER, TRAINING_ORCHESTRATOR

        self.assertIn("ray", DATA_PREPARER.skills)
        self.assertIn("mlflow-optimize-models", TRAINING_ORCHESTRATOR.skills)

    def test_repository_agent_skills_have_native_claude_discovery_paths(self):
        from agent.agents import (
            DATA_PREPARER,
            DOCUMENTATION_GENERATOR,
            EXPERIMENT_ANALYZER,
            MODEL_EVALUATOR,
            PLATFORM_INSPECTOR,
            TRAINING_ORCHESTRATOR,
        )

        requested = sorted(
            {
                skill_name
                for definition in (
                    DATA_PREPARER,
                    DOCUMENTATION_GENERATOR,
                    EXPERIMENT_ANALYZER,
                    MODEL_EVALUATOR,
                    PLATFORM_INSPECTOR,
                    TRAINING_ORCHESTRATOR,
                )
                for skill_name in definition.skills or []
            }
        )
        report = SkillRegistry(Path.cwd()).preflight(
            requested,
            include_plugin=False,
        )
        self.assertTrue(report.is_valid, report.missing)
        self.assertTrue(all(skill.source == "project" for skill in report.discovered))


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

    def test_claude_code_runtime_options_allow_base_tools(self):
        from agent.runtime import (
            GalateaRuntime,
            claude_code_tools_preset,
            unsafe_full_claude_code_allowed_tools,
        )

        runtime = GalateaRuntime(
            project_root=Path.cwd(),
            auto_load_config=False,
            tools=claude_code_tools_preset(),
            allowed_tools=unsafe_full_claude_code_allowed_tools(),
            disallowed_tools=[],
            permission_mode="dontAsk",
        )

        options = runtime.sdk_runtime.build_options()
        self.assertEqual(options.tools, {"type": "preset", "preset": "claude_code"})
        self.assertEqual(options.disallowed_tools, [])
        self.assertIn("Bash", options.allowed_tools)
        self.assertIn("Write", options.allowed_tools)
        self.assertIn("mcp__galatea-platform__inspect_ray_status", options.allowed_tools)
        self.assertEqual(
            runtime.sdk_runtime.permission_policy.check_permission(
                "Write",
                {"file_path": str(Path.cwd() / "agent/test/hello world.json")},
            ),
            "allow",
        )

    def test_permission_policy_parses_scoped_bash_rules(self):
        policy = PermissionPolicy.for_galatea(
            allowed_tools=[
                "Bash(git status:*)",
                "Bash(git push:*)",
            ],
            disallowed_tools=[],
        )

        self.assertEqual(
            policy.check_permission("Bash", {"command": "git status --short"}),
            "allow",
        )
        self.assertEqual(
            policy.check_permission("Bash", {"command": "git push origin feature"}),
            "allow",
        )
        self.assertEqual(
            policy.check_permission("Bash", {"command": "git reset --hard HEAD"}),
            "defer",
        )

    def test_bypass_permissions_requires_explicit_elevation(self):
        with self.assertRaisesRegex(ValueError, "allow_bypass_permissions"):
            GalateaSDKRuntime(
                AgentSDKConfig(
                    project_root=Path.cwd(),
                    permission_mode="bypassPermissions",
                    auto_load_config=False,
                )
            )

        runtime = GalateaSDKRuntime(
            AgentSDKConfig(
                project_root=Path.cwd(),
                permission_mode="bypassPermissions",
                allow_bypass_permissions=True,
                auto_load_config=False,
            )
        )
        self.assertEqual(runtime.build_options().permission_mode, "bypassPermissions")

    def test_sdk_permission_flows_are_passed_through(self):
        from claude_agent_sdk import create_sdk_mcp_server

        async def approve(tool_name, tool_input, context):
            from claude_agent_sdk import PermissionResultAllow

            del tool_name, tool_input, context
            return PermissionResultAllow()

        runtime = GalateaSDKRuntime(
            AgentSDKConfig(
                project_root=Path.cwd(),
                permission_mode="default",
                can_use_tool=approve,
                auto_load_config=False,
            )
        )
        options = runtime.build_options()
        self.assertIsNot(options.can_use_tool, approve)

        async def invoke_approval():
            from claude_agent_sdk import ToolPermissionContext

            return await options.can_use_tool(
                "Read",
                {"file_path": "README.md"},
                ToolPermissionContext(
                    tool_use_id="toolu-sdk-approval",
                    decision_reason="Read requires confirmation.",
                ),
            )

        decision = asyncio.run(invoke_approval())
        self.assertEqual(decision.behavior, "allow")
        evidence = runtime.hook_context.metadata["approval_decisions"][0]
        self.assertEqual(evidence["status"], "allowed")
        self.assertEqual(evidence["scope"], {"file_path": "README.md"})

        prompt_runtime = GalateaSDKRuntime(
            AgentSDKConfig(
                project_root=Path.cwd(),
                permission_mode="default",
                permission_prompt_tool_name="mcp__approval__request",
                additional_mcp_servers={
                    "approval": create_sdk_mcp_server(name="approval", tools=[])
                },
                auto_load_config=False,
            )
        )
        prompt_options = prompt_runtime.build_options()
        self.assertEqual(
            prompt_options.permission_prompt_tool_name,
            "mcp__approval__request",
        )
        self.assertIn("approval", prompt_options.mcp_servers)

        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            GalateaSDKRuntime(
                AgentSDKConfig(
                    project_root=Path.cwd(),
                    permission_mode="default",
                    can_use_tool=approve,
                    permission_prompt_tool_name="mcp__approval__request",
                    auto_load_config=False,
                )
            )
        with self.assertRaisesRegex(ValueError, "cannot provide approvals"):
            GalateaSDKRuntime(
                AgentSDKConfig(
                    project_root=Path.cwd(),
                    permission_mode="dontAsk",
                    can_use_tool=approve,
                    auto_load_config=False,
                )
            )
        with self.assertRaisesRegex(ValueError, "cannot provide approvals"):
            GalateaSDKRuntime(
                AgentSDKConfig(
                    project_root=Path.cwd(),
                    permission_mode="dontAsk",
                    permission_prompt_tool_name="mcp__approval__request",
                    auto_load_config=False,
                )
            )

    def test_permission_request_hook_records_structured_evidence(self):
        runtime = GalateaSDKRuntime(
            AgentSDKConfig(
                project_root=Path.cwd(),
                permission_mode="default",
                auto_load_config=False,
            )
        )
        callback = runtime.build_options().hooks["PermissionRequest"][0].hooks[0]

        async def invoke():
            return await callback(
                {
                    "hook_event_name": "PermissionRequest",
                    "session_id": "session-approval",
                    "transcript_path": "/tmp/transcript.jsonl",
                    "cwd": str(Path.cwd()),
                    "tool_name": "Write",
                    "tool_input": {"file_path": "README.md"},
                    "permission_suggestions": [
                        {"type": "addRules", "destination": "session"}
                    ],
                },
                "toolu-approval",
                {"signal": None},
            )

        self.assertEqual(asyncio.run(invoke()), {})
        request = runtime.hook_context.metadata["approval_requests"][0]
        self.assertTrue(request["approval_request_id"].startswith("approval-"))
        self.assertEqual(request["scope"], {"file_path": "README.md"})
        self.assertEqual(request["tool_name"], "Write")
        self.assertEqual(request["status"], "requested")

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

    async def test_workflow_orchestrator_records_evidence_but_never_dispatches(self):
        from agent.workflows import WorkflowOrchestrator
        from agent.workflows.orchestrator import DATA_TRAINING_WORKFLOW

        orchestrator = WorkflowOrchestrator(Path.cwd())
        await orchestrator.create_workflow("wf-state-only", DATA_TRAINING_WORKFLOW)
        started = await orchestrator.start_workflow("wf-state-only")
        self.assertEqual(started["current_stage"], "data")
        self.assertFalse(hasattr(orchestrator, "execute_stage"))
        self.assertFalse(hasattr(orchestrator, "execute_workflow"))

        after_data = await orchestrator.record_stage_result(
            "wf-state-only",
            "data",
            {"status": "success", "manifest_digest": "sha256:test"},
        )
        self.assertEqual(after_data["current_stage"], "training")
        completed = await orchestrator.record_stage_result(
            "wf-state-only",
            "training",
            {"status": "success", "mlflow_run_id": "run-1"},
        )
        self.assertEqual(completed["state"], "completed")



if __name__ == "__main__":
    unittest.main()
