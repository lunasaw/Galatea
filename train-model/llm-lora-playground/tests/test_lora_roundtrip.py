import unittest

from llm_lora_playground.lora import LoRAContractError, validate_target_modules


class TinyModel:
    def named_modules(self):
        return [("", self), ("layer.q_proj", object()), ("layer.v_proj", object())]


class LoraRoundtripTests(unittest.TestCase):
    def test_target_modules_must_exist(self):
        self.assertEqual(validate_target_modules(TinyModel(), ["q_proj", "v_proj"]), ["layer.q_proj", "layer.v_proj"])
        with self.assertRaises(LoRAContractError):
            validate_target_modules(TinyModel(), ["k_proj"])


if __name__ == "__main__":
    unittest.main()
