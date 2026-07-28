export function advanceToNextPendingRiskHeading(currentGateId: string) {
  const cards = Array.from(
    document.querySelectorAll<HTMLElement>("[data-risk-approval-id]"),
  );
  const currentIndex = cards.findIndex(
    (card) => card.dataset.riskApprovalId === currentGateId,
  );
  if (currentIndex < 0) {
    return;
  }

  const nextCard = cards
    .slice(currentIndex + 1)
    .find((card) => card.dataset.riskApprovalStatus === "pending");
  const heading = nextCard?.querySelector<HTMLElement>(
    "[data-risk-approval-heading]",
  );
  if (!heading) {
    return;
  }

  const reduceMotion =
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  heading.scrollIntoView({
    behavior: reduceMotion ? "auto" : "smooth",
    block: "start",
  });
  heading.focus({ preventScroll: true });
}
