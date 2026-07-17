import { Alert, Button, Checkbox, Modal, Tag } from "antd";
import { ChevronDown, ChevronRight, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { BackButton } from "../../components/BackButton";
import { differencesFor, entityLabels } from "../../data/demoDifferences";
import type { DifferencePerson, DifferenceType, EntityType } from "../../types/domain";
import {
  getSelectionState,
  issueIdsFor,
  selectedPeopleCount,
  toggleCategory,
  toggleIssue,
  togglePerson,
} from "./selection";
import { useIssueSelection } from "./useIssueSelection";

const filterOptions: { value: "all" | DifferenceType; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "missing", label: "魔方缺失" },
  { value: "redundant", label: "魔方多余" },
  { value: "attribute", label: "信息不一致" },
  { value: "structure", label: "归属不一致" },
];

const differenceLabels: Record<DifferenceType, string> = {
  missing: "魔方缺失",
  redundant: "魔方多余",
  attribute: "信息不一致",
  structure: "归属不一致",
};

const riskLabels = { low: "低风险", medium: "中风险", high: "高风险" } as const;
const riskColors = { low: "success", medium: "warning", high: "error" } as const;

function isEntityType(value: string | undefined): value is EntityType {
  return Boolean(value && value in entityLabels);
}

function visiblePeople(people: DifferencePerson[], filter: "all" | DifferenceType) {
  if (filter === "all") return people;
  return people
    .map((person) => ({ ...person, issues: person.issues.filter((issue) => issue.type === filter) }))
    .filter((person) => person.issues.length > 0);
}

export function DifferenceCategoryPage() {
  const { taskId = "", entityType: routeEntityType } = useParams();
  const entityType = isEntityType(routeEntityType) ? routeEntityType : "teacher";
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
      if (next.has(personId)) next.delete(personId);
      else next.add(personId);
      return next;
    });
  }

  return (
    <main className="page-shell difference-page">
      <BackButton fallback={`/tasks/${taskId}`} label="返回问题类型对照" />
      <section className="detail-heading difference-heading">
        <div>
          <span className="heading-tags"><Tag>演示差异</Tag><Tag color="warning">待人工确认</Tag></span>
          <h1>{entityLabels[entityType]}问题</h1>
          <p>展开具体人员，并独立选择需要处理的每一项差异。</p>
        </div>
        <div className="detail-total"><span>相关问题</span><strong>{allIssueIds.length}</strong></div>
      </section>

      <section className="difference-toolbar">
        <div className="filter-tabs" aria-label="问题筛选">
          {filterOptions.map((option) => (
            <button className={filter === option.value ? "active" : ""} type="button" key={option.value} onClick={() => setFilter(option.value)}>
              {option.label}
            </button>
          ))}
        </div>
        <Checkbox
          checked={categoryState.checked}
          indeterminate={categoryState.indeterminate}
          onChange={(event) => setSelection((current) => toggleCategory(current, allPeople, event.target.checked))}
        >
          选择全部问题
        </Checkbox>
      </section>

      <section className="person-list" aria-label={`${entityLabels[entityType]}问题列表`}>
        {people.map((person) => {
          const personIssueIds = issueIdsFor([person]);
          const personState = getSelectionState(selection, personIssueIds);
          const isExpanded = expanded.has(person.id);
          return (
            <article className="person-group" key={person.id}>
              <div className="person-row">
                <Checkbox
                  aria-label={`选择${person.name}的全部问题`}
                  checked={personState.checked}
                  indeterminate={personState.indeterminate}
                  onChange={(event) => setSelection((current) => togglePerson(current, person, event.target.checked))}
                />
                <button className="person-toggle" type="button" onClick={() => toggleExpanded(person.id)}>
                  <span className="person-avatar">{person.name.slice(0, 1)}</span>
                  <span className="person-copy"><strong>{person.name}</strong><small>{person.context}</small></span>
                  <span className="person-problem">{person.issues.map((issue) => differenceLabels[issue.type]).join(" · ")}</span>
                  <span className="person-count">{person.issues.length} 项</span>
                  {isExpanded ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
                </button>
              </div>
              {isExpanded && (
                <div className="issue-list">
                  {person.issues.map((issue) => (
                    <section className="issue-item" key={issue.id}>
                      <div className="issue-heading">
                        <Checkbox
                          aria-label={`选择${person.name}的${issue.field}`}
                          checked={selection.has(issue.id)}
                          disabled={!issue.selectable}
                          onChange={(event) => setSelection((current) => toggleIssue(current, issue.id, event.target.checked))}
                        />
                        <strong>{issue.field}</strong>
                        <Tag>{differenceLabels[issue.type]}</Tag>
                        <Tag color={riskColors[issue.risk]}>{riskLabels[issue.risk]}</Tag>
                      </div>
                      <div className="value-comparison">
                        <div className="comparison-source"><span>三方系统</span><strong>{issue.sourceValue}</strong></div>
                        <div className="comparison-target"><span>希沃魔方</span><strong>{issue.targetValue}</strong></div>
                      </div>
                      <div className="recommendation"><Sparkles size={15} /><span><small>建议</small>{issue.recommendation}</span></div>
                    </section>
                  ))}
                </div>
              )}
            </article>
          );
        })}
      </section>

      <footer className="selection-bar" aria-live="polite">
        <span>已选择 {selectedPeople} 人，共 {selectedIssues} 个问题</span>
        <div>
          <Button disabled={selectedIssues === 0} onClick={() => setSelection(() => new Set())}>取消选择</Button>
          <Button type="primary" disabled={selectedIssues === 0} onClick={() => setConfirmOpen(true)}>处理选中问题</Button>
        </div>
      </footer>

      <Modal
        open={confirmOpen}
        title="确认处理范围"
        okText="确认"
        cancelText="返回检查"
        onCancel={() => setConfirmOpen(false)}
        onOk={() => setConfirmOpen(false)}
      >
        <p>本次共选择 {selectedPeople} 人、{selectedIssues} 个具体问题。</p>
        <Alert type="info" showIcon message="当前仅生成演示处理范围，治理执行接口接入后才能实际修改魔方数据。" />
      </Modal>
    </main>
  );
}
