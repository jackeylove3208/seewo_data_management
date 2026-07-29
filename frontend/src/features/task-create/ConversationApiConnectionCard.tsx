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
  configure,
  onChange,
}: {
  connection: AgentApiConnectionCard;
  configure?: (
    configuration: AgentApiConnectionConfiguration,
  ) => Promise<AgentApiConnectionCard>;
  onChange(connection: AgentApiConnectionCard): void;
}) {
  const [displayName, setDisplayName] = useState(
    connection.display_name ?? `${providerLabel(connection.provider_id)}组织连接`,
  );
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

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!configure || submitting) return;
    setSubmitting(true);
    setError(undefined);
    try {
      const configured = await configure({
        provider_id: connection.provider_id,
        display_name: displayName.trim(),
        required_secret_fields: connection.required_secret_fields,
        secret: Object.fromEntries(
          connection.required_secret_fields.map((field) => [
            field,
            secret[field]?.trim() ?? "",
          ]),
        ),
        connection_id: connection.connection_id,
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
