import { useEffect, useState, type FormEvent } from "react";

import type {
  AgentApiConnectionCard,
  AgentApiConnectionConfiguration,
} from "../../api/agent";

const providerLabels: Record<string, string> = {
  dingtalk: "钉钉",
  wecom: "企业微信",
};

const secretFieldLabels: Record<string, string> = {
  app_key: "AppKey",
  app_secret: "AppSecret",
  corp_id: "CorpID",
  corp_secret: "CorpSecret",
};

function providerLabel(providerId: string) {
  return providerLabels[providerId] ?? providerId;
}

export function ConversationApiConnectionCard({
  connection,
  conversationId,
  configure,
  onChange,
}: {
  connection: AgentApiConnectionCard;
  conversationId: string;
  configure?: (
    configuration: AgentApiConnectionConfiguration,
  ) => Promise<AgentApiConnectionCard>;
  onChange(connection: AgentApiConnectionCard): void;
}) {
  const [displayName, setDisplayName] = useState(
    connection.display_name ?? `${providerLabel(connection.provider_id)}组织连接`,
  );
  const [personEntityKind, setPersonEntityKind] = useState<
    "" | "teacher" | "student"
  >("");
  const [rootDepartmentId, setRootDepartmentId] = useState("");
  const [numberField, setNumberField] = useState("");
  const [classNameField, setClassNameField] = useState("");
  const [secret, setSecret] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    setDisplayName(
      connection.display_name ?? `${providerLabel(connection.provider_id)}组织连接`,
    );
    setSecret({});
    setError(undefined);
  }, [connection.connection_id, connection.display_name, connection.provider_id]);

  useEffect(() => {
    if (connection.state !== "configuration_required") return;
    setPersonEntityKind("");
    setRootDepartmentId("");
    setNumberField("");
    setClassNameField("");
  }, [connection.display_name, connection.provider_id, connection.state]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!configure || submitting) return;
    setSubmitting(true);
    setError(undefined);
    try {
      const configured = await configure({
        conversation_id: conversationId,
        provider_id: connection.provider_id,
        display_name: displayName.trim(),
        required_secret_fields: connection.required_secret_fields,
        public_configuration: {
          person_entity_kind: personEntityKind as "teacher" | "student",
          root_department_id: Number(rootDepartmentId),
          ...(numberField.trim() ? { number_field: numberField.trim() } : {}),
          ...(personEntityKind === "student" && classNameField.trim()
            ? { class_name_field: classNameField.trim() }
            : {}),
        },
        secret: Object.fromEntries(
          connection.required_secret_fields.map((field) => [
            field,
            secret[field]?.trim() ?? "",
          ]),
        ),
        ...(connection.connection_id
          ? { connection_id: connection.connection_id }
          : {}),
      });
      setSecret({});
      onChange(configured);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "连接配置失败，请重试。");
    } finally {
      setSubmitting(false);
    }
  }

  const active = connection.state === "active";
  return (
    <article
      className="conversation-card api-connection-card"
      aria-label="API 连接配置"
    >
      <strong>{providerLabel(connection.provider_id)}连接</strong>
      {active ? (
        <p>连接测试通过</p>
      ) : (
        <form onSubmit={(event) => void submit(event)}>
          <label>
            <span>连接名称</span>
            <input
              aria-label="连接名称"
              value={displayName}
              maxLength={255}
              required
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </label>
          <label>
            <span>人员类型</span>
            <select
              aria-label="人员类型"
              required
              value={personEntityKind}
              onChange={(event) => setPersonEntityKind(
                event.target.value as "teacher" | "student",
              )}
            >
              <option value="" disabled>请选择</option>
              <option value="teacher">教师</option>
              <option value="student">学生</option>
            </select>
          </label>
          <label>
            <span>根部门 ID</span>
            <input
              aria-label="根部门 ID"
              type="number"
              min="1"
              step="1"
              required
              value={rootDepartmentId}
              onChange={(event) => setRootDepartmentId(event.target.value)}
            />
          </label>
          <label>
            <span>人员编号字段</span>
            <input
              aria-label="人员编号字段"
              value={numberField}
              placeholder="可选，例如 student_number"
              onChange={(event) => setNumberField(event.target.value)}
            />
          </label>
          {personEntityKind === "student" && (
            <label>
              <span>班级字段</span>
              <input
                aria-label="班级字段"
                value={classNameField}
                placeholder="可选，例如 class_name"
                onChange={(event) => setClassNameField(event.target.value)}
              />
            </label>
          )}
          {connection.required_secret_fields.map((field) => (
            <label key={field}>
              <span>{secretFieldLabels[field] ?? field}</span>
              <input
                aria-label={secretFieldLabels[field] ?? field}
                type="password"
                autoComplete="new-password"
                value={secret[field] ?? ""}
                required
                onChange={(event) => setSecret((current) => ({
                  ...current,
                  [field]: event.target.value,
                }))}
              />
            </label>
          ))}
          <button type="submit" disabled={!configure || submitting}>
            {submitting ? "正在测试连接" : "保存并测试连接"}
          </button>
        </form>
      )}
      {connection.safe_error_code && (
        <p role="alert">连接测试未通过：{connection.safe_error_code}</p>
      )}
      {error && <p role="alert">{error}</p>}
    </article>
  );
}
