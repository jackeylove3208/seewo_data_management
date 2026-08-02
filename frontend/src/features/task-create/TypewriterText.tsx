import { useEffect, useRef, useState } from "react";

import { prefersReducedMotion } from "./motionPreferences";

const CHARACTER_DELAY_MS = 24;
const MAX_STEPS = 100;

export function TypewriterText({
  text,
  onComplete,
}: {
  text: string;
  onComplete: () => void;
}) {
  const reducedMotion = prefersReducedMotion();
  const [visibleText, setVisibleText] = useState(reducedMotion ? text : "");
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;

  useEffect(() => {
    const characters = Array.from(text);
    if (reducedMotion || characters.length === 0) {
      setVisibleText(text);
      onCompleteRef.current();
      return;
    }

    let cancelled = false;
    let visibleCount = 0;
    let timer: number | undefined;
    const stepSize = Math.max(1, Math.ceil(characters.length / MAX_STEPS));

    const revealNext = () => {
      if (cancelled) return;
      visibleCount = Math.min(characters.length, visibleCount + stepSize);
      setVisibleText(characters.slice(0, visibleCount).join(""));
      if (visibleCount === characters.length) {
        onCompleteRef.current();
        return;
      }
      timer = window.setTimeout(revealNext, CHARACTER_DELAY_MS);
    };

    setVisibleText("");
    timer = window.setTimeout(revealNext, CHARACTER_DELAY_MS);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [reducedMotion, text]);

  return <p aria-hidden="true">{visibleText}</p>;
}
