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

function redactedPlan(developmentPlan) {
  const databaseUrl = developmentPlan.backend.environment.RECONCILIATION_DATABASE_URL;
  return {
    ...developmentPlan,
    backend: {
      ...developmentPlan.backend,
      environment: {
        ...developmentPlan.backend.environment,
        RECONCILIATION_DATABASE_URL: databaseUrl.replace(/:\/\/[^/@]+@/, "://***@"),
      },
    },
  };
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

function launchProcess(specification, options, spawnProcess) {
  const child = spawnProcess(specification.command, specification.args, options);
  const exited = new Promise((resolveExit) => {
    child.once("exit", (code, signal) => resolveExit({ code, signal }));
    child.once("error", (error) => resolveExit({ code: null, signal: null, error }));
  });
  const spawned = new Promise((resolveSpawn, rejectSpawn) => {
    const onSpawn = () => {
      child.removeListener("error", onError);
      resolveSpawn();
    };
    const onError = (error) => {
      child.removeListener("spawn", onSpawn);
      rejectSpawn(error);
    };
    child.once("spawn", onSpawn);
    child.once("error", onError);
  });
  return { child, exited, spawned };
}

function exitError(name, outcome) {
  if (outcome.error) return outcome.error;
  const reason = outcome.signal ? `signal ${outcome.signal}` : `code ${outcome.code ?? "unknown"}`;
  return new Error(`${name} 在开发环境启动期间退出（${reason}）`);
}

function isFilePath(command) {
  return command.includes("/") || command.includes("\\");
}

export async function startDevelopment({
  developmentPlan = plan,
  checkBackend = backendIsReady,
  waitUntilBackendReady = waitForBackend,
  verifyPath = access,
  spawnProcess = spawn,
  runtime = process,
} = {}) {
  let backendHandle;
  let frontendHandle;
  let stopping = false;
  const stop = (signal = "SIGTERM") => {
    if (stopping) return;
    stopping = true;
    runtime.removeListener("SIGINT", onSigint);
    runtime.removeListener("SIGTERM", onSigterm);
    for (const handle of [frontendHandle, backendHandle]) {
      const child = handle?.child;
      if (child && child.exitCode === null && child.signalCode === null && !child.killed) {
        child.kill(signal);
      }
    }
  };

  const onSigint = () => stop("SIGINT");
  const onSigterm = () => stop("SIGTERM");
  runtime.once("SIGINT", onSigint);
  runtime.once("SIGTERM", onSigterm);

  const handleExit = (name, outcome) => {
    if (stopping) return;
    runtime.exitCode = outcome.code && outcome.code > 0 ? outcome.code : 1;
    stop();
  };

  try {
    if (!(await checkBackend())) {
      if (isFilePath(developmentPlan.backend.command)) {
        try {
          await verifyPath(developmentPlan.backend.command);
        } catch {
          throw new Error(
            `未找到后端 Python 环境：${developmentPlan.backend.command}\n请先按 AGENTS.md 初始化 backend/.venv，或设置 BACKEND_PYTHON。`,
          );
        }
      }
      backendHandle = launchProcess(developmentPlan.backend, {
        cwd: backendDirectory,
        env: { ...process.env, ...developmentPlan.backend.environment },
        stdio: "inherit",
      }, spawnProcess);
      try {
        await backendHandle.spawned;
      } catch (error) {
        throw new Error(
          `无法启动后端 Python 命令：${developmentPlan.backend.command}`,
          { cause: error },
        );
      }
      await Promise.race([
        waitUntilBackendReady(),
        backendHandle.exited.then((outcome) => { throw exitError("后端服务", outcome); }),
      ]);
    }

    frontendHandle = launchProcess(developmentPlan.frontend, {
      cwd: frontendDirectory,
      env: process.env,
      stdio: "inherit",
    }, spawnProcess);
    await frontendHandle.spawned;

    void frontendHandle.exited.then((outcome) => handleExit("前端服务", outcome));
    if (backendHandle) {
      void backendHandle.exited.then((outcome) => handleExit("后端服务", outcome));
    }
    return { stop };
  } catch (error) {
    stop();
    throw error;
  }
}

const isDirectExecution = process.argv[1]
  && resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isDirectExecution) {
  if (process.env.RECONCILIATION_DEV_DRY_RUN === "1") {
    process.stdout.write(`${JSON.stringify(redactedPlan(plan))}\n`);
  } else {
    await startDevelopment();
  }
}
