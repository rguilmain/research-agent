"""Deployment configuration guards: the Fly app and the deploy workflow must fail closed.

These read the repository's own files. They exist because the two mistakes that matter here
are silent: an app that quietly starts in demo mode, and a workflow that deploys a merge
without review. Neither needs a provider key, a database or a network call to catch.
"""

import re
import tomllib
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # transitive (uvicorn/langchain); no stubs published

ROOT = Path(__file__).resolve().parents[2]
FLY_TOML = ROOT / "fly.toml"
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "fly-deploy.yml"

APP_NAME = "research-agent-rosy-dawn-8813"
# `name@<40 hex characters>`; a tag or branch would not match.
PINNED_ACTION = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def fly_config() -> dict:
    with FLY_TOML.open("rb") as handle:
        return tomllib.load(handle)


def deploy_jobs() -> list[dict]:
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text(encoding="utf-8"))
    return list(workflow["jobs"].values())


def test_fly_app_runs_as_a_production_app_that_refuses_to_start():
    config = fly_config()
    assert config["app"] == APP_NAME
    # Production is what activates the readiness refusal. Demo mode would be a public,
    # unauthenticated research endpoint, so APP_ENV must not be left at its default.
    assert config["env"]["APP_ENV"] == "production"


def test_fly_app_scales_to_zero_and_probes_the_liveness_endpoint():
    service = fly_config()["http_service"]
    assert service["internal_port"] == 8000
    assert service["auto_stop_machines"] == "stop"
    assert service["auto_start_machines"] is True
    assert service["min_machines_running"] == 0
    checks = service["checks"]
    assert len(checks) == 1
    # /readyz touches PostgreSQL and would fail a deploy while a Machine is starting.
    assert checks[0]["path"] == "/healthz"
    assert checks[0]["method"] == "GET"


def test_fly_release_command_applies_packaged_migrations():
    assert fly_config()["deploy"]["release_command"] == "research-agent-migrate"


def test_deploy_workflow_is_manual_and_approval_gated():
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML reads the bare `on` key as the boolean True under YAML 1.1.
    triggers = workflow.get("on", workflow.get(True))
    assert list(triggers) == ["workflow_dispatch"]
    jobs = deploy_jobs()
    assert len(jobs) == 1
    # An environment with required reviewers is the approval gate; without it this job
    # would deploy on the click alone.
    assert jobs[0]["environment"] == "production"


def test_deploy_workflow_pins_its_actions_to_commit_shas():
    steps = [step for job in deploy_jobs() for step in job["steps"] if "uses" in step]
    assert steps, "the deploy job must use at least one action"
    for step in steps:
        reference = str(step["uses"])
        assert PINNED_ACTION.match(reference), f"{reference} is not pinned to a commit SHA"


def test_deploy_workflow_never_echoes_the_deploy_token():
    text = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    assert "FLY_API_TOKEN" in text
    # The token may only be passed as the action's environment, never printed.
    assert "flyctl auth token" not in text
