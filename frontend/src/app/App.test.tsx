import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";

import { App } from "./App";

describe("application shell", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("shows the persistent workspace and opens a fresh reconciliation", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    window.history.pushState({}, "", "/tasks/demo-001");
    render(<App />);

    expect(screen.getByRole("link", { name: "魔方 AI 数据治理" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "对账工作区" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /三方全校数据核对/ })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByText("后端未连接")).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "新建对账" }));
    expect(screen.getByRole("heading", { name: "和 AI 一起新建对账" })).toBeInTheDocument();
  });

  it("moves focus into and back from the mobile workspace", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    window.history.pushState({}, "", "/tasks");
    render(<App />);

    const openButton = screen.getByRole("button", { name: "打开导航" });
    await user.click(openButton);
    const closeButton = within(screen.getByRole("navigation", { name: "对账工作区" })).getByRole("button", { name: "关闭导航" });
    expect(closeButton).toHaveFocus();

    await user.click(closeButton);
    expect(openButton).toHaveFocus();
  });
});
