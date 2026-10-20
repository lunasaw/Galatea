import unittest
from galatea_app.integrations.governance_mcp import GovernanceMCP
from galatea_app.common import digest


class CampaignTests(unittest.TestCase):
    def test_matches_public_logical_operation_without_nonexistent_idempotency_field(
        self,
    ):
        identity = {
            "schema_version": "galatea.operation/v1",
            "campaign_id": "campaign",
            "step_id": "baseline",
            "attempt": 1,
        }
        operation = {
            "operation_id": "op-" + digest(identity)[:32],
            "step_id": "baseline",
            "attempt": 1,
            "role": "baseline",
            "release_id": "r1",
            "config_id": "c1",
            "submission_id": "job",
            "execution": "succeeded",
            "run_id": "run",
        }
        calls = []

        def call(name, args):
            calls.append(name)
            return {"ok": True, "data": operation}

        adapter = GovernanceMCP(
            call,
            project_id="project",
            campaign_id="campaign",
            authorization_check=lambda p: True,
        )
        intent = {
            "idempotency_key": "task-intent",
            "immutable_inputs": {
                "campaign_id": "campaign",
                "step_id": "baseline",
                "attempt": 1,
                "role": "baseline",
                "release_id": "r1",
                "config_id": "c1",
            },
        }
        self.assertTrue(adapter.owns(intent, {"id": operation["operation_id"]}))
        self.assertEqual(
            adapter.observe({"id": operation["operation_id"]})["status"], "succeeded"
        )
        self.assertNotIn("galatea_observe_job", calls)
        self.assertFalse(
            adapter.owns(
                {
                    **intent,
                    "immutable_inputs": {**intent["immutable_inputs"], "attempt": 2},
                },
                {"id": operation["operation_id"]},
            )
        )

    def test_http_transport_returns_structured_data_and_never_retries_mutation(self):
        import httpx
        from galatea_app.integrations.governance_mcp import MCPTransport

        calls = []

        def respond(request):
            import json

            body = json.loads(request.content)
            calls.append(body)
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "structuredContent": {
                            "ok": True,
                            "data": {"execution": "running"},
                        }
                    },
                },
            )

        transport = MCPTransport(
            "http://localhost/mcp",
            "fixture-private-token",
            transport=httpx.MockTransport(respond),
        )
        try:
            self.assertEqual(
                transport("galatea_get_operation", {"operation_id": "op"})["data"][
                    "execution"
                ],
                "running",
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["params"]["name"], "galatea_get_operation")
        finally:
            transport.close()
