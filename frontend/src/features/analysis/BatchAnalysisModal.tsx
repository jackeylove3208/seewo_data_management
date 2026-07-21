import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Modal, Spin, Tag } from "antd";
import { ArrowRight, CheckCircle2, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiError } from "../../api/client";
import { queryKeys } from "../../api/queryKeys";
import { reconciliationApi, type BatchProposalResult } from "../../api/reconciliation";
import type { EntityType } from "../../types/domain";
import { displayFieldValue, entityTypeLabels, fieldLabel, operationLabels, riskColors, riskLabels } from "./localization";

export function BatchAnalysisModal({ open, taskId, jobId, onClose, onOpenEntityType }: {
  open: boolean;
  taskId: string;
  jobId: string;
  onClose: () => void;
  onOpenEntityType?: (entityType: EntityType) => void;
}) {
  const queryClient = useQueryClient();
  const [result, setResult] = useState<BatchProposalResult>();
  const idempotencyKey = useRef("");
  const preview = useQuery({
    queryKey: ["batch-proposal-preview", taskId, jobId],
    queryFn: () => reconciliationApi.previewProposalBatch(taskId, { analysis_job_id: jobId }),
    enabled: open && Boolean(taskId && jobId),
    staleTime: 30_000,
  });
  const confirm = useMutation({
    mutationFn: () => {
      if (!idempotencyKey.current) {
        idempotencyKey.current = globalThis.crypto?.randomUUID?.() ?? `batch-${Date.now()}`;
      }
      return reconciliationApi.confirmProposalBatch(taskId, {
        preview_token: preview.data!.preview_token,
        idempotency_key: idempotencyKey.current,
      });
    },
    onSuccess: (value) => {
      setResult(value);
      void queryClient.invalidateQueries({ queryKey: queryKeys.analysisSummary(taskId) });
      void queryClient.invalidateQueries({ queryKey: ["differences", taskId] });
    },
  });

  useEffect(() => {
    if (!open) setResult(undefined);
  }, [open]);

  useEffect(() => {
    if (!open || !preview.data?.preview_token) return;
    idempotencyKey.current = globalThis.crypto?.randomUUID?.() ?? `batch-${Date.now()}`;
  }, [open, preview.data?.preview_token]);

  const conflict = confirm.error instanceof ApiError && confirm.error.status === 409;

  function refreshPreview() {
    setResult(undefined);
    idempotencyKey.current = "";
    confirm.reset();
    void preview.refetch();
  }

  return (
    <Modal className="batch-analysis-modal" width={780} open={open} title="AI 一键处理" footer={null} onCancel={onClose} destroyOnHidden>
      {preview.isLoading && <div className="modal-loading"><Spin /><span>正在生成批量处理预览</span></div>}
      {preview.isError && <Alert type="error" showIcon message="批量预览失败" description="请重新读取最新分析结果。" action={<Button onClick={() => void preview.refetch()}>重试</Button>} />}
      {!result && preview.data && (
        <div className="batch-preview">
          <Alert type="info" showIcon message="确认后仅生成待执行方案，不会直接修改希沃数据。" />
          <section>
            <header><Sparkles size={17} /><strong>将采用 {preview.data.included.length} 条安全推荐方案</strong></header>
            <div className="batch-preview-list">
              {preview.data.included.map((item) => (
                <article key={item.difference_id}>
                  <div><strong>{item.title}</strong><small>{entityTypeLabels[item.entity_type]} · {operationLabels[item.operation_type]}</small></div>
                  <Tag color={riskColors[item.risk]}>{riskLabels[item.risk]}</Tag>
                  {item.changes.map((change) => <p key={change.field}><span>{fieldLabel(change.field)}</span><b>{displayFieldValue(change.field, change.before)}</b><ArrowRight size={14} /><b>{displayFieldValue(change.field, change.after)}</b></p>)}
                </article>
              ))}
            </div>
          </section>
          {preview.data.excluded.length > 0 && (
            <section className="batch-exclusions">
              <header><strong>本次自动排除 {preview.data.excluded.length} 项</strong></header>
              <div>{preview.data.excluded.map((item) => <Tag key={item.difference_id}>{entityTypeLabels[item.entity_type]} · {item.reason_label}</Tag>)}</div>
            </section>
          )}
          <div className="modal-command-row">
            <Button onClick={onClose}>返回检查</Button>
            <Button type="primary" loading={confirm.isPending || preview.isFetching} disabled={preview.data.included.length === 0 || preview.isFetching} onClick={() => confirm.mutate()}>
              确认生成 {preview.data.included.length} 份待执行方案
            </Button>
          </div>
          {confirm.isError && <Alert className="modal-error" type="error" showIcon message="批量确认失败" description={conflict ? "数据或预览已变化，请刷新后重新确认。" : "请求未完成，可直接重试；系统会复用同一幂等键。"} action={conflict ? <Button onClick={refreshPreview}>刷新预览</Button> : undefined} />}
        </div>
      )}
      {result && (
        <div className="proposal-success batch-result">
          <CheckCircle2 size={34} />
          <h3>已生成 {result.created} 份待执行方案</h3>
          <p>跳过 {result.skipped} 项，失败 {result.failed} 项。后续仍需进入治理执行完成预检和审核。</p>
          {result.items.some((item) => item.status !== "created") && <ul className="batch-result-details">{result.items.filter((item) => item.status !== "created").map((item) => <li key={item.difference_id}>{item.reason ?? "当前项目未生成方案"}</li>)}</ul>}
          <div className="modal-command-row">
            {result.created > 0 && preview.data?.included[0] && onOpenEntityType && <Button onClick={() => onOpenEntityType(preview.data!.included[0].entity_type)}>查看待执行方案</Button>}
            {(result.skipped > 0 || result.failed > 0 || (preview.data?.excluded.length ?? 0) > 0) && onOpenEntityType && <Button onClick={() => onOpenEntityType(preview.data?.excluded[0]?.entity_type ?? preview.data!.included[0].entity_type)}>查看剩余人工项</Button>}
            <Button type="primary" onClick={onClose}>完成</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
