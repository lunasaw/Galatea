"""Unit tests for Galatea SDK core skeleton capabilities."""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from claude_agent_sdk import ResultMessage

from agent.hooks import HookContext, HookEvent, HookInput, HookManager, HookOutput
from agent.policies import BudgetExceededError, BudgetPolicy, PermissionDeniedError, PermissionPolicy, PermissionRule
from agent.core import AgentSDKConfig, GalateaSDKRuntime, SDKRunResult
from agent.skills import SkillRegistry
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

    def test_scoped_skill_rules_match_exact_and_prefix(self):
        policy = PermissionPolicy.for_galatea(
            allowed_tools=[
                "Skill(ray)",
                "Skill(mlflow-optimize-models:*)",
            ],
            disallowed_tools=[],
            mode="dontAsk",
        )

        self.assertEqual(policy.check_permission("Skill", {"skill": "ray"}), "allow")
        self.assertEqual(policy.check_permission("Skill", {"skill": "/ray"}), "allow")
        self.assertEqual(
            policy.check_permission("Skill", {"skill": "mlflow-optimize-models advanced"}),
            "allow",
        )
        self.assertEqual(policy.check_permission("Skill", {"skill": "unknown"}), "deny")


class TestCommandRegistry(unittest.TestCase):
    def test_commit_push_command_builds_scoped_plan(self):
        from agent.commands import CommandContext, default_command_registry

        registry = default_command_registry()
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = registry.build_plan(
                "/commit-push include only command abstraction changes",
                CommandContext(project_root=Path(tmpdir)),
            )

        self.assertEqual(plan.command_name, "commit-push")
        self.assertIn("## Git Safety Protocol", plan.prompt)
        self.assertIn("include only command abstraction changes", plan.prompt)
        self.assertIn("Bash(git push:*)", plan.allowed_tools)
        self.assertIn("Bash(git push --force*)", plan.disallowed_tools)
        self.assertEqual(plan.tools, {"type": "preset", "preset": "claude_code"})

    def test_registry_leaves_unknown_slash_paths_as_plain_prompts(self):
        from agent.commands import CommandContext, default_command_registry

        registry = default_command_registry()
        prompt = "/data/ai/chenzhangyue/code/galatea/agent/runtime.py 是否抽象"
        plan = registry.build_plan(prompt, CommandContext(project_root=Path.cwd()))

        self.assertIsNone(plan.command_name)
        self.assertEqual(plan.prompt, prompt)

    def test_runtime_exposes_command_plan_without_command_hardcoding(self):
        from agent.runtime import GalateaRuntime

        runtime = GalateaRuntime(project_root=Path.cwd(), auto_load_config=False)
        plan = runtime.build_command_plan("commit and push these changes")

        self.assertEqual(plan.command_name, "commit-push")
        self.assertIn("Return the\ncommit hash", plan.prompt)

    def test_command_runtime_applies_scoped_tools_only_when_command_matches(self):
        from agent.commands import (
            CommandContext,
            claude_code_read_only_allowed_tools,
            default_command_registry,
        )
        from agent.runtime import GalateaRuntime, claude_code_tools_preset

        registry = default_command_registry()
        runtime = GalateaRuntime(
            project_root=Path.cwd(),
            auto_load_config=False,
            tools=claude_code_tools_preset(),
            allowed_tools=claude_code_read_only_allowed_tools(),
            disallowed_tools=registry.disallowed_tools(),
            command_registry=registry,
        )
        base_options = runtime.sdk_runtime.build_options()
        self.assertNotIn("Bash(git push:*)", base_options.allowed_tools)

        plan = registry.build_plan("/commit-push", CommandContext(project_root=Path.cwd()))
        command_runtime = runtime._build_runtime_for_plan(plan)
        self.assertIsNotNone(command_runtime)
        assert command_runtime is not None
        command_options = command_runtime.build_options()

        self.assertIn("Bash(git push:*)", command_options.allowed_tools)
        self.assertIn("Bash(git push --force*)", command_options.disallowed_tools)

    def test_preexpanded_command_prompt_is_not_expanded_twice(self):
        from agent.commands import CommandContext, default_command_registry

        registry = default_command_registry()
        context = CommandContext(project_root=Path.cwd())
        first = registry.build_plan("/commit-push", context)
        second = registry.build_plan(first.prompt, context)

        self.assertEqual(first.command_name, "commit-push")
        self.assertIsNone(second.command_name)
        self.assertEqual(second.prompt, first.prompt)


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
            discovered = {skill.name: skill for skill in registry.discover()}

            self.assertIn("reviewer", discovered)
            self.assertIn("ray", discovered)
            self.assertIn("galatea-skills:ray", discovered)
            self.assertEqual(discovered["reviewer"].description, "Review project changes with repository conventions.")
            self.assertEqual(discovered["reviewer"].allowed_tools, ("Read", "Grep"))
            self.assertEqual(discovered["reviewer"].paths, ("agent", "train-model"))

            runtime_config = registry.resolve(["reviewer"], include_plugin=False)
            self.assertEqual(runtime_config.skills, ["reviewer"])
            self.assertEqual(runtime_config.allowed_tools, ("Skill(reviewer)",))

    def test_runtime_builds_sdk_native_skill_options_and_policy(self):
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
            self.assertEqual(options.tools, ["Skill"])
            self.assertEqual(options.setting_sources, ["project"])
            self.assertIn("Skill(reviewer)", options.allowed_tools)
            self.assertEqual(
                runtime.permission_policy.check_permission("Skill", {"skill": "reviewer"}),
                "allow",
            )
            self.assertEqual(
                runtime.permission_policy.check_permission("Skill", {"skill": "/reviewer"}),
                "allow",
            )
            self.assertEqual(
                runtime.permission_policy.check_permission("Skill", {"skill": "unknown"}),
                "deny",
            )

    def test_predefined_agents_preload_relevant_skills(self):
        from agent.agents import DATA_PREPARER, TRAINING_ORCHESTRATOR

        self.assertIn("ray", DATA_PREPARER.skills)
        self.assertIn("mlflow-optimize-models", TRAINING_ORCHESTRATOR.skills)


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
            claude_code_allowed_tools,
            claude_code_tools_preset,
        )

        runtime = GalateaRuntime(
            project_root=Path.cwd(),
            auto_load_config=False,
            tools=claude_code_tools_preset(),
            allowed_tools=claude_code_allowed_tools(),
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
            mode="dontAsk",
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
            "deny",
        )

    def test_git_commit_push_runtime_uses_narrow_bash_allowlist(self):
        from agent.runtime import (
            GalateaRuntime,
            git_commit_push_allowed_tools,
            git_commit_push_disallowed_tools,
            git_commit_push_system_prompt,
            claude_code_tools_preset,
        )

        allowed_tools = git_commit_push_allowed_tools()
        self.assertIn("Bash(git push:*)", allowed_tools)
        self.assertNotIn("Bash", allowed_tools)

        runtime = GalateaRuntime(
            project_root=Path.cwd(),
            auto_load_config=False,
            tools=claude_code_tools_preset(),
            allowed_tools=allowed_tools,
            disallowed_tools=git_commit_push_disallowed_tools(),
            permission_mode="dontAsk",
            system_prompt=git_commit_push_system_prompt(),
        )

        options = runtime.sdk_runtime.build_options()
        self.assertEqual(options.tools, {"type": "preset", "preset": "claude_code"})
        self.assertIn("Bash(git push:*)", options.allowed_tools)
        self.assertNotIn("Bash", options.allowed_tools)
        self.assertIn("commit and push code", str(options.system_prompt))
        self.assertEqual(
            runtime.sdk_runtime.permission_policy.check_permission(
                "Bash",
                {"command": "git push --set-upstream origin feature"},
            ),
            "allow",
        )
        self.assertEqual(
            runtime.sdk_runtime.permission_policy.check_permission(
                "Bash",
                {"command": "git push --force origin main"},
            ),
            "deny",
        )
        self.assertEqual(
            runtime.sdk_runtime.permission_policy.check_permission(
                "Bash",
                {"command": "python -c 'print(1)'"},
            ),
            "deny",
        )


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
