import { afterEach, describe, expect, it, vi } from "vitest";

import { advanceToNextPendingRiskHeading } from "./advanceToNextRisk";

function riskCard(id: string, status: string, selectable = true) {
  const card = document.createElement("section");
  card.dataset.riskApprovalId = id;
  card.dataset.riskApprovalStatus = status;
  card.dataset.riskApprovalSelectable = String(selectable);
  const heading = document.createElement("h2");
  heading.dataset.riskApprovalHeading = "";
  card.append(heading);
  document.body.append(card);
  return heading;
}

afterEach(() => {
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

describe("advanceToNextPendingRiskHeading", () => {
  it("aligns the next card heading with the top of the conversation viewport", () => {
    const viewport = document.createElement("div");
    viewport.className = "conversation-messages";
    Object.defineProperty(viewport, "scrollTop", {
      configurable: true,
      value: 40,
      writable: true,
    });
    viewport.getBoundingClientRect = vi.fn().mockReturnValue({
      top: 100,
    });
    viewport.scrollTo = vi.fn();
    document.body.append(viewport);

    const firstCard = document.createElement("section");
    firstCard.dataset.riskApprovalId = "gate-1";
    firstCard.dataset.riskApprovalStatus = "pending";
    firstCard.dataset.riskApprovalSelectable = "true";
    viewport.append(firstCard);
    const nextHeading = riskCard("gate-2", "pending");
    viewport.append(nextHeading.parentElement!);
    nextHeading.getBoundingClientRect = vi.fn().mockReturnValue({
      top: 260,
    });
    nextHeading.focus = vi.fn();
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({ matches: false }),
    });

    advanceToNextPendingRiskHeading("gate-1");

    expect(viewport.scrollTo).toHaveBeenCalledWith({
      behavior: "smooth",
      top: 200,
    });
    expect(nextHeading.focus).toHaveBeenCalledWith({ preventScroll: true });
  });

  it("smoothly scrolls and focuses the next pending risk heading", () => {
    riskCard("gate-1", "pending");
    riskCard("gate-2", "approved");
    const nextHeading = riskCard("gate-3", "pending");
    const scrollIntoView = vi.fn();
    const focus = vi.fn();
    nextHeading.scrollIntoView = scrollIntoView;
    nextHeading.focus = focus;
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({ matches: false }),
    });

    advanceToNextPendingRiskHeading("gate-1");

    expect(scrollIntoView).toHaveBeenCalledWith({
      behavior: "smooth",
      block: "start",
    });
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
  });

  it("uses instant scrolling when reduced motion is requested", () => {
    riskCard("gate-1", "pending");
    const nextHeading = riskCard("gate-2", "pending");
    const scrollIntoView = vi.fn();
    nextHeading.scrollIntoView = scrollIntoView;
    nextHeading.focus = vi.fn();
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({ matches: true }),
    });

    advanceToNextPendingRiskHeading("gate-1");

    expect(scrollIntoView).toHaveBeenCalledWith({
      behavior: "auto",
      block: "start",
    });
  });

  it("skips a pending risk that cannot be selected", () => {
    riskCard("gate-1", "pending");
    const unavailableHeading = riskCard("gate-2", "pending", false);
    const nextHeading = riskCard("gate-3", "pending");
    const unavailableScroll = vi.fn();
    const nextScroll = vi.fn();
    unavailableHeading.scrollIntoView = unavailableScroll;
    unavailableHeading.focus = vi.fn();
    nextHeading.scrollIntoView = nextScroll;
    nextHeading.focus = vi.fn();
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({ matches: false }),
    });

    advanceToNextPendingRiskHeading("gate-1");

    expect(unavailableScroll).not.toHaveBeenCalled();
    expect(nextScroll).toHaveBeenCalledWith({
      behavior: "smooth",
      block: "start",
    });
  });

  it("does nothing when there is no later pending risk", () => {
    const currentHeading = riskCard("gate-1", "pending");
    const scrollIntoView = vi.fn();
    currentHeading.scrollIntoView = scrollIntoView;

    advanceToNextPendingRiskHeading("gate-1");

    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});
