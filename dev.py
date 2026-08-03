#!/usr/bin/env python3
"""Start the complete local CSV Agent development stack."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

AGENT_ENVIRONMENT = {
    "RECONCILIATION_NEW_AGENT_ENABLED": "true",
    "RECONCILIATION_AGENT_GRAPH_ENABLED": "true",
    "RECONCILIATION_AGENT_GRAPH_CSV_EXECUTION_ENABLED": "true",
    "RECONCILIATION_NEW_AGENT_ANALYSIS_ONLY": "false",
    "RECONCILIATION_NEW_AGENT_CSV_EXECUTION_ENABLED": "true",
    "RECONCILIATION_LLM_TIMEOUT_SECONDS": "120",
}


class PreflightError(RuntimeError):
    """A local prerequisite is missing or unsafe."""


@dataclass(frozen=True)
class CommandSpec:
    name: str
    argv: tuple[str, ...]
    cwd: Path


@dataclass(frozen=True)
class LaunchPlan:
    root: Path
    environment: dict[str, str]
    setup: tuple[CommandSpec, ...]
    services: tuple[CommandSpec, ...]


def _child_environment(overrides: dict[str, str]) -> dict[str, str]:
    return {**os.environ, **overrides}


def run_checked(command: CommandSpec, environment: dict[str, str]) -> None:
    subprocess.run(
        command.argv,
        cwd=command.cwd,
        env=_child_environment(environment),
        check=True,
    )


def start_process(
    command: CommandSpec, environment: dict[str, str]
) -> subprocess.Popen[Any]:
    return subprocess.Popen(
        command.argv,
        cwd=command.cwd,
        env=_child_environment(environment),
        start_new_session=True,
    )


def _wait_for_http(url: str, process: Any, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(f"FastAPI 在就绪前退出（code {exit_code}）")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if 200 <= response.status < 300:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise RuntimeError("FastAPI 启动超时，请检查上方日志")


def _wait_for_tcp(host: str, port: int, process: Any, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(f"Vite 在就绪前退出（code {exit_code}）")
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("Vite 启动超时，请检查上方日志")


def _terminate_process(process: Any) -> None:
    if process.poll() is not None:
        return
    pid = getattr(process, "pid", None)
    if pid is not None:
        try:
            os.killpg(pid, signal.SIGTERM)
            return
        except ProcessLookupError:
            return
    process.terminate()


class DevelopmentSupervisor:
    def __init__(
        self,
        plan: LaunchPlan,
        *,
        command_runner: Callable[[CommandSpec, dict[str, str]], None] = run_checked,
        process_starter: Callable[[CommandSpec, dict[str, str]], Any] = start_process,
        http_waiter: Callable[[str, Any], None] = _wait_for_http,
        tcp_waiter: Callable[[str, int, Any], None] = _wait_for_tcp,
        browser_opener: Callable[[str], object] = webbrowser.open,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.plan = plan
        self.command_runner = command_runner
        self.process_starter = process_starter
        self.http_waiter = http_waiter
        self.tcp_waiter = tcp_waiter
        self.browser_opener = browser_opener
        self.sleep = sleep
        self.processes: list[Any] = []

    def start(self, *, open_browser: bool) -> None:
        for command in self.plan.setup:
            print(f"\n==> {command.name}")
            self.command_runner(command, self.plan.environment)

        backend = self.process_starter(self.plan.services[0], self.plan.environment)
        self.processes.append(backend)
        self.http_waiter("http://127.0.0.1:8000/health/ready", backend)

        worker = self.process_starter(self.plan.services[1], self.plan.environment)
        self.processes.append(worker)

        frontend = self.process_starter(self.plan.services[2], self.plan.environment)
        self.processes.append(frontend)
        self.tcp_waiter("127.0.0.1", 5173, frontend)
        if open_browser:
            self.browser_opener("http://127.0.0.1:5173")

    def shutdown(self) -> None:
        for process in self.processes:
            _terminate_process(process)
        for process in self.processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pid = getattr(process, "pid", None)
                if pid is not None:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()

    def run(self, *, open_browser: bool) -> int:
        try:
            self.start(open_browser=open_browser)
            print(
                "\n项目已启动：http://127.0.0.1:5173 "
                "（API：http://127.0.0.1:8000/docs）。按 Ctrl+C 停止。"
            )
            while True:
                for process in self.processes:
                    exit_code = process.poll()
                    if exit_code is not None:
                        name = getattr(process, "name", "服务")
                        print(f"{name} 已退出（code {exit_code}）", file=sys.stderr)
                        return exit_code if exit_code != 0 else 1
                self.sleep(0.25)
        except KeyboardInterrupt:
            return 0
        finally:
            self.shutdown()


def build_launch_plan(root: Path) -> LaunchPlan:
    root = root.resolve()
    backend = root / "backend"
    frontend = root / "frontend"
    python = backend / ".venv/bin/python"
    compose = root / "infra/docker-compose.yml"
    setup = (
        CommandSpec(
            "PostgreSQL",
            ("docker", "compose", "-f", str(compose), "up", "-d", "--wait"),
            root,
        ),
        CommandSpec(
            "数据库迁移",
            (str(python), "-m", "alembic", "upgrade", "head"),
            backend,
        ),
    )
    services = (
        CommandSpec(
            "FastAPI",
            (
                str(python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--reload",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ),
            backend,
        ),
        CommandSpec(
            "Agent worker",
            (str(python), "-m", "app.agent_runtime"),
            backend,
        ),
        CommandSpec(
            "Vite",
            (
                "npm",
                "run",
                "dev:web",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                "5173",
                "--strictPort",
            ),
            frontend,
        ),
    )
    return LaunchPlan(
        root=root,
        environment=dict(AGENT_ENVIRONMENT),
        setup=setup,
        services=services,
    )


def validate_prerequisites(plan: LaunchPlan) -> None:
    required_paths = {
        plan.root / "backend/.env": (
            "找不到 backend/.env。该文件被 Git 忽略，不会自动复制到 worktree；"
            "请从 backend/.env.example 创建并填写本地模型配置。"
        ),
        plan.root / "backend/.venv/bin/python": (
            "找不到 backend/.venv/bin/python，请先创建后端 Python 3.12 虚拟环境。"
        ),
        plan.root / "frontend/node_modules": (
            "找不到 frontend/node_modules，请先在 frontend 目录运行 npm install。"
        ),
        plan.root / "infra/docker-compose.yml": "找不到 infra/docker-compose.yml。",
    }
    for path, message in required_paths.items():
        if not path.exists():
            raise PreflightError(message)
    for command in ("docker", "npm"):
        if shutil.which(command) is None:
            raise PreflightError(f"找不到 {command} 命令，请先安装并确保它位于 PATH。")


def port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def validate_ports() -> None:
    for port, service in ((8000, "FastAPI"), (5173, "Vite")):
        if not port_is_available("127.0.0.1", port):
            raise PreflightError(
                f"端口 {port} 已被占用，无法安全启动 {service}。"
                "请先停止旧服务，再重新运行 python3 dev.py。"
            )


def _plan_payload(plan: LaunchPlan) -> dict[str, object]:
    def command_payload(command: CommandSpec) -> dict[str, object]:
        return {
            "name": command.name,
            "argv": list(command.argv),
            "cwd": str(command.cwd),
        }

    return {
        "environment": plan.environment,
        "setup": [command_payload(command) for command in plan.setup],
        "services": [command_payload(command) for command in plan.services],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    arguments = parser.parse_args(argv)
    plan = build_launch_plan(arguments.root)
    validate_prerequisites(plan)
    if arguments.dry_run:
        print(json.dumps(_plan_payload(plan), ensure_ascii=False, indent=2))
        return 0
    validate_ports()
    return DevelopmentSupervisor(plan).run(open_browser=not arguments.no_browser)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PreflightError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"启动失败：{error}", file=sys.stderr)
        raise SystemExit(2) from error
