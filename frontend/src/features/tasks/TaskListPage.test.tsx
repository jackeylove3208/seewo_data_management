import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { TaskListPage } from "./TaskListPage";

function LocationProbe() {
  return <output>{useLocation().pathname}</output>;
}

describe("task history", () => {
  it("opens a historical task when its row body is clicked", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/tasks"]}>
        <Routes>
          <Route path="/tasks" element={<TaskListPage />} />
          <Route path="/tasks/:taskId" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(screen.getByText("三方全校数据核对"));

    expect(screen.getByText("/tasks/demo-001")).toBeInTheDocument();
  });
});
