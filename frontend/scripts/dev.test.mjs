import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { EventEmitter } from "node:events";

import { describe, expect, it } from "vitest";

const developmentModule = await import("./dev.mjs");

class FakeChildProcess extends EventEmitter {
  exitCode = null;
  signalCode = null;
  killed = false;

  kill() {
    this.killed = true;
    return true;
  }
}

function fakeRuntime() {
  const runtime = new EventEmitter();
  runtime.exitCode = undefined;
  return runtime;
}

const testPlan = {
  backend: {
    command: "/tmp/python",
    args: ["-m", "uvicorn"],
    environment: {},
  },
  frontend: {
    command: "npm",
    args: ["run", "dev:web"],
  },
};

describe("local development command", () => {
  it("starts the full local stack by default", async () => {
    const packageJson = JSON.parse(
      await readFile(resolve("package.json"), "utf8"),
    );

    expect(packageJson.scripts.dev).toBe("node scripts/dev.mjs");
    expect(packageJson.scripts["dev:web"]).toBe("vite");
  });

  it("plans a local SQLite API and Vite web process", () => {
    const result = spawnSync(process.execPath, ["scripts/dev.mjs"], {
      cwd: resolve("."),
      encoding: "utf8",
      env: { ...process.env, RECONCILIATION_DEV_DRY_RUN: "1" },
    });

    expect(result.status).toBe(0);
    const plan = JSON.parse(result.stdout);
    expect(plan.backend.args).toEqual([
      "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000",
    ]);
    expect(plan.backend.environment).toEqual({
      RECONCILIATION_AUTO_CREATE_SCHEMA: "true",
      RECONCILIATION_DATABASE_URL: "sqlite+aiosqlite:///./storage/dev.db",
    });
    expect(plan.frontend.command).toBe("npm");
    expect(plan.frontend.args).toEqual(["run", "dev:web"]);
  });

  it("redacts database credentials from the dry-run plan", () => {
    const result = spawnSync(process.execPath, ["scripts/dev.mjs"], {
      cwd: resolve("."),
      encoding: "utf8",
      env: {
        ...process.env,
        RECONCILIATION_DEV_DRY_RUN: "1",
        RECONCILIATION_DATABASE_URL: "postgresql+asyncpg://operator:secret@localhost/reconciliation",
      },
    });

    expect(result.status).toBe(0);
    expect(result.stdout).not.toContain("operator");
    expect(result.stdout).not.toContain("secret");
    expect(result.stdout).toContain("postgresql+asyncpg://***@localhost/reconciliation");
  });

  it("terminates the owned backend when readiness fails", async () => {
    const backend = new FakeChildProcess();
    const spawnProcess = () => {
      queueMicrotask(() => backend.emit("spawn"));
      return backend;
    };

    await expect(developmentModule.startDevelopment({
      developmentPlan: testPlan,
      checkBackend: async () => false,
      waitUntilBackendReady: async () => {
        throw new Error("not ready");
      },
      verifyPath: async () => {},
      spawnProcess,
      runtime: fakeRuntime(),
    })).rejects.toThrow("not ready");

    expect(backend.killed).toBe(true);
  });

  it("terminates the frontend when the owned backend exits", async () => {
    const backend = new FakeChildProcess();
    const frontend = new FakeChildProcess();
    const children = [backend, frontend];
    const spawnProcess = () => {
      const child = children.shift();
      queueMicrotask(() => child.emit("spawn"));
      return child;
    };
    const runtime = fakeRuntime();

    await developmentModule.startDevelopment({
      developmentPlan: testPlan,
      checkBackend: async () => false,
      waitUntilBackendReady: async () => {},
      verifyPath: async () => {},
      spawnProcess,
      runtime,
    });
    backend.exitCode = 1;
    backend.emit("exit", 1, null);
    await new Promise((resolveMicrotask) => queueMicrotask(resolveMicrotask));

    expect(frontend.killed).toBe(true);
    expect(runtime.exitCode).toBe(1);
  });

  it("allows a backend Python command to be resolved from PATH", async () => {
    const backend = new FakeChildProcess();
    const frontend = new FakeChildProcess();
    const children = [backend, frontend];
    const spawnProcess = () => {
      const child = children.shift();
      queueMicrotask(() => child.emit("spawn"));
      return child;
    };
    let verifiedPath;

    const development = await developmentModule.startDevelopment({
      developmentPlan: {
        ...testPlan,
        backend: { ...testPlan.backend, command: "python3.12" },
      },
      checkBackend: async () => false,
      waitUntilBackendReady: async () => {},
      verifyPath: async (command) => { verifiedPath = command; },
      spawnProcess,
      runtime: fakeRuntime(),
    });
    development.stop();

    expect(verifiedPath).toBeUndefined();
  });

  it("reports a backend command that cannot be started", async () => {
    const backend = new FakeChildProcess();
    const spawnProcess = () => {
      queueMicrotask(() => backend.emit("error", new Error("spawn ENOENT")));
      return backend;
    };

    await expect(developmentModule.startDevelopment({
      developmentPlan: {
        ...testPlan,
        backend: { ...testPlan.backend, command: "missing-python" },
      },
      checkBackend: async () => false,
      spawnProcess,
      runtime: fakeRuntime(),
    })).rejects.toThrow("无法启动后端 Python 命令：missing-python");

    expect(backend.killed).toBe(true);
  });
});
