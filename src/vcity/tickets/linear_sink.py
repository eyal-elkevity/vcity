from collections.abc import Mapping
from datetime import datetime
from typing import Self

from vcity.domain.tickets import Priority, TicketRef
from vcity.tickets.drafts import GAP_TITLE_PREFIX, TicketDraft
from vcity.tickets.gaps import GAP_LABEL, Ask, GapTicket, parse_ask
from vcity.tickets.linear_client import LinearClient, LinearConflictError, LinearError, LinearModel

TEAM_KEY = "COR"

_FIND_TEAM = """
query FindTeam($key: String!) { teams(filter: {key: {eq: $key}}) { nodes { id name } } }"""
_LABELS = """
query Labels($team: ID!) {
  issueLabels(first: 100, filter: {team: {id: {eq: $team}}}) { nodes { id name } }
}"""
_ISSUE_FIELDS = "id identifier url title createdAt"
_CREATE = f"""
mutation CreateIssue($input: IssueCreateInput!) {{
  issueCreate(input: $input) {{ issue {{ {_ISSUE_FIELDS} }} }}
}}"""
_ISSUE = f"""
query Issue($id: String!) {{ issue(id: $id) {{ {_ISSUE_FIELDS} }} }}"""
_OPEN_GAPS = f"""
query OpenGaps($team: ID!, $label: String!) {{
  issues(
    first: 100
    filter: {{
      team: {{id: {{eq: $team}}}}
      labels: {{name: {{eq: $label}}}}
      state: {{type: {{nin: ["completed", "canceled"]}}}}
    }}
  ) {{ nodes {{ {_ISSUE_FIELDS} }} }}
}}"""
_COMMENT = """
mutation Comment($input: CommentCreateInput!) { commentCreate(input: $input) { success } }"""
_COMMENTS = """
query Comments($id: String!) {
  issue(id: $id) { comments(first: 250) { nodes { body createdAt } } }
}"""
_UPDATE = """
mutation Update($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success }
}"""


class _Named(LinearModel):
    id: str
    name: str


class _NamedNodes(LinearModel):
    nodes: list[_Named]


class _TeamsResult(LinearModel):
    teams: _NamedNodes


class _LabelsResult(LinearModel):
    issue_labels: _NamedNodes


class _Issue(LinearModel):
    id: str
    identifier: str
    url: str
    title: str
    created_at: datetime


class _IssueCreate(LinearModel):
    issue: _Issue


class _CreateResult(LinearModel):
    issue_create: _IssueCreate


class _IssueResult(LinearModel):
    issue: _Issue


class _Issues(LinearModel):
    nodes: list[_Issue]


class _IssuesResult(LinearModel):
    issues: _Issues


class _Comment(LinearModel):
    body: str
    created_at: datetime


class _Comments(LinearModel):
    nodes: list[_Comment]


class _IssueComments(LinearModel):
    comments: _Comments


class _CommentsResult(LinearModel):
    issue: _IssueComments


class _Success(LinearModel):
    success: bool


class _CommentResult(LinearModel):
    comment_create: _Success


class _UpdateResult(LinearModel):
    issue_update: _Success


class LinearSink:
    def __init__(self, client: LinearClient, team_id: str, label_ids: Mapping[str, str]) -> None:
        self._client = client
        self._team_id = team_id
        self._label_ids = dict(label_ids)

    @classmethod
    async def connect(cls, client: LinearClient, team_key: str = TEAM_KEY) -> Self:
        team_id = await find_team(client, team_key)
        if team_id is None:
            raise LinearError(
                f"Linear team {team_key} is missing; run python -m vcity.tickets.linear_setup"
            )
        return cls(client, team_id, await team_labels(client, team_id))

    async def create(self, draft: TicketDraft) -> TicketRef:
        issue = await self._create_issue(draft)
        return TicketRef(draft.reference, url=issue.url)

    async def open_gaps(self) -> list[GapTicket]:
        result = await self._client.execute(
            _OPEN_GAPS, {"team": self._team_id, "label": GAP_LABEL}, _IssuesResult
        )
        return [_gap(issue) for issue in result.issues.nodes]

    async def file_gap(self, draft: TicketDraft) -> GapTicket:
        return _gap(await self._create_issue(draft))

    async def add_ask(self, gap: GapTicket, body: str) -> None:
        result = await self._client.execute(
            _COMMENT, {"input": {"issueId": gap.id, "body": body}}, _CommentResult
        )
        if not result.comment_create.success:
            raise LinearError(f"Linear refused to comment on {gap.reference}")

    async def asks(self, gap: GapTicket) -> list[Ask]:
        result = await self._client.execute(_COMMENTS, {"id": gap.id}, _CommentsResult)
        parsed = (parse_ask(c.body, c.created_at) for c in result.issue.comments.nodes)
        return [ask for ask in parsed if ask is not None]

    async def update_gap(self, gap: GapTicket, description: str, priority: Priority) -> None:
        result = await self._client.execute(
            _UPDATE,
            {"id": gap.id, "input": {"description": description, "priority": int(priority)}},
            _UpdateResult,
        )
        if not result.issue_update.success:
            raise LinearError(f"Linear refused to update {gap.reference}")

    async def _create_issue(self, draft: TicketDraft) -> _Issue:
        unknown = draft.labels - self._label_ids.keys()
        if unknown:
            raise LinearError(f"labels missing in Linear: {sorted(unknown)}")
        payload = {
            "id": str(draft.id),
            "teamId": self._team_id,
            "title": draft.title,
            "description": draft.description,
            "priority": int(draft.priority),
            "labelIds": sorted(self._label_ids[label] for label in draft.labels),
        }
        try:
            result = await self._client.execute(_CREATE, {"input": payload}, _CreateResult)
        except LinearConflictError:
            existing = await self._client.execute(_ISSUE, {"id": str(draft.id)}, _IssueResult)
            return existing.issue
        return result.issue_create.issue


async def find_team(client: LinearClient, team_key: str) -> str | None:
    result = await client.execute(_FIND_TEAM, {"key": team_key}, _TeamsResult)
    return result.teams.nodes[0].id if result.teams.nodes else None


async def team_labels(client: LinearClient, team_id: str) -> dict[str, str]:
    result = await client.execute(_LABELS, {"team": team_id}, _LabelsResult)
    return {label.name: label.id for label in result.issue_labels.nodes}


def _gap(issue: _Issue) -> GapTicket:
    return GapTicket(
        id=issue.id,
        reference=issue.identifier,
        question=issue.title.removeprefix(GAP_TITLE_PREFIX),
        created_at=issue.created_at,
    )
