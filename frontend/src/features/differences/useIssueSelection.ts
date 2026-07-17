import { useState } from "react";

const storageKey = (taskId: string) => `mofa-issue-selection:${taskId}`;

function readSelection(taskId: string) {
  try {
    const value = localStorage.getItem(storageKey(taskId));
    return new Set<string>(value ? JSON.parse(value) as string[] : []);
  } catch {
    return new Set<string>();
  }
}

export function useIssueSelection(taskId: string) {
  const [selection, setSelectionState] = useState(() => readSelection(taskId));

  function setSelection(update: (current: Set<string>) => Set<string>) {
    setSelectionState((current) => {
      const next = update(current);
      localStorage.setItem(storageKey(taskId), JSON.stringify([...next]));
      return next;
    });
  }

  return { selection, setSelection };
}
