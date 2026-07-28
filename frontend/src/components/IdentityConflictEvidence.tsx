import type {
  AgentGraphIdentityConflict,
  AgentGraphIdentityRecord,
} from "../api/agent";

const entityLabels: Record<string, string> = {
  student: "学生",
  teacher: "教师",
  department: "部门",
};

const fields: Array<{
  key: keyof AgentGraphIdentityRecord;
  label: string;
}> = [
  { key: "entity_kind", label: "实体" },
  { key: "category", label: "类别" },
  { key: "name", label: "姓名" },
  { key: "number", label: "编号" },
  { key: "class_name", label: "班级" },
  { key: "phone_masked", label: "电话" },
  { key: "email_masked", label: "邮箱" },
];

function displayValue(
  record: AgentGraphIdentityRecord,
  key: keyof AgentGraphIdentityRecord,
) {
  const value = record[key];
  if (!value) return "未填写";
  if (key === "entity_kind") return entityLabels[value] ?? value;
  return value;
}

function candidateLabel(index: number) {
  return index < 26 ? String.fromCharCode(65 + index) : String(index + 1);
}

function differsFromSubject(
  subject: AgentGraphIdentityRecord,
  candidate: AgentGraphIdentityRecord,
  key: keyof AgentGraphIdentityRecord,
) {
  return displayValue(subject, key) !== displayValue(candidate, key);
}

function IdentityRecordCard({
  title,
  record,
  subject,
}: {
  title: string;
  record: AgentGraphIdentityRecord;
  subject?: AgentGraphIdentityRecord;
}) {
  return (
    <article className="identity-conflict-record">
      <strong>{title}</strong>
      <dl>
        {fields.map(({ key, label }) => (
          <div
            className={
              subject && differsFromSubject(subject, record, key)
                ? "identity-conflict-field is-different"
                : "identity-conflict-field"
            }
            key={key}
          >
            <dt>{label}</dt>
            <dd>{displayValue(record, key)}</dd>
          </div>
        ))}
      </dl>
    </article>
  );
}

export function IdentityConflictEvidence({
  conflict,
  index,
  total,
}: {
  conflict: AgentGraphIdentityConflict;
  index: number;
  total: number;
}) {
  return (
    <section className="identity-conflict-evidence" aria-label="身份冲突证据">
      <header>
        <span>第 {index + 1}/{total} 条</span>
        <p>{conflict.summary_zh}</p>
      </header>
      <div className="identity-conflict-records">
        <IdentityRecordCard title="希沃记录" record={conflict.subject} />
        {conflict.candidates.map((candidate, candidateIndex) => (
          <IdentityRecordCard
            key={`${conflict.clarification_id}-${candidateIndex}`}
            title={`第三方候选 ${candidateLabel(candidateIndex)}`}
            record={candidate}
            subject={conflict.subject}
          />
        ))}
      </div>
      <small>
        请说明应采用哪个第三方候选；如果候选都不对应，也可以明确将这条希沃记录按“希沃多余”处理。
      </small>
    </section>
  );
}
