from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from wechat_persona.topic_repair_recovery_v2_compat import PROJECT_ROOT, resolve_recorded_path


class RepairRecoveryV2CompatTests(unittest.TestCase):
    def test_project_relative_evidence_paths_do_not_depend_on_cwd(self):
        self.assertEqual(
            PROJECT_ROOT / 'configs/topic-repair-recovery-v2.yaml',
            resolve_recorded_path('configs/topic-repair-recovery-v2.yaml'))
        absolute = PROJECT_ROOT / 'scripts/recover_topic_repairs_v2.py'
        self.assertEqual(absolute, resolve_recorded_path(str(absolute)))


if __name__ == '__main__':
    unittest.main()
