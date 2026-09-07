"""Official SDK boundary. Credentials are inherited from a sanitized worker."""
import json
from .types import TurnOutcome

class Adapter:
    def __init__(self, sdk=None, codex_bin=None):
        if sdk is None:
            import openai_codex as sdk
        self.sdk = sdk
        self.codex_bin = codex_bin
        self.handle = None

    def interrupt(self):
        if self.handle is not None:
            self.handle.interrupt()

    def run_turn(self, request, save_identity):
        sdk = self.sdk
        thread_id, turn_id = request.thread_id, None
        usage = None
        try:
            with sdk.Codex(sdk.CodexConfig(cwd=request.workspace, codex_bin=self.codex_bin)) as client:
                options = dict(cwd=request.workspace, sandbox=sdk.Sandbox.workspace_write,
                               approval_mode=sdk.ApprovalMode.deny_all)
                thread = (client.thread_resume(request.thread_id, **options) if request.thread_id
                          else client.thread_start(ephemeral=False, **options))
                thread_id = thread.id
                save_identity(thread_id, None)
                self.handle = thread.turn([
                    sdk.SkillInput(name='training-campaign', path=request.skill_path),
                    sdk.TextInput(text=request.prompt),
                ], output_schema=request.output_schema)
                turn_id = self.handle.id
                save_identity(thread_id, turn_id)
                result = self.handle.run()
                if result.usage is not None:
                    usage = {'thread_id':thread_id, 'total_tokens':result.usage.total.total_tokens}
                status = getattr(result.status, 'value', result.status)
                if status != 'completed' or result.error is not None or not result.final_response:
                    return TurnOutcome(thread_id, turn_id, 'failed', usage_snapshot=usage, error='Turn did not complete')
                value = json.loads(result.final_response)
                if not isinstance(value, dict): raise ValueError('Final response must be an object')
                return TurnOutcome(thread_id, turn_id, 'completed', value, usage)
        except Exception as exc:
            return TurnOutcome(thread_id, turn_id, 'failed', usage_snapshot=usage, error=type(exc).__name__)
        finally:
            self.handle = None
