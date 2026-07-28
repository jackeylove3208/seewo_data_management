import { describe, expect, it } from "vitest";

import differencePage from "../features/differences/DifferenceCategoryPage.tsx?raw";
import executionDetailPage from "../features/executions/ExecutionDetailPage.tsx?raw";
import executionHistoryPage from "../features/executions/ExecutionHistoryPage.tsx?raw";
import taskDetailPage from "../features/task-detail/TaskDetailPage.tsx?raw";
import appleCss from "./apple.css?raw";
import globalCss from "./global.css?raw";

const routedPages = [
  ["差异分类", differencePage],
  ["执行详情", executionDetailPage],
  ["执行历史", executionHistoryPage],
  ["旧任务详情", taskDetailPage],
] as const;

describe("Codex page theme coverage", () => {
  it.each(routedPages)("applies the light workbench class to every %s page state", (_name, source) => {
    expect(source.match(/<main\b/g)?.length).toBe(
      source.match(/<main\b[^>]*className="[^"]*\bapple-page\b/g)?.length,
    );
  });

  it("uses the approved wide chat canvas and right-aligned user messages", () => {
    expect(globalCss).toContain("width: min(calc(100% - 32px), 1440px)");
    expect(globalCss).toContain(".conversation-message.user");
    expect(globalCss).toContain("justify-self: end");
    expect(appleCss).toContain("grid-template-columns: minmax(0, 1fr) 280px");
  });
});
