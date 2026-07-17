import type { DifferencePerson } from "../../types/domain";

export interface SelectionState {
  checked: boolean;
  indeterminate: boolean;
}

export function issueIdsFor(people: DifferencePerson[]) {
  return people.flatMap((person) => person.issues.filter((issue) => issue.selectable).map((issue) => issue.id));
}

function toggleIds(selection: Set<string>, issueIds: string[], checked: boolean) {
  const next = new Set(selection);
  issueIds.forEach((issueId) => {
    if (checked) next.add(issueId);
    else next.delete(issueId);
  });
  return next;
}

export function toggleCategory(selection: Set<string>, people: DifferencePerson[], checked: boolean) {
  return toggleIds(selection, issueIdsFor(people), checked);
}

export function togglePerson(selection: Set<string>, person: DifferencePerson, checked: boolean) {
  return toggleIds(selection, issueIdsFor([person]), checked);
}

export function toggleIssue(selection: Set<string>, issueId: string, checked: boolean) {
  return toggleIds(selection, [issueId], checked);
}

export function getSelectionState(selection: Set<string>, issueIds: string[]): SelectionState {
  const selectedCount = issueIds.filter((issueId) => selection.has(issueId)).length;
  return {
    checked: issueIds.length > 0 && selectedCount === issueIds.length,
    indeterminate: selectedCount > 0 && selectedCount < issueIds.length,
  };
}

export function selectedPeopleCount(selection: Set<string>, people: DifferencePerson[]) {
  return people.filter((person) => issueIdsFor([person]).some((issueId) => selection.has(issueId))).length;
}
