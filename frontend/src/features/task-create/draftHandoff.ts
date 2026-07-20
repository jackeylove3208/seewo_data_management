import type { TaskIntentDraft } from "./types";
import { isTaskIntentDraft, isTaskIntentReady } from "./types";

export const TASK_INTENT_STORAGE_KEY = "mofa-v2-task-intent";

const STORAGE_VERSION = 1;

interface StoredTaskIntent {
  version: typeof STORAGE_VERSION;
  draft: TaskIntentDraft;
}

export function saveTaskIntentDraft(draft: TaskIntentDraft) {
  if (!isTaskIntentDraft(draft) || !isTaskIntentReady(draft)) return false;
  const payload: StoredTaskIntent = {
    version: STORAGE_VERSION,
    draft: {
      title: draft.title,
      scopeLabel: draft.scopeLabel,
      snapshotMode: draft.snapshotMode,
      entityTypes: [...draft.entityTypes],
    },
  };
  sessionStorage.setItem(TASK_INTENT_STORAGE_KEY, JSON.stringify(payload));
  return true;
}

export function loadTaskIntentDraft() {
  const stored = sessionStorage.getItem(TASK_INTENT_STORAGE_KEY);
  if (!stored) return undefined;
  try {
    const payload = JSON.parse(stored) as Partial<StoredTaskIntent>;
    if (payload.version !== STORAGE_VERSION || !isTaskIntentDraft(payload.draft) || !isTaskIntentReady(payload.draft)) {
      clearTaskIntentDraft();
      return undefined;
    }
    return {
      title: payload.draft.title,
      scopeLabel: payload.draft.scopeLabel,
      snapshotMode: payload.draft.snapshotMode,
      entityTypes: [...payload.draft.entityTypes],
    };
  } catch {
    clearTaskIntentDraft();
    return undefined;
  }
}

export function clearTaskIntentDraft() {
  sessionStorage.removeItem(TASK_INTENT_STORAGE_KEY);
}
