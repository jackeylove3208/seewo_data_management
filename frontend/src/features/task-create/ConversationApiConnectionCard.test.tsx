import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { ConversationApiConnectionCard } from "./ConversationApiConnectionCard";

it("describes classification failures as retryable service errors", () => {
  render(
    <ConversationApiConnectionCard
      conversationId="00000000-0000-0000-0000-000000000001"
      connection={{
        provider_id: "dingtalk",
        state: "invalid",
        safe_error_code: "connector_entity_classification_unknown",
        required_secret_fields: ["app_key", "app_secret"],
        display_name: "钉钉临时连接-测试",
        capabilities: {},
        visibility_summary: {},
      }}
      configure={vi.fn()}
      onChange={vi.fn()}
    />,
  );

  expect(screen.getByRole("alert")).toHaveTextContent(
    "人员分类服务暂时不可用，请稍后重试连接。",
  );
  expect(screen.getByRole("alert")).not.toHaveTextContent("调整钉钉组织归属");
});

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
  await user.selectOptions(within(card).getByLabelText("同步范围"), "people");
  await user.type(within(card).getByLabelText("根部门 ID"), "1");
  await user.type(within(card).getByLabelText("AppKey"), "sensitive-app-key");
  await user.type(within(card).getByLabelText("AppSecret"), "sensitive-app-secret");
  await user.click(within(card).getByRole("button", { name: "保存并测试连接" }));

  expect(await within(card).findByRole("alert")).toHaveTextContent("连接失败");
  expect(within(card).getByLabelText("AppKey")).toHaveValue("");
  expect(within(card).getByLabelText("AppSecret")).toHaveValue("");
});

it("offers three synchronization scopes and submits no classification map", async () => {
  const configure = vi.fn().mockResolvedValue({
    provider_id: "dingtalk",
    state: "pending",
    required_secret_fields: ["app_key", "app_secret"],
    capabilities: {},
    visibility_summary: {},
  });
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
  const scope = within(card).getByLabelText("同步范围");
  expect(within(scope).getAllByRole("option").map((option) => ({
    text: option.textContent,
    value: (option as HTMLOptionElement).value,
  }))).toEqual([
    { text: "请选择", value: "" },
    { text: "部门", value: "department" },
    { text: "人员", value: "people" },
    { text: "全部", value: "all" },
  ]);
  expect(within(card).queryByLabelText("人员类型")).not.toBeInTheDocument();
  expect(within(card).queryByLabelText("班级字段")).not.toBeInTheDocument();

  await user.selectOptions(scope, "all");
  await user.type(within(card).getByLabelText("根部门 ID"), "2");
  await user.type(within(card).getByLabelText("人员编号字段"), "job_number");
  await user.type(within(card).getByLabelText("AppKey"), "ding-app");
  await user.type(within(card).getByLabelText("AppSecret"), "ding-secret");
  await user.click(within(card).getByRole("button", { name: "保存并测试连接" }));

  expect(configure).toHaveBeenCalledWith({
    conversation_id: "00000000-0000-0000-0000-000000000001",
    provider_id: "dingtalk",
    display_name: "钉钉临时连接-测试",
    required_secret_fields: ["app_key", "app_secret"],
    public_configuration: {
      sync_scope: "all",
      root_department_id: 2,
      person_classification_mode: "organization_unit_llm",
      number_field: "job_number",
    },
    secret: { app_key: "ding-app", app_secret: "ding-secret" },
  });
  const submitted = configure.mock.calls[0][0].public_configuration;
  expect(submitted).not.toHaveProperty("person_entity_kind");
  expect(submitted).not.toHaveProperty("class_name_field");
  expect(submitted).not.toHaveProperty("department_entity_kinds");
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
