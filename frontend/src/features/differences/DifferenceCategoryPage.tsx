import { useInfiniteQuery } from "@tanstack/react-query";
import { Alert, Button, Empty, Select, Skeleton, Tag } from "antd";
import { ArrowRight, RefreshCw, Sparkles } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { queryKeys } from "../../api/queryKeys";
import {
  reconciliationApi,
  type AnalysisStatus,
  type DifferenceFilters,
  type DifferenceItem,
  type DifferenceType as ApiDifferenceType,
  type RiskLevel,
} from "../../api/reconciliation";
import { BackButton } from "../../components/BackButton";
import type { EntityType } from "../../types/domain";
import { AnalysisModal } from "../analysis/AnalysisModal";
import { entityTypeLabels, fieldLabel } from "../analysis/localization";

const differenceLabels: Record<ApiDifferenceType, string> = {
  seewo_missing: "希沃缺失",
  seewo_redundant: "希沃多余",
  attribute_conflict: "属性冲突",
  structure_conflict: "归属冲突",
  duplicate_conflict: "重复冲突",
};
const riskLabels = { low: "低风险", medium: "中风险", high: "高风险" } as const;
const riskColors = { low: "success", medium: "warning", high: "error" } as const;
const analysisLabels: Record<AnalysisStatus, string> = { pending: "AI 分析中", succeeded: "分析完成", manual_review: "仅人工", failed: "分析失败" };

function isEntityType(value: string | undefined): value is EntityType {
  return Boolean(value && value in entityTypeLabels);
}

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "未设置";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function entityName(item: DifferenceItem) {
  const source = item.evidence.source_payload?.name;
  const target = item.evidence.target_payload?.name;
  return displayValue(source ?? target ?? `${entityTypeLabels[item.entity_type]}记录`);
}

function RealDifferencePage({ taskId, entityType }: { taskId: string; entityType: EntityType }) {
  const [differenceType, setDifferenceType] = useState<ApiDifferenceType | undefined>();
  const [analysisStatus, setAnalysisStatus] = useState<AnalysisStatus | undefined>();
  const [risk, setRisk] = useState<RiskLevel | undefined>();
  const [selectedId, setSelectedId] = useState<string>();
  const filters: DifferenceFilters = { entity_type: entityType, difference_type: differenceType, analysis_status: analysisStatus, risk, limit: 25 };
  const differences = useInfiniteQuery({
    queryKey: queryKeys.differences(taskId, filters),
    queryFn: ({ pageParam, signal }) => reconciliationApi.listDifferences(taskId, { ...filters, cursor: pageParam }, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: (query) => query.state.data?.pages.some((page) => page.items.some((item) => item.analysis_status === "pending")) ? 2_000 : false,
  });
  const items = differences.data?.pages.flatMap((page) => page.items) ?? [];
  const selected = items.find((item) => item.id === selectedId);

  return (
    <main className="page-shell difference-page real-difference-page apple-page">
      <BackButton fallback={`/tasks/${taskId}`} label="返回任务详情" />
      <section className="detail-heading difference-heading">
        <div><span className="heading-tags"><Tag color="processing">真实任务</Tag><Tag>分析后生成待执行方案</Tag></span><h1>{entityTypeLabels[entityType]}差异</h1><p>检查权威数据与希沃快照，并按条选择 AI 方案或人工修改。</p></div>
        <div className="detail-total"><span>当前结果</span><strong>{items.length}</strong></div>
      </section>

      <section className="real-filter-bar" aria-label="差异筛选">
        <Select aria-label="差异类型" placeholder="全部差异类型" allowClear value={differenceType} onChange={setDifferenceType} options={Object.entries(differenceLabels).map(([value, label]) => ({ value, label }))} />
        <Select aria-label="分析状态" placeholder="全部分析状态" allowClear value={analysisStatus} onChange={setAnalysisStatus} options={Object.entries(analysisLabels).map(([value, label]) => ({ value, label }))} />
        <Select aria-label="风险等级" placeholder="全部风险等级" allowClear value={risk} onChange={setRisk} options={Object.entries(riskLabels).map(([value, label]) => ({ value, label }))} />
        <Button icon={<RefreshCw size={14} />} loading={differences.isRefetching} onClick={() => void differences.refetch()}>刷新</Button>
      </section>

      {differences.isLoading && <div className="difference-loading"><Skeleton active paragraph={{ rows: 6 }} /></div>}
      {differences.isError && <Alert type="error" showIcon message="差异读取失败" description={differences.error.message} action={<Button onClick={() => void differences.refetch()}>重试</Button>} />}
      {!differences.isLoading && !differences.isError && items.length === 0 && <div className="difference-empty"><Empty description="当前筛选条件下没有差异" /></div>}

      <section className="real-difference-list" aria-label={`${entityTypeLabels[entityType]}真实差异列表`}>
        {items.map((item) => (
          <article className="real-difference-item" key={item.id}>
            <header>
              <div className="difference-identity"><span>{entityName(item).slice(0, 1)}</span><div><strong>{entityName(item)}</strong><small>{differenceLabels[item.difference_type]} · 版本 {item.version}</small></div></div>
              <div className="difference-statuses">
                {item.risk && <Tag color={riskColors[item.risk]}>{riskLabels[item.risk]}</Tag>}
                <Tag color={item.analysis_status === "failed" ? "error" : item.analysis_status === "pending" ? "processing" : item.analysis_status === "manual_review" ? "warning" : "success"}>{analysisLabels[item.analysis_status]}</Tag>
                {item.proposal_status && <Tag color="blue">待治理执行 v{item.current_proposal_version}</Tag>}
              </div>
            </header>
            <div className="real-field-list">
              {(item.evidence.fields.length > 0 ? item.evidence.fields : [{ field: "实体状态", source_value: item.evidence.source_payload ? "存在" : "缺失", target_value: item.evidence.target_payload ? "存在" : "缺失", normalized_source: null, normalized_target: null, comparison: "attribute" as const }]).map((field) => (
                <div className="real-field-row" key={field.field}>
                  <strong>{fieldLabel(field.field)}</strong>
                  <div><small>三方系统</small><span>{displayValue(field.source_value)}</span></div>
                  <ArrowRight size={15} />
                  <div><small>希沃</small><span>{displayValue(field.target_value)}</span></div>
                </div>
              ))}
            </div>
            <footer>
              <span>{item.evidence.match_evidence.length > 0 ? `${item.evidence.match_evidence.length} 条匹配证据` : `规则 ${item.evidence.comparison_rule_version}`}</span>
              <Button type="primary" ghost icon={<Sparkles size={15} />} onClick={() => setSelectedId(item.id)}>查看 AI 分析</Button>
            </footer>
          </article>
        ))}
      </section>
      {differences.hasNextPage && <div className="load-more"><Button loading={differences.isFetchingNextPage} onClick={() => void differences.fetchNextPage()}>加载更多</Button></div>}
      {selected && <AnalysisModal open difference={selected} onClose={() => setSelectedId(undefined)} />}
    </main>
  );
}

export function DifferenceCategoryPage() {
  const { taskId = "", entityType: routeEntityType } = useParams();
  const entityType = isEntityType(routeEntityType) ? routeEntityType : "teacher";
  return <RealDifferencePage taskId={taskId} entityType={entityType} />;
}
