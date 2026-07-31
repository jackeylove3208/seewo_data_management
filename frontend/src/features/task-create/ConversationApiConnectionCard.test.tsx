import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { ConversationApiConnectionCard } from "./ConversationApiConnectionCard";

it("clears credential inputs after a failed submission", async () => {
  const configure = vi.fn().mockRejectedValue(new Error("连接失败"));
  const user = userEvent.setup();
  render(
    <ConversationApiConnectionCard
      conversationId="00000000-0000-0000-0000-000000000001"
      connection={{
        provider_id: "dingtalk",
        state: "configuration_required",
        required_secret_fields: ["app_key", "app_secret"],
        display_name: "钉钉临时连接-测试",
        capabilities: {},
        visibility_summary: {},
      }}
      configure={configure}
      onChange={vi.fn()}
    />,
  );
  const card = screen.getByLabelText("API 连接配置");
  await user.selectOptions(within(card).getByLabelText("人员类型"), "teacher");
  await user.type(within(card).getByLabelText("根部门 ID"), "1");
  await user.type(within(card).getByLabelText("AppKey"), "sensitive-app-key");
  await user.type(within(card).getByLabelText("AppSecret"), "sensitive-app-secret");
  await user.click(within(card).getByRole("button", { name: "保存并测试连接" }));

  expect(await within(card).findByRole("alert")).toHaveTextContent("连接失败");
  expect(within(card).getByLabelText("AppKey")).toHaveValue("");
  expect(within(card).getByLabelText("AppSecret")).toHaveValue("");
});

it("uses a single-column form for DingTalk connection fields", () => {
  render(
    <ConversationApiConnectionCard
      conversationId="00000000-0000-0000-0000-000000000001"
      connection={{
        provider_id: "dingtalk",
        state: "configuration_required",
        required_secret_fields: ["app_key", "app_secret"],
        display_name: "钉钉临时连接-测试",
        capabilities: {},
        visibility_summary: {},
      }}
      configure={vi.fn()}
      onChange={vi.fn()}
    />,
  );

  expect(screen.getByLabelText("API 连接配置").querySelector("form"))
    .toHaveClass("api-connection-form");
});
