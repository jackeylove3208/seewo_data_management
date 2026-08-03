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

const safeErrorMessages: Record<string, string> = {
  connector_permission_denied:
    "钉钉部门或人员目录权限或可见范围不足，请在钉钉开发者后台修正后重试。",
  connector_entity_classification_unknown:
    "部分有人员的行政单元无法判断为教职工或学生，请调整钉钉组织归属后重试。",
  connector_entity_classification_ambiguous:
    "存在同时归属教职工与学生分支的人员，请调整钉钉组织归属后重试。",
  connector_entity_classification_invalid:
    "钉钉行政单元分类结果不完整或互相矛盾，请重新测试连接。",
  connector_entity_classification_unavailable:
    "暂时无法完成钉钉行政单元分类，请稍后重试。",
  connector_organization_changed:
    "钉钉组织结构在检测期间发生变化，请重新测试连接。",
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
  const [syncScope, setSyncScope] = useState<
    "" | "department" | "people" | "all"
  >("");
  const [rootDepartmentId, setRootDepartmentId] = useState("");
  const [numberField, setNumberField] = useState("");
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
    setSyncScope("");
    setRootDepartmentId("");
    setNumberField("");
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
          sync_scope: syncScope as "department" | "people" | "all",
          root_department_id: Number(rootDepartmentId),
          ...(syncScope === "people" || syncScope === "all"
            ? { person_classification_mode: "organization_unit_llm" as const }
            : {}),
          ...(numberField.trim() ? { number_field: numberField.trim() } : {}),
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
      onChange(configured);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "连接配置失败，请重试。");
    } finally {
      setSecret({});
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
        <form
          className="api-connection-form"
          onSubmit={(event) => void submit(event)}
        >
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
            <span>同步范围</span>
            <select
              aria-label="同步范围"
              required
              value={syncScope}
              onChange={(event) => setSyncScope(
                event.target.value as "department" | "people" | "all",
              )}
            >
              <option value="" disabled>请选择</option>
              <option value="department">部门</option>
              <option value="people">人员</option>
              <option value="all">全部</option>
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
              placeholder="可选，例如 job_number"
              onChange={(event) => setNumberField(event.target.value)}
            />
          </label>
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
        <p role="alert">
          {safeErrorMessages[connection.safe_error_code]
            ?? `连接测试未通过：${connection.safe_error_code}`}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
    </article>
  );
}
