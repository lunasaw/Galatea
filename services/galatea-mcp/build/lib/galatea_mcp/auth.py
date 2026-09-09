"""Only transport/admin configuration may create a Principal."""
from dataclasses import dataclass
from .errors import DomainError


@dataclass(frozen=True)
class Principal:
    principal_id: str
    project_ids: frozenset[str]
    campaign_ids: frozenset[str]
    actions: frozenset[str]

    def check(self, action, project_id=None, campaign_id=None):
        if action not in self.actions and '*' not in self.actions:
            raise DomainError('forbidden')
        if project_id is not None and project_id not in self.project_ids:
            raise DomainError('forbidden')
        if campaign_id is not None and campaign_id not in self.campaign_ids:
            raise DomainError('forbidden')
