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
    .find(
      (card) =>
        card.dataset.riskApprovalStatus === "pending"
        && card.dataset.riskApprovalSelectable === "true",
    );
  const heading = nextCard?.matches("[data-risk-approval-heading]")
    ? nextCard
    : nextCard?.querySelector<HTMLElement>("[data-risk-approval-heading]");
  if (!heading) {
    return;
  }

  const reduceMotion =
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const behavior = reduceMotion ? "auto" : "smooth";
  const conversationViewport = heading.closest<HTMLElement>(
    ".conversation-messages",
  );
  if (conversationViewport) {
    const viewportTop = conversationViewport.getBoundingClientRect().top;
    const headingTop = heading.getBoundingClientRect().top;
    conversationViewport.scrollTo({
      behavior,
      top: conversationViewport.scrollTop + headingTop - viewportTop,
    });
  } else {
    heading.scrollIntoView({
      behavior,
      block: "start",
    });
  }
  heading.focus({ preventScroll: true });
}
