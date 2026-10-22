import io
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from scripts import install_test_ha as installer
from scripts.package import ROOT


def test_latest_stable_excludes_preview_empty_and_yanked_releases():
    releases = {
        name: [{"yanked": False}]
        for name in ("2025.12.5", "2026.10.2", "2026.11.0b1", "2026.11.0rc1", "2027.1.0.dev1")
    }
    releases["2026.10.3"] = [{"yanked": True}]
    releases["2027.1.0"] = []
    assert installer.latest_stable(releases) == "2026.10.2"
    releases["2026.10.4"] = [{"yanked": False}]
    assert installer.latest_stable(releases) == "2026.10.4"
    with pytest.raises(ValueError):
        installer.latest_stable({"2026.11.0b1": [{"yanked": False}]})


@pytest.fixture
def install_environment(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"requirements": ["home-assistant-frontend==20261021.0"]}))
    metadata = {"releases": {"2026.10.2": [{"yanked": False}]}}
    fetch = Mock(side_effect=lambda *a, **kw: io.StringIO(json.dumps(metadata)))
    monkeypatch.setattr(installer, "urlopen", fetch)
    monkeypatch.setattr(
        installer,
        "distribution",
        Mock(return_value=SimpleNamespace(locate_file=lambda p: manifest)),
    )
    monkeypatch.setattr(installer, "version", Mock(return_value="2026.10.2"))
    run = Mock()
    monkeypatch.setattr(installer.subprocess, "run", run)
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    return fetch, run, summary, metadata


def test_install_exact_core_matching_frontend_and_report(install_environment, capsys):
    fetch, run, summary, metadata = install_environment
    installer.install()
    assert len(run.call_args_list) == 2
    for call in run.call_args_list:
        assert "homeassistant==2026.10.2" in call.args[0]
        assert call.kwargs["check"] is True
    assert "home-assistant-frontend==20261021.0" in run.call_args.args[0]
    assert "Installed Home Assistant Core under test: 2026.10.2" in capsys.readouterr().out
    assert "Installed Home Assistant Core under test: 2026.10.2" in summary.read_text()
    metadata["releases"]["2026.10.3"] = [{"yanked": False}]
    installer.version.return_value = "2026.10.3"
    installer.install()
    assert fetch.call_count == 2
    assert "homeassistant==2026.10.3" in run.call_args.args[0]


def test_incompatible_latest_fails_without_fallback(install_environment):
    _, run, summary, _ = install_environment
    run.side_effect = subprocess.CalledProcessError(1, ["pip", "install"])
    with pytest.raises(subprocess.CalledProcessError):
        installer.install()
    assert run.call_count == 1
    assert "2026.10.2" in summary.read_text()
    assert "Installed" not in summary.read_text()


def test_unexpected_installed_core_fails(install_environment, monkeypatch):
    monkeypatch.setattr(installer, "version", Mock(return_value="2025.12.5"))
    with pytest.raises(RuntimeError, match="Expected Home Assistant Core 2026.10.2"):
        installer.install()


def test_ci_runs_complete_suite_with_dynamic_core_and_keeps_docs_fast_path(tmp_path, monkeypatch):
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    events = workflow.get("on", workflow.get(True))
    assert events == {"push": {"branches": ["main"]}, "pull_request": None}
    assert len(workflow["jobs"]) == 1
    steps = workflow["jobs"]["validation"]["steps"]
    install_index = next(
        i for i, s in enumerate(steps) if "scripts.install_test_ha" in s.get("run", "")
    )
    test_steps = [s for s in steps if "pytest " in s.get("run", "")]
    assert len(test_steps) == 1
    assert steps.index(test_steps[0]) > install_index
    assert test_steps[0]["run"].splitlines()[0] == "pytest --cov --cov-report=term-missing"
    requirements = (ROOT / "requirements-test.txt").read_text().splitlines()
    assert not any(
        line.startswith(("homeassistant", "home-assistant-frontend")) for line in requirements
    )
    for step in steps:
        if any(
            token in str(step) for token in ("setup-python", "setup-node", "pip install", "pytest ")
        ):
            assert step["if"] == "steps.changes.outputs.code == 'true'"
    classify = next(s["run"] for s in steps if s.get("id") == "changes")
    monkeypatch.setenv("BASE", "a" * 40)
    monkeypatch.setenv("EVENT", "pull_request")
    for paths, expected in [
        (b"README.md\ndocs/guide.md\n", "false"),
        (b"tests/test_ha.py\n", "true"),
    ]:
        output = tmp_path / expected
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        monkeypatch.setattr(subprocess, "check_output", Mock(return_value=paths.decode()))
        exec(compile(classify, "CI change classification", "exec"), {})
        assert output.read_text() == f"code={expected}\n"
