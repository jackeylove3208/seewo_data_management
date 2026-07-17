import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

import { describe, expect, it } from "vitest";

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
});
