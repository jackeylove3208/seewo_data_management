import { describe, expect, it } from "vitest";

import globalCss from "./global.css?inline";

describe("responsive analysis styles", () => {
  it("allows mobile progress details to wrap", () => {
    expect(globalCss).toMatch(/\.stage-analysis-progress\s*>\s*small\s*\{[^}]*white-space:\s*normal/s);
  });

  it("keeps the batch modal body within the viewport", () => {
    expect(globalCss).toMatch(/\.batch-analysis-modal\s+\.ant-modal-body\s*\{[^}]*max-height:[^;}]+;[^}]*overflow-y:\s*auto/s);
  });
});
