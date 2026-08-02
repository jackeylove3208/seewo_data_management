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
    environment: { RECONCILIATION_DATABASE_URL: "sqlite+aiosqlite:///./storage/test.db" },
  },
  worker: {
    command: "/tmp/python",
    args: ["-m", "app.ai.worker"],
    environment: { RECONCILIATION_DATABASE_URL: "sqlite+aiosqlite:///./storage/test.db" },
  },
  frontend: {
    command: "npm",
    args: ["run", "dev:web"],
  },
};

const migrationTestPlan = {
  ...testPlan,
  migration: {
    command: "/tmp/python",
    args: ["-m", "alembic", "upgrade", "head"],
    environment: { RECONCILIATION_DATABASE_URL: "sqlite+aiosqlite:///./storage/test.db" },
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

  it("plans a local SQLite API, worker, and Vite web process", () => {
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
    expect(plan.worker).toEqual({
      command: plan.backend.command,
      args: ["-m", "app.ai.worker"],
      environment: plan.backend.environment,
    });
    expect(plan.migration).toEqual({
      command: plan.backend.command,
      args: ["-m", "alembic", "upgrade", "head"],
      environment: plan.backend.environment,
    });
    expect(plan.frontend.command).toBe("npm");
    expect(plan.frontend.args).toEqual(["run", "dev:web"]);
  });

  it("upgrades the configured database before starting the backend", async () => {
    const migration = new FakeChildProcess();
    const backend = new FakeChildProcess();
    const worker = new FakeChildProcess();
    const frontend = new FakeChildProcess();
    const children = [migration, backend, worker, frontend];
    const spawned = [];
    const spawnProcess = (command, args, options) => {
      spawned.push({ command, args, options });
      const child = children.shift();
      queueMicrotask(() => {
        child.emit("spawn");
        if (child === migration) {
          child.exitCode = 0;
          child.emit("exit", 0, null);
        }
      });
      return child;
    };

    const development = await developmentModule.startDevelopment({
      developmentPlan: migrationTestPlan,
      checkBackend: async () => false,
      waitUntilBackendReady: async () => {},
      verifyPath: async () => {},
      spawnProcess,
      runtime: fakeRuntime(),
    });
    development.stop();

    expect(spawned.map(({ args }) => args)).toEqual([
      ["-m", "alembic", "upgrade", "head"],
      ["-m", "uvicorn"],
      ["-m", "app.ai.worker"],
      ["run", "dev:web"],
    ]);
    expect(spawned[0].options.env.RECONCILIATION_DATABASE_URL).toBe(
      spawned[1].options.env.RECONCILIATION_DATABASE_URL,
    );
    expect(spawned[2].options.env.RECONCILIATION_DATABASE_URL).toBe(
      spawned[1].options.env.RECONCILIATION_DATABASE_URL,
    );
  });

  it("rejects an already-ready backend before migration or frontend startup", async () => {
    const spawned = [];
    const spawnProcess = (command, args, options) => {
      spawned.push({ command, args, options });
      throw new Error("unexpected process spawn");
    };

    await expect(developmentModule.startDevelopment({
      developmentPlan: migrationTestPlan,
      checkBackend: async () => true,
      verifyPath: async () => {},
      spawnProcess,
      runtime: fakeRuntime(),
    })).rejects.toThrow("请先停止旧后端");

    expect(spawned).toEqual([]);
  });

  it("does not start the backend or frontend when migration fails", async () => {
    const migration = new FakeChildProcess();
    const spawned = [];
    const spawnProcess = (command, args) => {
      spawned.push({ command, args });
      queueMicrotask(() => {
        migration.emit("spawn");
        migration.exitCode = 1;
        migration.emit("exit", 1, null);
      });
      return migration;
    };

    await expect(developmentModule.startDevelopment({
      developmentPlan: migrationTestPlan,
      checkBackend: async () => false,
      verifyPath: async () => {},
      spawnProcess,
      runtime: fakeRuntime(),
    })).rejects.toThrow("数据库迁移");

    expect(spawned.map(({ args }) => args)).toEqual([
      ["-m", "alembic", "upgrade", "head"],
    ]);
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
    const worker = new FakeChildProcess();
    const frontend = new FakeChildProcess();
    const children = [backend, worker, frontend];
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
    expect(worker.killed).toBe(true);
    expect(runtime.exitCode).toBe(1);
  });

  it("allows a backend Python command to be resolved from PATH", async () => {
    const backend = new FakeChildProcess();
    const worker = new FakeChildProcess();
    const frontend = new FakeChildProcess();
    const children = [backend, worker, frontend];
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
        worker: { ...testPlan.worker, command: "python3.12" },
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
