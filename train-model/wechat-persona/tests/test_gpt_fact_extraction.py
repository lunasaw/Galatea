from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.gpt_fact_extraction import (  # noqa: E402
    GPTSettings,
    MODEL_OUTPUT_SCHEMA,
    WIRE_SCHEMA_MODE_COMPATIBLE,
    extract_facts,
    output_schema_digest,
    prompt_digest,
    wire_output_schema,
    wire_output_schema_digest,
)


class GPTFactExtractionTests(unittest.TestCase):
    def test_strict_response_is_validated_and_content_is_not_stored(self):
        model_output = {
            "facts": [
                {
                    "fact_key": "relationship.started_at",
                    "value": "2024-05-20",
                    "value_type": "date",
                    "date_precision": "day",
                    "date_basis": "explicit",
                    "claim_type": "explicit",
                    "polarity": "positive",
                    "confidence": 0.9,
                    "aliases": ["确定关系"],
                    "evidence": [
                        {"evidence_ref": "e_" + "1" * 20, "quote": "确定关系"}
                    ],
                    "unresolved_references": [],
                }
            ],
            "unresolved_references": [],
        }
        captured = {}

        def fake_request(url, payload, settings):
            captured.update(payload)
            return {
                "id": "response-fixture",
                "model": settings.model,
                "output_text": json.dumps(model_output, ensure_ascii=False),
                "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            }

        with patch("wechat_persona.gpt_fact_extraction._json_request", fake_request):
            result, metadata = extract_facts(
                {
                    "conversation_date": "2024-05-20",
                    "session_id": "session-a",
                    "primary_turns": [],
                    "neighbor_context": [],
                },
                GPTSettings(api_key="fixture-key", model="gpt-fixture", temperature=0),
            )
        self.assertEqual(result, model_output)
        self.assertEqual(metadata["usage"]["total_tokens"], 150)
        self.assertFalse(captured["store"])
        self.assertEqual(captured["temperature"], 0)
        self.assertTrue(captured["response_format"]["json_schema"]["strict"])
        self.assertEqual(len(prompt_digest()), 64)
        self.assertEqual(len(output_schema_digest()), 64)

    def test_compatible_wire_schema_keeps_shape_and_full_local_validation(self):
        settings = GPTSettings(
            api_key="fixture-key",
            wire_schema_mode=WIRE_SCHEMA_MODE_COMPATIBLE,
        )
        wire_schema = wire_output_schema(settings)
        self.assertEqual(wire_schema["required"], MODEL_OUTPUT_SCHEMA["required"])
        self.assertFalse(wire_schema["additionalProperties"])
        fact_schema = wire_schema["properties"]["facts"]["items"]
        self.assertEqual(
            fact_schema["required"],
            MODEL_OUTPUT_SCHEMA["properties"]["facts"]["items"]["required"],
        )
        self.assertFalse(fact_schema["additionalProperties"])
        self.assertNotIn("maxItems", wire_schema["properties"]["facts"])
        self.assertNotEqual(wire_output_schema_digest(settings), output_schema_digest())


if __name__ == "__main__":
    unittest.main()
