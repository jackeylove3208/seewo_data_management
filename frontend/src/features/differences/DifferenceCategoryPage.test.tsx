import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { DifferenceCategoryPage } from "./DifferenceCategoryPage";

describe("difference category detail", () => {
  beforeEach(() => localStorage.clear());

  it("selects one issue independently when a person has multiple problems", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/tasks/demo-001/differences/teacher"]}>
        <Routes>
          <Route path="/tasks/:taskId/differences/:entityType" element={<DifferenceCategoryPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: /张三/ }));
    await user.click(screen.getByRole("checkbox", { name: "选择张三的所属部门" }));

    expect(screen.getByText("已选择 1 人，共 1 个问题")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "选择张三的手机号" })).not.toBeChecked();
  });
});
