import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { agentApi } from "../../api/agent";
import { AgentReportPage } from "./AgentReportPage";

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const rendered = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/tasks/task-report-1/report"]}>
        <Routes>
          <Route path="/tasks/:taskId/report" element={<AgentReportPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...rendered, client };
}

describe("Agent synchronization report", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the model narrative and local writeback result in the light document workbench", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-1",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: true,
      deletion_eligible: false,
      created_at: "2026-07-26T07:00:00Z",
      content: {
        narrative: {
          title_zh: "全校组织数据同步分析报告",
          summary_zh:
            "Agent 已完成数据核验与治理。发现两项需要处理的问题，获批操作均已写入希沃本地数据。",
        },
      },
      facts: {
        findings: [
          {
            id: "finding-1",
            category_zh: "手机号不一致",
            entity_name: "李明",
            analysis_zh: "第三方权威手机号与希沃记录不一致。",
            solution_zh: "已按审核结果更新希沃手机号。",
            operator_decision: "approved",
            execution_status: "succeeded",
          },
        ],
        excluded_findings: [],
        mutations: [
          {
            id: "operation-1",
            operation: "update",
            entity_kind: "student",
            status: "succeeded",
          },
        ],
        mutation_summary: { succeeded: 1, failed: 0 },
        publication: {
          status: "published",
          source_ref: "seewo/current.csv",
        },
      },
    });

    const { client, container } = renderPage();

    expect(
      await screen.findByRole("heading", {
        name: "全校组织数据同步分析报告",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/发现两项需要处理的问题，获批操作均已写入希沃本地数据/),
    ).toBeInTheDocument();
    expect(screen.getByText("第三方权威手机号与希沃记录不一致。")).toBeInTheDocument();
    expect(screen.getByText("已按审核结果更新希沃手机号。")).toBeInTheDocument();
    expect(screen.getByText("已写回本地 CSV")).toBeInTheDocument();
    expect(screen.getByText("seewo/current.csv")).toBeInTheDocument();
    expect(container.querySelector(".agent-report-page.apple-page")).not.toBeNull();
    const finding = container.querySelector(".agent-report-findings > li");
    expect(finding).not.toBeNull();
    expect(within(finding as HTMLElement).getByText("已同意")).toBeInTheDocument();
    expect(within(finding as HTMLElement).getByText("执行成功")).toBeInTheDocument();
    client.clear();
  });

  it("shows approved failures, input anomalies, and partial completion prominently", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-partial",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-07-29T08:00:00Z",
      content: {
        narrative: {
          title_zh: "部分执行同步报告",
          summary_zh: "部分获批变更未能完成。",
          input_exception_analyses: [{
            reason_code: "authority_identity_absent",
            title_zh: "权威部门数据缺少身份标识",
            analysis_zh: "钉钉权威数据中有 4 条部门记录缺少身份标识。",
            impact_zh: "这些部门记录无法可靠匹配，已从治理范围排除。",
            suggestion_zh: "请补充可用于匹配的部门编号后重新运行任务。",
          }],
        },
      },
      facts: {
        findings: [
          {
            id: "finding-failed",
            kind: "target_extra",
            category_zh: "多余教师",
            entity_name: "测试教师",
            operator_decision: "approved",
            execution_status: "failed",
          },
          {
            id: "authority-invalid-1",
            kind: "authority_invalid",
            category_zh: "权威记录缺少身份标识",
            analysis_zh: "部门记录缺少编号、电话和邮箱。",
            execution_status: "not_executed",
          },
          {
            id: "authority-invalid-2",
            kind: "authority_invalid",
            category_zh: "权威记录缺少身份标识",
            analysis_zh: "部门记录缺少编号、电话和邮箱。",
            execution_status: "not_executed",
          },
        ],
        excluded_findings: [
          {
            reason: "authority_field_unavailable",
            disposition: "source_field_unavailable",
          },
          {
            reason: "authority_identity_absent",
            disposition: "mandatory_ai_anomaly",
          },
          {
            reason: "目标记录缺少身份字段",
            disposition: "target_extra",
          },
        ],
        input_diagnostics: {
          marked_input_counts: { authoritative: 4, target: 0 },
          unique_marked_input_count: 4,
          reason_counts: { authority_identity_absent: 4 },
          overlapped_reason_counts: { authority_field_unavailable: 4 },
          unavailable_field_counts: {},
          identity_absent_count: 4,
        },
        mutations: [{
          id: "operation-failed",
          operation: "delete",
          entity_kind: "teacher",
          status: "failed",
        }],
        mutation_summary: {
          succeeded: 0,
          failed: 1,
          verification_failed: 0,
        },
        publication: {
          status: "no_changes",
          source_ref: "seewo/current.csv",
        },
      },
    });

    const { client, container } = renderPage();

    expect(await screen.findByText("部分完成")).toBeInTheDocument();
    const finding = container.querySelector(".agent-report-findings > li");
    expect(finding).not.toBeNull();
    expect(container.querySelectorAll(".agent-report-findings > li")).toHaveLength(1);
    expect(screen.queryByText("权威记录缺少身份标识")).not.toBeInTheDocument();
    expect(within(finding as HTMLElement).getByText("已同意")).toBeInTheDocument();
    expect(within(finding as HTMLElement).getByText("执行失败")).toBeInTheDocument();
    const exclusion = container.querySelector(".agent-report-exclusions > li");
    expect(exclusion).not.toBeNull();
    expect(
      within(exclusion as HTMLElement).getByText("输入异常"),
    ).toBeInTheDocument();
    expect(screen.getByText("权威部门数据缺少身份标识")).toBeInTheDocument();
    expect(
      screen.getByText("钉钉权威数据中有 4 条部门记录缺少身份标识。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("这些部门记录无法可靠匹配，已从治理范围排除。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("请补充可用于匹配的部门编号后重新运行任务。"),
    ).toBeInTheDocument();
    const exceptionMetric = [...container.querySelectorAll(".agent-report-metrics article")]
      .find((item) => within(item as HTMLElement).queryByText("输入异常"));
    expect(within(exceptionMetric as HTMLElement).getByText("4")).toBeInTheDocument();
    expect(screen.queryByText("authority_field_unavailable")).not.toBeInTheDocument();
    expect(container.querySelectorAll(".agent-report-exclusions > li")).toHaveLength(1);
    expect(
      container.querySelector(".agent-report-exception-analyses > li"),
    ).toHaveClass("agent-report-exception-analysis");
    expect(container.querySelector(".agent-report-metric-error")).not.toBeNull();
    client.clear();
  });

  it("renders a duplicated unexecuted finding only once", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-unexecuted",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-07-31T08:00:00Z",
      content: { narrative: {} },
      facts: {
        findings: [
          {
            id: "finding-unexecuted",
            category_zh: "待处理学生信息",
            analysis_zh: "缺少可执行的审批结论。",
            solution_zh: "等待人工审核后再执行。",
            execution_status: "not_executed",
          },
          {
            id: "finding-unexecuted",
            category_zh: "待处理学生信息",
            analysis_zh: "缺少可执行的审批结论。",
            solution_zh: "等待人工审核后再执行。",
            execution_status: "not_executed",
          },
        ],
        excluded_findings: [],
        mutations: [],
        mutation_summary: { succeeded: 0, failed: 0 },
      },
    });

    const { client, container } = renderPage();

    await screen.findByText("待处理学生信息");
    expect(container.querySelectorAll(".agent-report-findings > li")).toHaveLength(1);
    expect(screen.getAllByText("缺少可执行的审批结论。")).toHaveLength(1);
    expect(screen.getByText("未执行")).toBeInTheDocument();
    client.clear();
  });

  it("explains an early termination instead of claiming that analysis found no issues", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-terminated",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "terminated",
      rollback_eligible: false,
      deletion_eligible: true,
      created_at: "2026-08-02T02:00:00Z",
      content: {
        narrative: {
          title_zh: "任务终止报告",
          summary_zh: "任务已按操作人要求终止；终止前尚未形成治理问题，也没有修改目标数据。",
        },
      },
      facts: {
        findings: [],
        excluded_findings: [],
        mutations: [],
        mutation_summary: { succeeded: 0, failed: 0 },
        termination_context: {
          reason_code: "operator_requested",
          reason_zh: "操作人主动终止任务",
          current_node: "termination_report",
          phase_zh: "报告生成",
          recorded_finding_count: 0,
          succeeded_mutation_count: 0,
          verified_mutation_count: 0,
          data_modified: false,
        },
      },
    });

    const { client } = renderPage();

    expect(await screen.findByRole("heading", { name: "任务终止说明" }))
      .toBeInTheDocument();
    expect(screen.getByText("操作人主动终止任务")).toBeInTheDocument();
    expect(screen.getByText("报告生成")).toBeInTheDocument();
    expect(screen.getByText("任务在完成问题分析前已终止")).toBeInTheDocument();
    expect(screen.queryByText("没有需要治理的问题")).not.toBeInTheDocument();
    client.clear();
  });

  it("presents abnormal input as the reason governance analysis did not run", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-abnormal",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "abnormal_input",
      rollback_eligible: false,
      deletion_eligible: true,
      created_at: "2026-08-02T02:00:00Z",
      content: {
        narrative: {
          title_zh: "输入异常报告",
          summary_zh: "输入数据未满足安全治理要求。",
          input_exception_analyses: [{
            reason_code: "authority_identity_absent",
            title_zh: "权威数据缺少可用身份标识",
            analysis_zh: "权威数据中有 1 条记录缺少可用身份标识。",
            impact_zh: "该记录无法可靠匹配。",
            suggestion_zh: "请补充身份标识后重试。",
          }],
        },
      },
      facts: {
        findings: [],
        excluded_findings: [],
        input_diagnostics: { unique_marked_input_count: 1 },
        mutations: [],
        mutation_summary: { succeeded: 0, failed: 0 },
      },
    });

    const { client } = renderPage();

    expect(await screen.findByText("权威数据缺少可用身份标识"))
      .toBeInTheDocument();
    expect(screen.getByText("输入异常阻止了治理分析")).toBeInTheDocument();
    expect(screen.queryByText("没有需要治理的问题")).not.toBeInTheDocument();
    client.clear();
  });

  it("shows a no-write rollback as already restored instead of skipped", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-rollback-restored",
      task_id: "task-report-1",
      kind: "rollback",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: true,
      created_at: "2026-07-29T02:00:00Z",
      content: { narrative: {} },
      facts: {
        findings: [],
        mutations: [{
          id: "rollback-operation-1",
          operation: "update",
          entity_kind: "student",
          status: "already_restored",
        }],
        mutation_summary: {
          succeeded: 0,
          already_restored: 1,
          conflict_skipped: 0,
          failed: 0,
        },
        publication: {
          status: "no_changes",
          source_ref: "seewo/current.csv",
        },
      },
    });

    const { client } = renderPage();

    expect(await screen.findByText("回滚完成")).toBeInTheDocument();
    expect(screen.getByText("已处于原状态")).toBeInTheDocument();
    expect(screen.getByText("无需再次写入本地 CSV")).toBeInTheDocument();
    expect(screen.getByText("1", { selector: "strong" })).toBeInTheDocument();
    client.clear();
  });

  it("surfaces rollback conflicts instead of reporting successful completion", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-rollback-conflict",
      task_id: "task-report-1",
      kind: "rollback",
      terminal_state: "completed_with_conflicts",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-07-29T02:00:00Z",
      content: { narrative: {} },
      facts: {
        findings: [],
        mutations: [{
          id: "rollback-operation-1",
          operation: "update",
          entity_kind: "student",
          status: "conflict_skipped",
        }],
        mutation_summary: {
          succeeded: 0,
          already_restored: 0,
          conflict_skipped: 1,
          failed: 0,
        },
        publication: {
          status: "no_changes",
          source_ref: "seewo/current.csv",
        },
      },
    });

    const { client } = renderPage();

    expect(await screen.findByText("回滚存在冲突")).toBeInTheDocument();
    expect(screen.getByText("因当前值冲突而跳过")).toBeInTheDocument();
    expect(screen.queryByText("回滚完成")).not.toBeInTheDocument();
    client.clear();
  });

  it("corrects a stale included quality warning from frozen facts", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-historical-included-warning",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-08-02T02:00:00Z",
      content: {
        narrative: {
          title_zh: "模型错误标题：缺字段学生已排除",
          summary_zh: "请补充班级信息后重新运行，现有任务未完成。",
          input_exception_analyses: [{
            reason_code: "authority_field_unavailable",
            title_zh: "模型错误地声称学生被排除",
            analysis_zh: "3 名学生无法参与匹配。",
            impact_zh: "这些学生已从治理范围排除。",
            suggestion_zh: "请重新同步。",
          }],
        },
      },
      facts: {
        findings: [],
        excluded_findings: [
          {
            reason: "authority_field_unavailable",
            affected_fields: ["class_name"],
            inclusion_state: "included",
            safe_evidence: { entity_kind: "student" },
          },
          {
            reason: "authority_field_unavailable",
            affected_fields: ["class_name"],
            inclusion_state: "included",
            safe_evidence: { entity_kind: "student" },
          },
          {
            reason: "authority_field_unavailable",
            affected_fields: ["class_name"],
            inclusion_state: "included",
            safe_evidence: { entity_kind: "student" },
          },
        ],
        input_diagnostics: {
          reason_counts: { authority_field_unavailable: 3 },
          unavailable_field_counts: { class_name: 3 },
        },
        mutations: [],
        mutation_summary: { succeeded: 0, failed: 0 },
      },
    });

    const { client } = renderPage();

    expect(await screen.findByRole("heading", { name: "数据质量提醒与排除项" }))
      .toBeInTheDocument();
    expect(screen.getByText("权威学生数据缺少班级信息")).toBeInTheDocument();
    expect(screen.getByText("权威学生数据中有 3 条记录缺少班级信息。"))
      .toBeInTheDocument();
    expect(screen.getByText("允许同步")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "数据同步任务报告" })).toBeInTheDocument();
    expect(screen.getByText(
      "来源字段缺失已记录为质量提醒；允许同步的记录仍参与匹配与同步，具体执行结果见下方服务端事实。",
    )).toBeInTheDocument();
    expect(screen.queryByText("模型错误标题：缺字段学生已排除")).not.toBeInTheDocument();
    expect(screen.queryByText("请补充班级信息后重新运行，现有任务未完成。"))
      .not.toBeInTheDocument();
    expect(screen.queryByText("模型错误地声称学生被排除")).not.toBeInTheDocument();
    expect(screen.queryByText("这些学生已从治理范围排除。")).not.toBeInTheDocument();
    expect(screen.queryByText("请重新同步。")).not.toBeInTheDocument();
    client.clear();
  });

  it("does not create an included warning from a pure overlapped reason", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-pure-overlapped-warning",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-08-02T02:00:00Z",
      content: { narrative: {} },
      facts: {
        findings: [],
        excluded_findings: [{
          reason: "authority_field_unavailable",
          affected_fields: ["email"],
          inclusion_state: "included",
          safe_evidence: { entity_kind: "student" },
        }],
        input_diagnostics: {
          reason_counts: { authority_identity_absent: 1 },
          overlapped_reason_counts: { authority_field_unavailable: 1 },
          unavailable_field_counts: {},
        },
        mutations: [],
        mutation_summary: { succeeded: 0, failed: 0 },
      },
    });

    const { client } = renderPage();

    expect(await screen.findByRole("heading", { name: "数据同步分析报告" }))
      .toBeInTheDocument();
    expect(screen.queryByText("允许同步")).not.toBeInTheDocument();
    expect(screen.queryByText("权威学生数据缺少邮箱")).not.toBeInTheDocument();
    client.clear();
  });

  it("uses exclusive diagnostics for included warning count fields and entities", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-exclusive-included-warning",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-08-02T02:00:00Z",
      content: { narrative: {} },
      facts: {
        findings: [],
        excluded_findings: [
          ...Array.from({ length: 3 }, () => ({
            reason: "authority_field_unavailable",
            affected_fields: ["class_name"],
            inclusion_state: "included",
            safe_evidence: { entity_kind: "student" },
          })),
          ...["email", "number", "phone", "email"].map((field) => ({
            reason: "authority_field_unavailable",
            affected_fields: [field],
            inclusion_state: "included",
            safe_evidence: { entity_kind: "teacher" },
          })),
        ],
        input_diagnostics: {
          reason_counts: { authority_field_unavailable: 3 },
          overlapped_reason_counts: { authority_field_unavailable: 4 },
          unavailable_field_counts: { class_name: 3 },
        },
        mutations: [],
        mutation_summary: { succeeded: 0, failed: 0 },
      },
    });

    const { client } = renderPage();

    const title = await screen.findByText("权威学生数据缺少班级信息");
    const warning = title.closest("li");
    expect(warning).toHaveTextContent("权威学生数据中有 3 条记录缺少班级信息。");
    expect(warning).not.toHaveTextContent("邮箱");
    expect(warning).not.toHaveTextContent("编号");
    expect(warning).not.toHaveTextContent("手机号");
    expect(warning).not.toHaveTextContent("教师");
    client.clear();
  });

  it("uses a neutral entity and conservative impact for same-field overlap", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-same-field-overlap",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-08-02T02:00:00Z",
      content: { narrative: {} },
      facts: {
        findings: [],
        excluded_findings: ["student", "teacher"].map((entityKind) => ({
          reason: "authority_field_unavailable",
          affected_fields: ["email"],
          inclusion_state: "included",
          safe_evidence: { entity_kind: entityKind },
        })),
        input_diagnostics: {
          reason_counts: { authority_field_unavailable: 1 },
          overlapped_reason_counts: { authority_field_unavailable: 1 },
          unavailable_field_counts: { email: 1 },
        },
        mutations: [],
        mutation_summary: { succeeded: 0, failed: 0 },
      },
    });

    const { client } = renderPage();

    const warning = (await screen.findByText("允许同步")).closest("li");
    expect(warning).toHaveTextContent("权威记录数据中有 1 条记录缺少邮箱。");
    expect(warning).toHaveTextContent(
      "影响：邮箱不可用仅作为数据质量提醒；允许同步的记录仍保留在匹配与同步范围内；其他记录按其更高优先级异常状态处理。",
    );
    expect(warning).not.toHaveTextContent("教师");
    expect(warning).not.toHaveTextContent("teacher");
    expect(warning).not.toHaveTextContent("学生");
    client.clear();
  });

  it("localizes unambiguous department and teacher entities", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-localized-entities",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-08-02T02:00:00Z",
      content: { narrative: {} },
      facts: {
        findings: [],
        excluded_findings: ["department", "teacher"].map((entityKind) => ({
          reason: "authority_field_unavailable",
          affected_fields: ["email"],
          inclusion_state: "included",
          safe_evidence: { entity_kind: entityKind },
        })),
        input_diagnostics: {
          reason_counts: { authority_field_unavailable: 2 },
          unavailable_field_counts: { email: 2 },
        },
        mutations: [],
        mutation_summary: { succeeded: 0, failed: 0 },
      },
    });

    const { client } = renderPage();

    const warning = (await screen.findByText("允许同步")).closest("li");
    expect(warning).toHaveTextContent("教师");
    expect(warning).toHaveTextContent("部门");
    expect(warning).not.toHaveTextContent("teacher");
    expect(warning).not.toHaveTextContent("department");
    client.clear();
  });

  it("localizes every supported unavailable authority field", async () => {
    const fields = ["category", "name", "number", "class_name", "phone", "email"];
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-all-localized-fields",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-08-02T02:00:00Z",
      content: { narrative: {} },
      facts: {
        findings: [],
        excluded_findings: [{
          reason: "authority_field_unavailable",
          affected_fields: fields,
          inclusion_state: "included",
          safe_evidence: { entity_kind: "student" },
        }],
        input_diagnostics: {
          reason_counts: { authority_field_unavailable: 1 },
          unavailable_field_counts: Object.fromEntries(fields.map((field) => [field, 1])),
        },
        mutations: [],
        mutation_summary: { succeeded: 0, failed: 0 },
      },
    });

    const { client } = renderPage();

    const warning = (await screen.findByText("允许同步")).closest("li");
    for (const label of ["类别", "名称", "编号", "班级信息", "手机号", "邮箱"]) {
      expect(warning).toHaveTextContent(label);
    }
    for (const rawField of fields) {
      expect(warning).not.toHaveTextContent(rawField);
    }
    client.clear();
  });

  it("counts included quality warnings from marks when diagnostics are absent", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-historical-included-warning-without-diagnostics",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-08-02T02:00:00Z",
      content: { narrative: {} },
      facts: {
        findings: [],
        excluded_findings: [
          {
            reason: "authority_field_unavailable",
            affected_fields: ["email"],
            inclusion_state: "included",
            safe_evidence: { entity_kind: "student" },
          },
          {
            reason: "authority_field_unavailable",
            affected_fields: ["email"],
            inclusion_state: "included",
            safe_evidence: { entity_kind: "student" },
          },
        ],
        mutations: [],
        mutation_summary: { succeeded: 0, failed: 0 },
      },
    });

    const { client } = renderPage();

    expect(await screen.findByText("权威学生数据中有 2 条记录缺少邮箱。"))
      .toBeInTheDocument();
    client.clear();
  });

  it("keeps actual excluded findings separate from included quality warnings", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-mixed-inclusion-states",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-08-02T02:00:00Z",
      content: {
        narrative: {
          input_exception_analyses: [{
            reason_code: "authority_field_unavailable",
            title_zh: "学生被排除",
            analysis_zh: "模型将所有学生都标为排除。",
            impact_zh: "已排除。",
            suggestion_zh: "请重新同步。",
          }],
        },
      },
      facts: {
        findings: [],
        excluded_findings: [
          {
            reason: "authority_field_unavailable",
            affected_fields: ["class_name"],
            inclusion_state: "included",
            safe_evidence: { entity_kind: "student" },
          },
          {
            reason: "authority_field_unavailable",
            affected_fields: ["class_name"],
            inclusion_state: "excluded",
            disposition: "source_field_unavailable",
            safe_evidence: { entity_kind: "teacher" },
          },
        ],
        input_diagnostics: {
          reason_counts: { authority_field_unavailable: 2 },
          unavailable_field_counts: { class_name: 2 },
        },
        mutations: [],
        mutation_summary: { succeeded: 0, failed: 0 },
      },
    });

    const { client, container } = renderPage();

    expect(await screen.findByText("允许同步")).toBeInTheDocument();
    const warning = screen.getByText("权威记录数据缺少班级信息").closest("li");
    expect(warning).toHaveTextContent("权威记录数据中有 2 条记录缺少班级信息。");
    expect(warning).toHaveTextContent(
      "影响：班级信息不可用仅作为数据质量提醒；允许同步的记录仍保留在匹配与同步范围内；其他记录按排除或异常状态处理。",
    );
    expect(warning).not.toHaveTextContent("学生");
    expect(warning).not.toHaveTextContent("教师");
    expect(warning).not.toHaveTextContent("teacher");
    expect(container.querySelectorAll(".agent-report-exclusions > li")).toHaveLength(1);
    expect(
      within(container.querySelector(".agent-report-exclusions > li") as HTMLElement)
        .getByText("authority_field_unavailable"),
    ).toBeInTheDocument();
    client.clear();
  });

  it("does not duplicate other anomaly marks already covered by their narrative analysis", async () => {
    vi.spyOn(agentApi, "report").mockResolvedValue({
      id: "report-other-anomaly",
      task_id: "task-report-1",
      kind: "sync",
      terminal_state: "completed",
      rollback_eligible: false,
      deletion_eligible: false,
      created_at: "2026-08-02T02:00:00Z",
      content: {
        narrative: {
          input_exception_analyses: [{
            reason_code: "authority_identity_absent",
            title_zh: "权威数据缺少身份标识",
            analysis_zh: "一条权威记录无法可靠匹配。",
            impact_zh: "该记录已从治理范围排除。",
            suggestion_zh: "请补充身份标识后重试。",
          }],
        },
      },
      facts: {
        findings: [],
        excluded_findings: [{
          reason: "authority_identity_absent",
          inclusion_state: "anomaly",
          disposition: "mandatory_ai_anomaly",
        }],
        mutations: [],
        mutation_summary: { succeeded: 0, failed: 0 },
      },
    });

    const { client, container } = renderPage();

    expect(await screen.findByText("权威数据缺少身份标识")).toBeInTheDocument();
    expect(container.querySelectorAll(".agent-report-exclusions > li")).toHaveLength(0);
    client.clear();
  });
});
