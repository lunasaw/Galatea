from unittest.mock import patch
import test_topic_blind_reference as fixtures
from wechat_persona import topic_blind_reference_opus as opus


class OpusReferenceTests(fixtures.BlindReferenceTests):
    def setUp(self):
        patcher = patch.object(fixtures.reference, 'run_reference', opus.run_reference)
        patcher.start()
        self.addCleanup(patcher.stop)

    def args(self, root):
        return {**super().args(root), 'config_path': fixtures.ROOT / 'configs/topic-blind-reference-opus-v1.yaml'}
