import contextlib
import io
import unittest

from claude_agent_sdk import AssistantMessage, ToolUseBlock

from agent.scripts.interactive_chat import display_message


class TestInteractiveChatUnicode(unittest.TestCase):
    def test_tool_input_preserves_non_ascii_text(self):
        message = AssistantMessage(
            content=[
                ToolUseBlock(
                    id="toolu_test",
                    name="Read",
                    input={"description": "列出 agent 目录下的 Python 文件"},
                )
            ],
            model="test",
        )

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            display_message(message)

        rendered = output.getvalue()
        self.assertIn("列出 agent 目录下的 Python 文件", rendered)
        self.assertNotIn("\\u5217", rendered)


if __name__ == "__main__":
    unittest.main()
