import { demoDifferences } from "../../data/demoDifferences";
import {
  getSelectionState,
  issueIdsFor,
  selectedPeopleCount,
  toggleCategory,
  toggleIssue,
  togglePerson,
} from "./selection";

const teachers = demoDifferences.filter((person) => person.entityType === "teacher");

describe("hierarchical difference selection", () => {
  it("selects every eligible issue in a category", () => {
    const selected = toggleCategory(new Set(), teachers, true);

    expect([...selected].sort()).toEqual(issueIdsFor(teachers).sort());
    expect(getSelectionState(selected, issueIdsFor(teachers))).toEqual({ checked: true, indeterminate: false });
  });

  it("represents a person as partially selected", () => {
    const zhang = teachers[0];
    const selected = toggleIssue(new Set(), zhang.issues[0].id, true);

    expect(getSelectionState(selected, issueIdsFor([zhang]))).toEqual({ checked: false, indeterminate: true });
    expect(selectedPeopleCount(selected, teachers)).toBe(1);
  });

  it("can deselect one issue without clearing its sibling", () => {
    const zhang = teachers[0];
    const allZhang = togglePerson(new Set(), zhang, true);
    const oneLeft = toggleIssue(allZhang, zhang.issues[0].id, false);

    expect(oneLeft.has(zhang.issues[0].id)).toBe(false);
    expect(oneLeft.has(zhang.issues[1].id)).toBe(true);
  });
});
