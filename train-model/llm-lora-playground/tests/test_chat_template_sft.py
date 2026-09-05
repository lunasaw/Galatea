import unittest

from llm_lora_playground.sft import LossMaskContractError, tokenize_conversation


class SpyTokenizer:
    pad_token_id = 0

    def __init__(self, with_mask=True):
        self.calls = []
        self.with_mask = with_mask

    def apply_chat_template(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        result = {"input_ids": [1, 2, 3, 4, 0]}
        if self.with_mask:
            result["assistant_tokens_mask"] = [0, 0, 1, 1, 0]
        return result


class ChatTemplateTests(unittest.TestCase):
    def test_chat_template_and_mask_are_explicit(self):
        tokenizer = SpyTokenizer()
        tokenized = tokenize_conversation(
            tokenizer,
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}],
            max_length=32,
        )
        self.assertEqual(tokenizer.calls[0][1]["add_generation_prompt"], False)
        self.assertEqual(tokenized.labels, [-100, -100, 3, 4, -100])

    def test_missing_span_fails_closed(self):
        with self.assertRaises(LossMaskContractError):
            tokenize_conversation(
                SpyTokenizer(with_mask=False),
                [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}],
                max_length=32,
            )


if __name__ == "__main__":
    unittest.main()
