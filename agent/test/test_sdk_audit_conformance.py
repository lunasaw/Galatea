"""Regression tests for SDK/Claude source audit conformance."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


class TestSdkAuditDependencies(unittest.TestCase):
    def test_agent_requirements_pin_sdk_runtime_dependencies(self) -> None:
        requirements_path = Path("agent/requirements.txt")
        self.assertTrue(requirements_path.is_file())

        pinned = {
            line.strip()
            for line in requirements_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn("claude-agent-sdk==0.2.136", pinned)
        self.assertIn("mcp==1.26.0", pinned)
        self.assertIn("pydantic==2.12.3", pinned)
        self.assertIn("PyYAML==6.0.3", pinned)


class TestSdkSessionStoreBoundary(unittest.TestCase):
    def test_agent_state_store_is_not_accepted_as_sdk_session_store(self) -> None:
        from agent.core import AgentSDKConfig, GalateaSDKRuntime
        from agent.state import InMemoryAgentStateStore

        with tempfile.TemporaryDirectory() as tmpdir:
            state_store = InMemoryAgentStateStore()
            config = AgentSDKConfig(
                project_root=Path(tmpdir),
                session_store=state_store,
                auto_load_config=False,
            )

            with self.assertRaisesRegex(TypeError, "SDK SessionStore"):
                GalateaSDKRuntime(config)

    def test_sdk_session_store_duck_type_is_accepted(self) -> None:
        from agent.core import AgentSDKConfig, GalateaSDKRuntime

        class TranscriptStore:
            async def append(self, key, entries):
                self.key = key
                self.entries = entries

            async def load(self, key):
                return getattr(self, "entries", None)

        with tempfile.TemporaryDirectory() as tmpdir:
            store = TranscriptStore()
            runtime = GalateaSDKRuntime(
                AgentSDKConfig(
                    project_root=Path(tmpdir),
                    session_store=store,
                    auto_load_config=False,
                )
            )

        options = runtime.build_options()
        self.assertIs(options.session_store, store)
        self.assertIsNone(options.can_use_tool)


class TestProjectClaudeAgents(unittest.TestCase):
    def test_project_agent_prompts_are_removed_from_runtime_sources(self) -> None:
        agent_dir = Path(".claude/agents")
        self.assertFalse(
            agent_dir.exists() and any(agent_dir.glob("*.md")),
            ".claude/agents must not contain legacy prompt agents; use SDK AgentDefinition in code.",
        )

    def test_legacy_agent_definition_wrapper_is_removed(self) -> None:
        self.assertFalse(Path("agent/agents/definition.py").exists())

    def test_agent_registry_accepts_only_sdk_agent_definitions(self) -> None:
        from claude_agent_sdk import AgentDefinition
        from agent.agents.registry import AgentRegistry

        registry = AgentRegistry()
        definition = AgentDefinition(description="helper", prompt="help", permissionMode="dontAsk")
        registry.register("helper", definition, tags=["read-only"])

        self.assertIs(registry.get("helper"), definition)
        self.assertEqual(registry.to_sdk_agents(), {"helper": definition})
        self.assertEqual(registry.list(["read-only"]), ["helper"])

        with self.assertRaises(TypeError):
            registry.register("bad", object())  # type: ignore[arg-type]


class TestSdkThinAdapters(unittest.TestCase):
    def test_hook_events_match_sdk_supported_events(self) -> None:
        from agent.hooks import SDK_HOOK_EVENTS

        self.assertEqual(
            SDK_HOOK_EVENTS,
            {
                "UserPromptSubmit",
                "PreToolUse",
                "PostToolUse",
                "PostToolUseFailure",
                "Stop",
                "PreCompact",
                "SubagentStart",
                "SubagentStop",
                "Notification",
                "PermissionRequest",
            },
        )

    def test_hooks_export_sdk_types_without_local_input_output_models(self) -> None:
        import agent.hooks as hooks

        self.assertFalse(hasattr(hooks, "HookOutput"))
        self.assertFalse(hasattr(hooks, "HookRegistry"))
        self.assertEqual(hooks.HookInput.__module__, "types")
        self.assertEqual(hooks.HookMatcher.__module__, "claude_agent_sdk.types")

    def test_mcp_tool_name_static_introspection_is_not_exported(self) -> None:
        import agent.core as core
        import agent.core.sdk as sdk

        self.assertFalse(hasattr(core, "mcp_tool_names"))
        self.assertFalse(hasattr(sdk, "mcp_tool_names"))

    def test_runtime_has_no_local_skill_authorization_config(self) -> None:
        import agent.skills as skills
        from agent.core import AgentSDKConfig, GalateaSDKRuntime

        self.assertFalse(hasattr(skills, "SkillRuntimeConfig"))
        self.assertFalse(hasattr(skills, "skill_permission_rules"))
        runtime = GalateaSDKRuntime(
            AgentSDKConfig(
                project_root=Path.cwd(),
                skills=["ray"],
                auto_load_config=False,
            )
        )
        options = runtime.build_options()
        self.assertEqual(options.skills, ["ray"])
        self.assertNotIn("Skill(ray)", options.allowed_tools)

    def test_repository_skill_plugin_manifest_points_to_existing_directory(self) -> None:
        manifest_path = Path(".claude-plugin/plugin.json")
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["name"], "galatea-skills")
        self.assertTrue(payload["skills"].startswith("./"))
        self.assertTrue((Path.cwd() / payload["skills"]).is_dir())

    def test_sdk_plugins_are_explicit_and_passed_through(self) -> None:
        from agent.core import AgentSDKConfig, GalateaSDKRuntime

        plugin = {"type": "local", "path": str(Path.cwd())}
        runtime = GalateaSDKRuntime(
            AgentSDKConfig(
                project_root=Path.cwd(),
                plugins=[plugin],
                auto_load_config=False,
            )
        )
        self.assertEqual(runtime.build_options().plugins, [plugin])

    def test_direct_executor_is_not_a_public_tool_runtime(self) -> None:
        import agent.tools as tools
        from agent.tools.executor import inspection_test_executor
        from agent.tools.server import INSPECTION_TOOLS

        self.assertFalse(hasattr(tools, "ToolExecutor"))
        self.assertFalse(hasattr(tools, "DeterministicMcpToolExecutor"))
        executor = inspection_test_executor()
        self.assertEqual(
            executor.registry.list(),
            sorted(sdk_tool.name for sdk_tool in INSPECTION_TOOLS),
        )

    def test_mcp_tools_declare_read_only_annotations(self) -> None:
        from agent.tools.server import INSPECTION_TOOLS

        for sdk_tool in INSPECTION_TOOLS:
            self.assertIsNotNone(sdk_tool.annotations)
            self.assertTrue(sdk_tool.annotations.readOnlyHint)
            self.assertFalse(sdk_tool.annotations.destructiveHint)
            self.assertTrue(sdk_tool.annotations.idempotentHint)


class TestInspectPlatformCli(unittest.IsolatedAsyncioTestCase):
    async def test_cli_delegates_to_galatea_runtime(self) -> None:
        from agent.scripts import inspect_platform as cli

        calls = []

        class FakeRuntime:
            def __init__(self, project_root: Path, **kwargs) -> None:
                self.project_root = project_root
                self.kwargs = kwargs
                calls.append(("init", project_root, kwargs))

            async def __aenter__(self):
                calls.append(("enter", self.project_root, {}))
                return self

            async def __aexit__(self, *args):
                calls.append(("exit", self.project_root, {}))

            async def inspect_platform(self, *, detailed: bool = False):
                calls.append(("inspect", self.project_root, {"detailed": detailed}))
                return {
                    "status": "success",
                    "response": "runtime inspection ok",
                    "tool_calls": ["mcp__galatea-platform__list_training_projects"],
                    "cost_usd": 0.0,
                    "tokens": 3,
                }

        project_root = Path("/tmp/galatea-audit-cli")
        buffer = io.StringIO()
        with patch.object(cli, "GalateaRuntime", FakeRuntime), redirect_stdout(buffer):
            result = await cli.inspect_platform(detailed=True, project_root=project_root)

        self.assertEqual(result["response"], "runtime inspection ok")
        self.assertIn("runtime inspection ok", buffer.getvalue())
        self.assertEqual(
            calls,
            [
                ("init", project_root, {"auto_load_config": True}),
                ("enter", project_root, {}),
                ("inspect", project_root, {"detailed": True}),
                ("exit", project_root, {}),
            ],
        )


class TestPlaceholderStageCli(unittest.TestCase):
    def test_planned_stage_clis_return_unsupported_json(self) -> None:
        from agent.scripts import run_data_stage, run_inference_stage, run_training_stage

        for module, stage in [
            (run_data_stage, "data"),
            (run_training_stage, "training"),
            (run_inference_stage, "inference"),
        ]:
            buffer = io.StringIO()
            with patch("sys.argv", [module.__file__, "--json"]), redirect_stdout(buffer):
                exit_code = module.main()
            payload = json.loads(buffer.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertEqual(payload["status"], "unsupported")
            self.assertEqual(payload["stage"], stage)
            self.assertTrue(payload["planned_tools"])


class TestBaseFoundationHasNoPatrolBusinessLayer(unittest.TestCase):
    def test_active_agent_tree_has_no_patrol_business_modules(self) -> None:
        ignored_parts = {"__pycache__", "summary", "archive"}
        offenders = []
        for path in Path("agent").rglob("*"):
            if any(part in ignored_parts for part in path.parts):
                continue
            if "patrol" in path.name.lower():
                offenders.append(str(path))
        self.assertEqual(offenders, [])

    def test_active_agent_code_does_not_import_patrol_business_modules(self) -> None:
        ignored_parts = {"__pycache__", "summary", "archive"}
        patterns = (
            "agent.patrol",
            "agent.schemas.patrol",
            "agent.state.patrol",
            "agent.policies.patrol",
            "agent.workflows.patrol",
            "agent.tools.patrol_output",
        )
        offenders = []
        for path in Path("agent").rglob("*.py"):
            if any(part in ignored_parts for part in path.parts):
                continue
            if path == Path(__file__).relative_to(Path.cwd()):
                continue
            text = path.read_text(encoding="utf-8")
            if any(pattern in text for pattern in patterns):
                offenders.append(str(path))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
