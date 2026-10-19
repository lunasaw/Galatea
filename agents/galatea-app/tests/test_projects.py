import json
import unittest
import test_domain
from galatea_app.projects import Projects


class FakeHost:
    def __init__(self):
        self.receipts = {}
        self.calls = 0

    async def activate(self, body):
        if body["activation_id"] not in self.receipts:
            self.calls += 1
            self.receipts[body["activation_id"]] = {
                "state": "ready",
                "session_id": "new-session",
                "thread_id": "new-thread",
                "workspace_ref": body["workspace_ref"],
            }
        return self.receipts[body["activation_id"]]


class ProjectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.base = test_domain.DomainTests()
        self.base.setUp()
        self.s = self.base.service
        self.host = FakeHost()
        self.projects = Projects(self.s, self.host, workspace_ref="work")

    def tearDown(self):
        self.base.tearDown()

    async def test_launch_is_durable_and_activation_creates_one_bound_task(self):
        user = self.base.user
        args = {
            "goal": "new data task",
            "inputs": [{"path": "input.csv"}],
            "constraints": {},
            "command_id": "launch",
        }
        first = await self.projects.call(user, "launch_project", args)
        second = await self.projects.call(user, "launch_project", args)
        self.assertEqual(first, second)
        await self.projects.tick()
        await self.projects.tick()
        self.assertEqual(self.host.calls, 1)
        result = await self.projects.call(
            user, "get_activation", {"activation_id": first["activation_id"]}
        )
        self.assertEqual(result["state"], "ready")
        task = self.s.get_task(user, result["task_id"])
        self.assertNotEqual(
            task["task"]["binding_id"], self.base.snapshot()["task"]["binding_id"]
        )
        with self.s.storage.lock:
            aggregate = self.s._load(self.s.storage.db, user, result["task_id"])
            self.assertEqual(aggregate["binding"]["session_id"], "new-session")
            self.assertEqual(aggregate["launch_receipt"], first["activation_id"])
            self.assertTrue(
                list(aggregate["inputs"].values())[0]["workspace_ref"] == "work"
            )

    async def test_directory_validation_precedes_host_creation(self):
        with self.assertRaises(Exception):
            await self.projects.call(
                self.base.user,
                "launch_project",
                {"goal": "bad", "inputs": [{"path": "/etc"}], "command_id": "bad"},
            )
        self.assertEqual(self.host.calls, 0)

    async def test_switch_is_deferred_until_host_write_receipt_can_complete(self):
        launched = await self.projects.call(
            self.base.user,
            "launch_project",
            {"goal": "new", "inputs": [{"path": "input.csv"}], "command_id": "launch"},
        )
        await self.projects.tick()
        ready = await self.projects.call(
            self.base.user,
            "get_activation",
            {"activation_id": launched["activation_id"]},
        )
        args = {
            "task_id": ready["task_id"],
            "project_id": ready["project_id"],
            "command_id": "switch",
        }
        pending = await self.projects.call(
            self.base.user, "activate_project_task", args
        )
        self.assertEqual(pending["state"], "binding")
        self.assertEqual(self.host.calls, 1)
        await self.projects.tick()
        self.assertEqual(self.host.calls, 2)
        self.assertEqual(
            (
                await self.projects.call(
                    self.base.user,
                    "get_activation",
                    {"activation_id": pending["activation_id"]},
                )
            )["state"],
            "ready",
        )
