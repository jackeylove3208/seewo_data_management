import { describe, expect, it } from "vitest";

import globalCss from "./global.css?inline";
import appleCss from "./apple.css?inline";

describe("responsive analysis styles", () => {
  it("defines the flat Codex light workspace visual system", () => {
    expect(appleCss).toMatch(/--codex-canvas:\s*#ffffff/);
    expect(appleCss).toMatch(/--codex-sidebar:\s*#f6f7f8/);
    expect(appleCss).toMatch(/--codex-ink:\s*#202123/);
    expect(appleCss).toMatch(/\.apple-sidebar/);
    expect(appleCss).toMatch(/\.conversation-reset-button/);
    expect(appleCss).toMatch(/\.task-status-rail/);
    expect(appleCss).toMatch(/\.agent-report-section/);
    expect(appleCss).toMatch(/\.apple-page\s+\.graph-live-progress\s*,\s*\.apple-page\s+\.graph-approval-card/);
    expect(appleCss).toMatch(/\.apple-agent-modal\s+\.ant-modal-content/);
    expect(appleCss).not.toMatch(/radial-gradient/);
    expect(appleCss).not.toMatch(/apple-drift/);
  });

  it("keeps task controls and approval workbench in a complete two-column layout", () => {
    expect(globalCss).toMatch(
      /\.graph-live-progress,\s*\.graph-approval-card\s*\{[^}]*margin:\s*0 0 28px/s,
    );
    expect(globalCss).toMatch(
      /\.graph-approval-card\s*\{[^}]*display:\s*grid[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s,
    );
    expect(appleCss).toMatch(
      /\.agent-task-detail-page:has\(>\s*\.task-status-rail\.is-collapsed\)\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+50px/s,
    );
    expect(appleCss).toMatch(
      /\.apple-page\s+\.graph-medium-review-panel\s*\{[^}]*display:\s*grid/s,
    );
    expect(appleCss).toMatch(
      /\.apple-page\s+\.graph-medium-review-group\s*\{[^}]*border:\s*1px solid/s,
    );
    expect(appleCss).toMatch(
      /\.apple-page\s+\.graph-medium-review-actions\s*\{[^}]*justify-content:\s*flex-end/s,
    );
  });
  it("allows mobile progress details to wrap", () => {
    expect(globalCss).toMatch(/\.stage-analysis-progress\s*>\s*small\s*\{[^}]*white-space:\s*normal/s);
  });

  it("keeps the batch modal body within the viewport", () => {
    expect(globalCss).toMatch(/\.batch-analysis-modal\s+\.ant-modal-body\s*\{[^}]*max-height:[^;}]+;[^}]*overflow-y:\s*auto/s);
  });

  it("lets the agent conversation fill the available viewport", () => {
    expect(globalCss).toMatch(/\.conversation-surface\s*\{[^}]*display:\s*grid/s);
    expect(globalCss).toMatch(/\.conversation-messages\s*\{[^}]*max-height:\s*none/s);
    expect(globalCss).not.toMatch(/\.conversation-messages\s*\{[^}]*max-height:\s*410px/s);
  });
});
