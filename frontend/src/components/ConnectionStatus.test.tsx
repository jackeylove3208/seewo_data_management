import { render, screen } from "@testing-library/react";

import { ConnectionStatus } from "./ConnectionStatus";

describe("backend connection status", () => {
  it("shows a direct offline state when readiness fails", async () => {
    render(<ConnectionStatus check={() => Promise.reject(new Error("offline"))} />);

    expect(await screen.findByText("后端未连接")).toBeInTheDocument();
  });
});
