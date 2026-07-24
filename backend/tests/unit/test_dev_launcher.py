import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("project_dev", ROOT / "dev.py")
assert SPEC is not None
assert SPEC.loader is not None
dev = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dev
SPEC.loader.exec_module(dev)


def make_project(tmp_path: Path) -> Path:
    (tmp_path / "backend/.venv/bin").mkdir(parents=True)
    (tmp_path / "backend/.venv/bin/python").touch()
    (tmp_path / "backend/.env").touch()
    (tmp_path / "frontend/node_modules").mkdir(parents=True)
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra/docker-compose.yml").touch()
    return tmp_path


def test_launch_plan_starts_complete_csv_agent_stack(tmp_path: Path) -> None:
    root = make_project(tmp_path)

    plan = dev.build_launch_plan(root)

    assert plan.setup[0].argv[-3:] == ("up", "-d", "--wait")
    assert plan.setup[1].argv[-4:] == ("-m", "alembic", "upgrade", "head")
    assert plan.services[0].argv[-2:] == ("--port", "8000")
    assert plan.services[1].argv[-2:] == ("-m", "app.agent_runtime")
    assert plan.services[2].argv[:3] == ("npm", "run", "dev:web")
    assert plan.environment == {
        "RECONCILIATION_NEW_AGENT_ENABLED": "true",
        "RECONCILIATION_AGENT_GRAPH_ENABLED": "true",
        "RECONCILIATION_AGENT_GRAPH_CSV_EXECUTION_ENABLED": "true",
        "RECONCILIATION_NEW_AGENT_ANALYSIS_ONLY": "false",
        "RECONCILIATION_NEW_AGENT_CSV_EXECUTION_ENABLED": "true",
        "RECONCILIATION_LLM_TIMEOUT_SECONDS": "60",
    }


def test_preflight_explains_that_ignored_backend_env_is_required(
    tmp_path: Path, monkeypatch
) -> None:
    root = make_project(tmp_path)
    (root / "backend/.env").unlink()
    monkeypatch.setattr(dev.shutil, "which", lambda _command: "/usr/bin/tool")

    with pytest.raises(dev.PreflightError, match=r"backend/\.env.*\.env\.example"):
        dev.validate_prerequisites(dev.build_launch_plan(root))


def test_preflight_rejects_ports_owned_by_existing_services(
    tmp_path: Path, monkeypatch
) -> None:
    make_project(tmp_path)
    monkeypatch.setattr(dev, "port_is_available", lambda _host, port: port != 8000)

    with pytest.raises(dev.PreflightError, match=r"8000.*占用"):
        dev.validate_ports()


def test_dry_run_contains_commands_but_never_reads_env_secrets(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = make_project(tmp_path)
    (root / "backend/.env").write_text(
        "RECONCILIATION_LLM_API_KEY=do-not-print-this\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dev.shutil, "which", lambda command: f"/usr/bin/{command}")

    exit_code = dev.main(["--root", str(root), "--dry-run"])

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 0
    assert payload["services"][1]["argv"][-1] == "app.agent_runtime"
    assert payload["environment"]["RECONCILIATION_NEW_AGENT_ENABLED"] == "true"
    assert "do-not-print-this" not in output


def test_supervisor_starts_components_in_required_order(tmp_path: Path) -> None:
    plan = dev.build_launch_plan(make_project(tmp_path))
    events: list[str] = []

    def run_command(command, _environment):
        events.append(f"run:{command.name}")

    def start_process(command, _environment):
        events.append(f"start:{command.name}")
        return SimpleNamespace(
            name=command.name,
            poll=lambda: None,
            terminate=lambda: None,
            wait=lambda timeout=None: 0,
            kill=lambda: None,
        )

    supervisor = dev.DevelopmentSupervisor(
        plan,
        command_runner=run_command,
        process_starter=start_process,
        http_waiter=lambda *_args: events.append("ready:FastAPI"),
        tcp_waiter=lambda *_args: events.append("ready:Vite"),
        browser_opener=lambda _url: events.append("open:browser"),
    )

    supervisor.start(open_browser=True)

    assert events == [
        "run:PostgreSQL",
        "run:数据库迁移",
        "start:FastAPI",
        "ready:FastAPI",
        "start:Agent worker",
        "start:Vite",
        "ready:Vite",
        "open:browser",
    ]


def test_unexpected_service_exit_stops_sibling_processes(tmp_path: Path) -> None:
    plan = dev.build_launch_plan(make_project(tmp_path))
    terminated: list[str] = []

    def start_process(command, _environment):
        exit_code = 7 if command.name == "FastAPI" else None
        return SimpleNamespace(
            name=command.name,
            poll=lambda: exit_code,
            terminate=lambda: terminated.append(command.name),
            wait=lambda timeout=None: exit_code or 0,
            kill=lambda: None,
        )

    supervisor = dev.DevelopmentSupervisor(
        plan,
        command_runner=lambda *_args: None,
        process_starter=start_process,
        http_waiter=lambda *_args: None,
        tcp_waiter=lambda *_args: None,
        browser_opener=lambda _url: None,
        sleep=lambda _seconds: None,
    )

    assert supervisor.run(open_browser=False) == 7
    assert terminated == ["Agent worker", "Vite"]
