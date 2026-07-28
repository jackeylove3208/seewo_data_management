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

  it("keeps the outer conversation page fixed while messages scroll internally", () => {
    expect(globalCss).toMatch(
      /\.workspace-main:has\(>\s*\.conversation-create-page\)\s*\{[^}]*height:\s*100dvh[^}]*overflow:\s*hidden/s,
    );
    expect(globalCss).toMatch(
      /\.app-shell:has\(\.workspace-main\s*>\s*\.conversation-create-page\)\s*\{[^}]*height:\s*100dvh[^}]*min-height:\s*0[^}]*overflow:\s*hidden/s,
    );
    expect(globalCss).toMatch(
      /\.conversation-create-page\s*\{[^}]*height:\s*100%[^}]*min-height:\s*0[^}]*overflow:\s*hidden/s,
    );
    expect(globalCss).toMatch(
      /\.conversation-create-page\s*\{[^}]*grid-template-rows:\s*auto auto minmax\(0,\s*1fr\)/s,
    );
    expect(globalCss).toMatch(
      /\.workspace-main:has\(>\s*\.conversation-create-page\)\s*>\s*\.conversation-create-page\s*\{[^}]*grid-row:\s*2/s,
    );
    expect(globalCss).toMatch(
      /\.conversation-create-page\s+\.conversation-workspace\s*\{[^}]*grid-row:\s*3/s,
    );
    expect(globalCss).toMatch(/\.conversation-surface\s*\{[^}]*overflow:\s*hidden/s);
    expect(globalCss).toMatch(/\.conversation-messages\s*\{[^}]*overflow-y:\s*auto/s);
    expect(appleCss).toMatch(
      /\.conversation-workspace\s*>\s*\.task-status-rail\s*\{[^}]*align-self:\s*start[^}]*height:\s*auto[^}]*max-height:\s*min\(50dvh,\s*440px\)/s,
    );
    expect(appleCss).toMatch(
      /\.conversation-workspace\s*>\s*\.task-status-rail\.is-collapsed\s*\{[^}]*max-height:\s*58px/s,
    );
  });

  it("uses one visual shell for the conversation composer", () => {
    expect(globalCss).toMatch(
      /\.conversation-composer\s*\{[^}]*border:\s*1px solid[^}]*border-radius:/s,
    );
    expect(globalCss).toMatch(
      /\.conversation-composer textarea\s*\{[^}]*resize:\s*none[^}]*border:\s*0[^}]*background:\s*transparent/s,
    );
    expect(globalCss).toMatch(
      /\.conversation-composer textarea:focus\s*\{[^}]*box-shadow:\s*none/s,
    );
  });
});
