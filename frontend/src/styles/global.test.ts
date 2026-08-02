import { describe, expect, it } from "vitest";

import globalCss from "./global.css?inline";
import appleCss from "./apple.css?inline";

function extractCssBlocks(css: string, marker: string): string[] {
  const blocks: string[] = [];
  let searchIndex = 0;

  while (searchIndex < css.length) {
    const markerIndex = css.indexOf(marker, searchIndex);
    if (markerIndex === -1) {
      break;
    }

    const openingBraceIndex = css.indexOf("{", markerIndex);
    expect(openingBraceIndex).toBeGreaterThan(markerIndex);

    let depth = 0;
    let blockClosed = false;
    for (let index = openingBraceIndex; index < css.length; index += 1) {
      if (css[index] === "{") {
        depth += 1;
      } else if (css[index] === "}") {
        depth -= 1;
        if (depth === 0) {
          blocks.push(css.slice(openingBraceIndex + 1, index));
          searchIndex = index + 1;
          blockClosed = true;
          break;
        }
      }
    }

    if (!blockClosed) {
      throw new Error(`Unclosed CSS block: ${marker}`);
    }
  }

  expect(blocks.length).toBeGreaterThan(0);
  return blocks;
}

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
      /\.conversation-workspace\s*>\s*\.task-status-rail\.is-collapsed\s*\{[^}]*max-height:\s*59px/s,
    );
  });

  it("keeps sidebar controls fixed while every expanded source scrolls internally", () => {
    expect(globalCss).toMatch(
      /\.workspace-navigation\s*\{[^}]*min-height:\s*0[^}]*overflow:\s*hidden/s,
    );
    expect(globalCss).toMatch(
      /\.workspace-history\s*\{[^}]*min-height:\s*0[^}]*overflow:\s*hidden/s,
    );
    expect(globalCss).toMatch(
      /\.workspace-history-list\s*\{[^}]*min-height:\s*0[^}]*overflow-y:\s*auto[^}]*overscroll-behavior-y:\s*contain/s,
    );
    expect(globalCss).toMatch(
      /\.workspace-source-groups\s*\{[^}]*display:\s*flex[^}]*flex-direction:\s*column/s,
    );
    expect(globalCss).toMatch(
      /\.workspace-source-group:has\(\.workspace-source-tasks\)\s*\{[^}]*display:\s*grid[^}]*grid-template-rows:\s*auto minmax\(0,\s*1fr\)[^}]*flex:\s*1 1 0/s,
    );
    expect(globalCss).toMatch(
      /\.workspace-source-tasks\s*\{[^}]*min-height:\s*0[^}]*overflow-y:\s*auto[^}]*overscroll-behavior-y:\s*contain/s,
    );
  });

  it("keeps the conversation header compact on desktop and mobile", () => {
    const mobileCss = extractCssBlocks(globalCss, "@media (max-width: 720px)").join("\n");

    expect(globalCss).toMatch(
      /\.conversation-create-page\s*\{[^}]*padding:\s*12px 0 14px/s,
    );
    expect(globalCss).toMatch(
      /\.conversation-page-actions\s*\{[^}]*min-height:\s*32px[^}]*margin-bottom:\s*7px/s,
    );
    expect(globalCss).toMatch(
      /\.conversation-page-actions \.conversation-assistant-title\s*\{[^}]*font-size:\s*15px/s,
    );
    expect(appleCss).toMatch(
      /\.conversation-reset-button\s*\{[^}]*min-height:\s*32px[^}]*padding:\s*0 10px/s,
    );
    expect(mobileCss).toMatch(
      /\.conversation-create-page\s*\{[^}]*padding:\s*10px 0/s,
    );
    expect(mobileCss).toMatch(
      /\.conversation-page-actions\s*\{[^}]*margin-bottom:\s*6px/s,
    );
  });

  it("uses Codex message colors and uniform confirmation metadata", () => {
    expect(appleCss).toMatch(
      /\.apple-page \.conversation-message p\s*\{[^}]*color:\s*var\(--codex-ink\)/s,
    );
    expect(appleCss).toMatch(
      /\.apple-page \.conversation-message\.user\s*\{[^}]*border-color:\s*var\(--codex-border\)[^}]*background:\s*var\(--codex-panel-muted\)/s,
    );
    expect(appleCss).toMatch(
      /\.apple-page \.conversation-message\.user \.message-avatar\s*\{[^}]*color:\s*#4f5358[^}]*background:\s*#e2e4e7/s,
    );
    expect(appleCss).toMatch(
      /body,\s*button,\s*input,\s*textarea,\s*select\s*\{[^}]*font-family:\s*var\(--codex-font\)/s,
    );
    expect(appleCss).toMatch(
      /\.apple-page \.start-confirmation\s*\{[^}]*font-size:\s*13px/s,
    );
    expect(appleCss).toMatch(
      /\.apple-page \.start-confirmation \.start-confirmation-details\s*\{[^}]*display:\s*grid/s,
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

  it("styles idle task progress and subtle user-message motion", () => {
    expect(appleCss).toMatch(
      /\.task-status-rail\.is-idle\s+\.task-status-stage-list\s*\{[^}]*opacity:\s*0\.55/s,
    );
    expect(appleCss).toMatch(
      /\.task-status-idle\s*\{[^}]*text-align:\s*center/s,
    );
    expect(globalCss).toMatch(/@keyframes\s+conversation-message-enter/);
    expect(globalCss).toMatch(
      /\.conversation-message\.user\.is-entering\s*\{[^}]*animation:\s*conversation-message-enter\s+180ms\s+ease-out\s+both/s,
    );
    const reducedMotionCss = extractCssBlocks(
      globalCss,
      "@media (prefers-reduced-motion: reduce)",
    ).join("\n");
    expect(reducedMotionCss).toMatch(
      /\.conversation-message\.user\.is-entering\s*\{[^}]*animation:\s*none/s,
    );
  });
});
