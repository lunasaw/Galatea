import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.screenplay import ScreenplayContractError, build_event_card, generate_screenplay, edit_event_card, delete_event_card


class ScreenplayContractTests(unittest.TestCase):
    def test_event_card_has_source_lineage_and_safe_rewrite(self):
        card = build_event_card("e1", "familiar", "咖啡店", ["互相了解"], "从拘谨到放松", ["session-1"], "a" * 64)
        self.assertEqual("rewrite", card["rewrite_policy"])
        script = generate_screenplay([card], duration_seconds=120)
        self.assertEqual("e1", script["scenes"][0]["source_event_id"])
        self.assertNotIn("voice", script)

    def test_invalid_media_flags_and_unapproved_cards_fail_closed(self):
        with self.assertRaises(ScreenplayContractError):
            generate_screenplay([], duration_seconds=120, allow_voice=True)
        card = build_event_card("e1", "unknown", "scene", ["goal"], "turn", ["s"], "b" * 64)
        with self.assertRaises(ScreenplayContractError):
            generate_screenplay([card], duration_seconds=120)

    def test_edit_and_delete_are_lineage_preserving(self):
        card = build_event_card("e1", "familiar", "scene", ["goal"], "turn", ["s"], "c" * 64)
        edited = edit_event_card(card, scene="new scene")
        self.assertEqual("new scene", edited["scene"])
        deleted = delete_event_card([edited], "e1")
        self.assertEqual([], deleted)


if __name__ == "__main__":
    unittest.main()
