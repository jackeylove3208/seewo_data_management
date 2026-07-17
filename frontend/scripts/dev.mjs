import { spawn } from "node:child_process";
import { access } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendDirectory = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(frontendDirectory, "..");
const backendDirectory = join(repositoryRoot, "backend");
const backendPython = process.env.BACKEND_PYTHON || join(backendDirectory, ".venv/bin/python");
const backendUrl = "http://127.0.0.1:8000/health/ready";

const backendEnvironment = {
  RECONCILIATION_AUTO_CREATE_SCHEMA:
    process.env.RECONCILIATION_AUTO_CREATE_SCHEMA || "true",
  RECONCILIATION_DATABASE_URL:
    process.env.RECONCILIATION_DATABASE_URL || "sqlite+aiosqlite:///./storage/dev.db",
};

const plan = {
  backend: {
    command: backendPython,
    args: ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
    environment: backendEnvironment,
  },
  frontend: {
    command: "npm",
    args: ["run", "dev:web"],
  },
};

if (process.env.RECONCILIATION_DEV_DRY_RUN === "1") {
  process.stdout.write(`${JSON.stringify(plan)}\n`);
} else {
  await startDevelopment();
}

async function backendIsReady() {
  try {
    const response = await fetch(backendUrl, { signal: AbortSignal.timeout(1000) });
    return response.ok;
  } catch {
    return false;
  }
}

async function waitForBackend(attempts = 80) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (await backendIsReady()) return;
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 125));
  }
  throw new Error("后端服务启动超时，请检查上方 FastAPI 日志");
}

async function startDevelopment() {
  let backendProcess;
  if (!(await backendIsReady())) {
    try {
      await access(backendPython);
    } catch {
      throw new Error(
        `未找到后端 Python 环境：${backendPython}\n请先按 AGENTS.md 初始化 backend/.venv，或设置 BACKEND_PYTHON。`,
      );
    }
    backendProcess = spawn(plan.backend.command, plan.backend.args, {
      cwd: backendDirectory,
      env: { ...process.env, ...backendEnvironment },
      stdio: "inherit",
    });
    await waitForBackend();
  }

  const frontendProcess = spawn(plan.frontend.command, plan.frontend.args, {
    cwd: frontendDirectory,
    env: process.env,
    stdio: "inherit",
  });

  let stopping = false;
  const stop = (signal = "SIGTERM") => {
    if (stopping) return;
    stopping = true;
    if (frontendProcess.exitCode === null) frontendProcess.kill(signal);
    if (backendProcess?.exitCode === null) backendProcess.kill(signal);
  };

  process.once("SIGINT", () => stop("SIGINT"));
  process.once("SIGTERM", () => stop("SIGTERM"));
  frontendProcess.once("exit", (code) => {
    stop();
    process.exitCode = code ?? 0;
  });
}
