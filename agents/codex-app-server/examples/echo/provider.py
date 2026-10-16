"""No business services or extra dependencies are needed for this extension."""
from dataclasses import dataclass

from codex_agent.providers import ToolProvider


@dataclass
class Echo:
    prefix: str

    def __call__(self, context, arguments):
        return {"text": self.prefix + arguments["text"]}


def create_provider(options):
    return ToolProvider(handlers={"echo": Echo(options.get("prefix", ""))})
