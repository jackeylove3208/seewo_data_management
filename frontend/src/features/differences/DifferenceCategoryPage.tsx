import { useInfiniteQuery } from "@tanstack/react-query";
import { Alert, Button, Checkbox, Empty, Modal, Select, Skeleton, Tag } from "antd";
import { ArrowRight, ChevronDown, ChevronRight, RefreshCw, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
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
import { differencesFor, entityLabels } from "../../data/demoDifferences";
import { findTask } from "../../data/taskHistory";
import type { DifferencePerson, DifferenceType, EntityType } from "../../types/domain";
import { AnalysisModal } from "../analysis/AnalysisModal";
import { fieldLabel } from "../analysis/localization";
import {
  getSelectionState,
  issueIdsFor,
  selectedPeopleCount,
  toggleCategory,
  toggleIssue,
  togglePerson,
} from "./selection";
import { useIssueSelection } from "./useIssueSelection";

const demoFilterOptions: { value: "all" | DifferenceType; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "missing", label: "魔方缺失" },
  { value: "redundant", label: "魔方多余" },
  { value: "attribute", label: "信息不一致" },
  { value: "structure", label: "归属不一致" },
];

const differenceLabels: Record<ApiDifferenceType, string> = {
  seewo_missing: "希沃缺失",
  seewo_redundant: "希沃多余",
  attribute_conflict: "属性冲突",
  structure_conflict: "归属冲突",
  duplicate_conflict: "重复冲突",
};
const demoDifferenceLabels: Record<DifferenceType, string> = { missing: "魔方缺失", redundant: "魔方多余", attribute: "信息不一致", structure: "归属不一致" };
const riskLabels = { low: "低风险", medium: "中风险", high: "高风险" } as const;
const riskColors = { low: "success", medium: "warning", high: "error" } as const;
const analysisLabels: Record<AnalysisStatus, string> = { pending: "AI 分析中", succeeded: "分析完成", manual_review: "仅人工", failed: "分析失败" };

function isEntityType(value: string | undefined): value is EntityType {
  return Boolean(value && value in entityLabels);
}

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "未设置";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function entityName(item: DifferenceItem) {
  const source = item.evidence.source_payload?.name;
  const target = item.evidence.target_payload?.name;
  return displayValue(source ?? target ?? `${entityLabels[item.entity_type]}记录`);
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
        <div><span className="heading-tags"><Tag color="processing">真实任务</Tag><Tag>分析后生成待执行方案</Tag></span><h1>{entityLabels[entityType]}差异</h1><p>检查权威数据与希沃快照，并按条选择 AI 方案或人工修改。</p></div>
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

      <section className="real-difference-list" aria-label={`${entityLabels[entityType]}真实差异列表`}>
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

function visiblePeople(people: DifferencePerson[], filter: "all" | DifferenceType) {
  if (filter === "all") return people;
  return people.map((person) => ({ ...person, issues: person.issues.filter((issue) => issue.type === filter) })).filter((person) => person.issues.length > 0);
}

function DemoDifferencePage({ taskId, entityType }: { taskId: string; entityType: EntityType }) {
  const allPeople = useMemo(() => differencesFor(entityType), [entityType]);
  const [filter, setFilter] = useState<"all" | DifferenceType>("all");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const { selection, setSelection } = useIssueSelection(taskId);
  const people = visiblePeople(allPeople, filter);
  const allIssueIds = issueIdsFor(allPeople);
  const categoryState = getSelectionState(selection, allIssueIds);
  const selectedIssues = selection.size;
  const selectedPeople = selectedPeopleCount(selection, allPeople);

  function toggleExpanded(personId: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(personId)) next.delete(personId); else next.add(personId);
      return next;
    });
  }

  return (
    <main className="page-shell difference-page apple-page">
      <BackButton fallback={`/tasks/${taskId}`} label="返回问题类型对照" />
      <section className="detail-heading difference-heading"><div><span className="heading-tags"><Tag>演示差异</Tag><Tag color="warning">待人工确认</Tag></span><h1>{entityLabels[entityType]}问题</h1><p>展开具体人员，并独立选择需要处理的每一项问题。</p></div><div className="detail-total"><span>相关问题</span><strong>{allIssueIds.length}</strong></div></section>
      <section className="difference-toolbar"><div className="filter-tabs" aria-label="问题筛选">{demoFilterOptions.map((option) => <button className={filter === option.value ? "active" : ""} type="button" key={option.value} onClick={() => setFilter(option.value)}>{option.label}</button>)}</div><Checkbox checked={categoryState.checked} indeterminate={categoryState.indeterminate} onChange={(event) => setSelection((current) => toggleCategory(current, allPeople, event.target.checked))}>选择全部问题</Checkbox></section>
      <section className="person-list" aria-label={`${entityLabels[entityType]}问题列表`}>
        {people.map((person) => {
          const personState = getSelectionState(selection, issueIdsFor([person]));
          const isExpanded = expanded.has(person.id);
          return <article className="person-group" key={person.id}><div className="person-row"><Checkbox aria-label={`选择${person.name}的全部问题`} checked={personState.checked} indeterminate={personState.indeterminate} onChange={(event) => setSelection((current) => togglePerson(current, person, event.target.checked))} /><button className="person-toggle" type="button" onClick={() => toggleExpanded(person.id)}><span className="person-avatar">{person.name.slice(0, 1)}</span><span className="person-copy"><strong>{person.name}</strong><small>{person.context}</small></span><span className="person-problem">{person.issues.map((issue) => demoDifferenceLabels[issue.type]).join(" · ")}</span><span className="person-count">{person.issues.length} 项</span>{isExpanded ? <ChevronDown size={18} /> : <ChevronRight size={18} />}</button></div>{isExpanded && <div className="issue-list">{person.issues.map((issue) => <section className="issue-item" key={issue.id}><div className="issue-heading"><Checkbox aria-label={`选择${person.name}的${issue.field}`} checked={selection.has(issue.id)} disabled={!issue.selectable} onChange={(event) => setSelection((current) => toggleIssue(current, issue.id, event.target.checked))} /><strong>{issue.field}</strong><Tag>{demoDifferenceLabels[issue.type]}</Tag><Tag color={riskColors[issue.risk]}>{riskLabels[issue.risk]}</Tag></div><div className="value-comparison"><div className="comparison-source"><span>三方系统</span><strong>{issue.sourceValue}</strong></div><div className="comparison-target"><span>希沃魔方</span><strong>{issue.targetValue}</strong></div></div><div className="recommendation"><Sparkles size={15} /><span><small>建议</small>{issue.recommendation}</span></div></section>)}</div>}</article>;
        })}
      </section>
      <footer className="selection-bar" aria-live="polite"><span>已选择 {selectedPeople} 人，共 {selectedIssues} 个问题</span><div><Button disabled={selectedIssues === 0} onClick={() => setSelection(() => new Set())}>取消选择</Button><Button type="primary" disabled={selectedIssues === 0} onClick={() => setConfirmOpen(true)}>处理选中问题</Button></div></footer>
      <Modal open={confirmOpen} title="确认处理范围" okText="确认" cancelText="返回检查" onCancel={() => setConfirmOpen(false)} onOk={() => setConfirmOpen(false)}><p>本次共选择 {selectedPeople} 人、{selectedIssues} 个具体问题。</p><Alert type="info" showIcon message="当前仅生成演示处理范围，治理执行接口接入后才能实际修改魔方数据。" /></Modal>
    </main>
  );
}

export function DifferenceCategoryPage() {
  const { taskId = "", entityType: routeEntityType } = useParams();
  const entityType = isEntityType(routeEntityType) ? routeEntityType : "teacher";
  const task = findTask(taskId);
  return task?.isDemo ? <DemoDifferencePage taskId={taskId} entityType={entityType} /> : <RealDifferencePage taskId={taskId} entityType={entityType} />;
}
