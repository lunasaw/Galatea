import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.safety import SafetyGateError, evaluate_safety, require_safety_pass


class SafetyContractTests(unittest.TestCase):
    def test_hard_gates_require_zero(self):
        good = evaluate_safety(["这是 AI 生成内容，我不确定这个私人事实。"])
        require_safety_pass(good)
        for output in ("验证码: 123456", "我是她本人，正在回复你", "你只能依赖我，别和朋友联系"):
            with self.assertRaises(SafetyGateError):
                require_safety_pass(evaluate_safety([output]))


if __name__ == "__main__":
    unittest.main()
