from __future__ import annotations

import unittest

from llm_lora_playground.probes import (
    AdapterEffectError,
    AdapterProbe,
    assert_adapter_effective,
    probes_from_tokenizer,
)


class _Output:
    def __init__(self, logits):
        self.logits = logits


class _Model:
    def __init__(self):
        import torch

        self.enabled = True
        self.torch = torch

    def disable_adapter(self):
        from contextlib import contextmanager

        @contextmanager
        def context():
            old = self.enabled
            self.enabled = False
            try:
                yield
            finally:
                self.enabled = old

        return context()

    def __call__(self, input_ids, attention_mask=None):
        offset = 1.0 if self.enabled else 0.0
        return _Output(self.torch.ones((1, 2, 4)) * offset)


class ProbeTests(unittest.TestCase):
    def test_probe_requires_real_logit_difference(self):
        import torch

        model = _Model()
        result = assert_adapter_effective(model, [AdapterProbe("a", torch.ones((1, 2), dtype=torch.long))])
        self.assertEqual(result[0].probe_id, "a")

    def test_probe_fails_when_disabled_and_enabled_are_identical(self):
        import torch

        class Same(_Model):
            def __call__(self, input_ids, attention_mask=None):
                return _Output(self.torch.zeros((1, 2, 4)))

        with self.assertRaises(AdapterEffectError):
            assert_adapter_effective(Same(), [AdapterProbe("a", torch.ones((1, 2), dtype=torch.long))])

    def test_tokenizer_probe_tensors_move_to_declared_model_device(self):
        class Tensor:
            def __init__(self):
                self.device = None

            def to(self, device):
                self.device = device
                return self

        input_ids = Tensor()
        attention_mask = Tensor()

        class Tokenizer:
            def apply_chat_template(self, messages, **kwargs):
                return {"input_ids": input_ids, "attention_mask": attention_mask}

        probes = probes_from_tokenizer(
            Tokenizer(),
            [("device", [{"role": "user", "content": "hello"}])],
            device="cuda:0",
        )

        self.assertIs(probes[0].input_ids, input_ids)
        self.assertIs(probes[0].attention_mask, attention_mask)
        self.assertEqual(input_ids.device, "cuda:0")
        self.assertEqual(attention_mask.device, "cuda:0")


if __name__ == "__main__":
    unittest.main()
