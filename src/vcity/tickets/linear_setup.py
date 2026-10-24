import argparse
import asyncio
from collections.abc import Iterable, Sequence

from vcity.config.departments import DEFAULT_DIRECTORY_PATH, load_directory
from vcity.settings import TicketSettings
from vcity.tickets.gaps import GAP_LABEL
from vcity.tickets.linear_client import LinearClient, LinearModel
from vcity.tickets.linear_sink import TEAM_KEY, find_team, team_labels

TEAM_NAME = "Corona 311"
# load_directory requires a transfer number; setup only needs department names.
_UNUSED_TRANSFER = "+10000000000"

_CREATE_TEAM = """
mutation CreateTeam($input: TeamCreateInput!) { teamCreate(input: $input) { team { id name } } }"""
_CREATE_LABEL = """
mutation CreateLabel($input: IssueLabelCreateInput!) {
  issueLabelCreate(input: $input) { issueLabel { id name } }
}"""


class _Node(LinearModel):
    id: str
    name: str


class _CreatedTeam(LinearModel):
    team: _Node


class _CreateTeamResult(LinearModel):
    team_create: _CreatedTeam


class _CreatedLabel(LinearModel):
    issue_label: _Node


class _CreateLabelResult(LinearModel):
    issue_label_create: _CreatedLabel


def label_names() -> list[str]:
    directory = load_directory(DEFAULT_DIRECTORY_PATH, transfer_to=_UNUSED_TRANSFER)
    return [info.name for info in directory.values()] + [GAP_LABEL]


async def ensure_team(client: LinearClient) -> str:
    if (team_id := await find_team(client, TEAM_KEY)) is not None:
        return team_id
    created = await client.execute(
        _CREATE_TEAM, {"input": {"name": TEAM_NAME, "key": TEAM_KEY}}, _CreateTeamResult
    )
    return created.team_create.team.id


async def ensure_labels(client: LinearClient, team_id: str, names: Iterable[str]) -> dict[str, str]:
    labels = await team_labels(client, team_id)
    for name in names:
        if name not in labels:
            created = await client.execute(
                _CREATE_LABEL, {"input": {"name": name, "teamId": team_id}}, _CreateLabelResult
            )
            labels[name] = created.issue_label_create.issue_label.id
    return labels


async def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Create the Corona 311 team and its labels.")
    parser.parse_args(argv)
    settings = TicketSettings.from_env()
    async with LinearClient.connect(settings.linear_api_key.get_secret_value()) as client:
        team_id = await ensure_team(client)
        labels = await ensure_labels(client, team_id, label_names())
    print(f"team {TEAM_KEY} ({team_id}) has labels: {', '.join(sorted(labels))}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
