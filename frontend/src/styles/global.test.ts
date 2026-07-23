import { describe, expect, it } from "vitest";

import globalCss from "./global.css?inline";

describe("responsive analysis styles", () => {
  it("defines the Apple workspace visual system", () => {
    expect(globalCss).toMatch(/--apple-canvas:/);
    expect(globalCss).toMatch(/\.apple-workspace::before/);
    expect(globalCss).toMatch(/\.apple-sidebar/);
    expect(globalCss).toMatch(/prefers-reduced-motion/);
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
