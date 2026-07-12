"""CLI surface tests — no network required."""

from typer.testing import CliRunner

from ibee_cli.main import app

runner = CliRunner()


def test_help_lists_command_groups():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for group in ("buckets", "secrets", "vms", "gpus"):
        assert group in result.output


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "ibee-cli" in result.output


def test_subcommand_help():
    for args in (["buckets", "--help"], ["secrets", "--help"], ["vms", "--help"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0


def test_missing_token_is_clean_error(monkeypatch):
    for var in ("IBEE_TOKEN", "IBEE_API_TOKEN", "IBEE_WORKSPACE_ID"):
        monkeypatch.delenv(var, raising=False)
    result = runner.invoke(app, ["buckets", "list"])
    assert result.exit_code == 2
    assert "No API token" in result.output
