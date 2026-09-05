import unittest

from llm_lora_playground.datasets import TrainingSample
from llm_lora_playground.split import build_group_split, validate_split_manifest


def samples():
    return [
        TrainingSample(f"s{i}", f"g{i // 2}", [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}], {"style_label": "warm_brief", "generator_version": "v1", "seed": 1})
        for i in range(12)
    ]


class SplitIntegrityTests(unittest.TestCase):
    def test_groups_never_cross_splits_and_digest_is_stable(self):
        manifest = build_group_split(samples(), "scenario_id", (0.6, 0.2, 0.2), 42)
        validate_split_manifest(samples(), manifest)
        self.assertEqual(manifest.digest, build_group_split(samples(), "scenario_id", (0.6, 0.2, 0.2), 42).digest)
        memberships = {}
        for split, group_ids in manifest.group_ids_by_split.items():
            for group_id in group_ids:
                memberships.setdefault(group_id, set()).add(split)
        self.assertTrue(all(len(value) == 1 for value in memberships.values()))


if __name__ == "__main__":
    unittest.main()
