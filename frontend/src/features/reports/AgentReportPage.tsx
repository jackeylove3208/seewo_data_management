import { useQuery } from "@tanstack/react-query";
import { Alert, Card, Descriptions, Empty, List, Skeleton, Tag } from "antd";
import { useParams } from "react-router-dom";

import { agentApi } from "../../api/agent";
import { BackButton } from "../../components/BackButton";

type ReportItem = Record<string, unknown>;

function records(value: unknown): ReportItem[] {
  return Array.isArray(value)
    ? value.filter((item): item is ReportItem => Boolean(item) && typeof item === "object")
    : [];
}

function text(value: unknown, fallback = "—") {
  return typeof value === "string" || typeof value === "number" ? String(value) : fallback;
}

export function AgentReportPage() {
  const { taskId = "" } = useParams();
  const report = useQuery({
    queryKey: ["agent-report", taskId],
    queryFn: ({ signal }) => agentApi.report(taskId, signal),
  });

  if (report.isLoading) {
    return <main className="page-shell"><BackButton fallback={`/tasks/${taskId}`} label="返回任务详情" /><Skeleton active paragraph={{ rows: 8 }} /></main>;
  }
  if (report.isError || !report.data) {
    return <main className="page-shell"><BackButton fallback={`/tasks/${taskId}`} label="返回任务详情" /><Alert type="error" showIcon message="任务报告读取失败" description="报告可能尚未生成，请稍后重试。" /></main>;
  }

  const facts = report.data.facts;
  const findings = records(facts.findings);
  const excluded = records(facts.excluded_findings ?? facts.invalid_rows);
  const mutations = records(facts.mutations);
  const mutationSummary = typeof facts.mutation_summary === "object" && facts.mutation_summary
    ? facts.mutation_summary as ReportItem
    : {};

  return (
    <main className="page-shell agent-report-page">
      <BackButton fallback={`/tasks/${taskId}`} label="返回任务详情" />
      <section className="page-heading">
        <div>
          <p className="eyebrow">AGENT REPORT</p>
          <h1>{report.data.kind === "rollback" ? "回滚任务报告" : "数据同步报告"}</h1>
          <p>报告只展示后端持久化事实和已脱敏的 Agent 说明。</p>
        </div>
        <Tag color={report.data.terminal_state === "completed" ? "success" : "warning"}>
          {report.data.terminal_state}
        </Tag>
      </section>

      <Descriptions bordered size="small" column={{ xs: 1, sm: 2, md: 4 }}>
        <Descriptions.Item label="发现问题">{findings.length}</Descriptions.Item>
        <Descriptions.Item label="排除/异常">{excluded.length}</Descriptions.Item>
        <Descriptions.Item label="成功变更">{text(mutationSummary.succeeded, "0")}</Descriptions.Item>
        <Descriptions.Item label="失败变更">{text(mutationSummary.failed, "0")}</Descriptions.Item>
      </Descriptions>

      <Card title="需要处理的问题">
        {findings.length ? (
          <List
            dataSource={findings}
            renderItem={(item) => (
              <List.Item>
                <List.Item.Meta
                  title={text(item.category_zh ?? item.kind, "未分类问题")}
                  description={text(item.analysis_zh ?? item.reason, "已记录结构化证据")}
                />
              </List.Item>
            )}
          />
        ) : <Empty description="没有需要治理的问题" />}
      </Card>

      {excluded.length > 0 && (
        <Card title="输入异常与排除项">
          <List
            dataSource={excluded}
            renderItem={(item) => <List.Item>{text(item.reason ?? item.disposition ?? item.source, "输入数据不符合规范")}</List.Item>}
          />
        </Card>
      )}

      <Card title="治理结果">
        {mutations.length ? (
          <List
            dataSource={mutations}
            renderItem={(item) => (
              <List.Item>
                <Tag color={item.status === "succeeded" ? "success" : "error"}>{text(item.status)}</Tag>
                <span>{text(item.operation, "操作")} · {text(item.entity_kind, "实体")}</span>
              </List.Item>
            )}
          />
        ) : <Empty description="本任务没有修改目标数据" />}
      </Card>
    </main>
  );
}
