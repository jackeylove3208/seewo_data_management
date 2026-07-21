import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Input, Modal, Select, Spin, Tag } from "antd";
import { ArrowRight, CheckCircle2, ShieldAlert, Sparkles, UserRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { queryKeys } from "../../api/queryKeys";
import { ApiError } from "../../api/client";
import {
  reconciliationApi,
  type AIProposalRequest,
  type AutoExecutableResolution,
  type CauseAnalysisV3,
  type DifferenceItem,
  type GovernanceOption,
  type GovernanceProposalPreview,
  type ManualProposalRequest,
  type OperationType,
} from "../../api/reconciliation";
import { displayFieldValue, fieldLabel, operationLabels, riskColors, riskLabels } from "./localization";

type View = "analysis" | "manual" | "preview" | "success";

function executableOperation(value: OperationType): OperationType {
  return value === "manual_review" || value === "skip" ? "update" : value;
}

function AnalysisAnimation() {
  return (
    <div className="analysis-animation" data-testid="analysis-animation" role="status">
      <span className="analysis-animation-icon"><Sparkles size={24} /></span>
      <div><strong>AI 正在分析这条差异</strong><p>正在核对层级证据、字段变化和可执行风险。</p></div>
      <span className="analysis-dots" aria-hidden="true"><i /><i /><i /></span>
    </div>
  );
}

function ChangePreview({ preview }: { preview: GovernanceProposalPreview }) {
  return (
    <div className="proposal-preview">
      <div className="proposal-preview-heading">
        <div><span>方案修改预览</span><strong>{preview.proposal_source === "ai" ? "AI 方案" : "人工方案"}</strong></div>
        <Tag color={riskColors[preview.risk]}>{riskLabels[preview.risk]}</Tag>
      </div>
      <div className="proposal-change-list">
        {preview.changes.map((change) => (
          <div className="proposal-change" key={change.field}>
            <strong>{fieldLabel(change.field)}</strong>
            <span>{displayFieldValue(change.field, change.before)}</span>
            <ArrowRight size={15} />
            <span>{displayFieldValue(change.field, change.after)}</span>
          </div>
        ))}
      </div>
      <p className="proposal-rationale">{preview.rationale}</p>
      <Alert type="info" showIcon message="确认后仅生成待执行方案，不会直接修改希沃数据。" />
    </div>
  );
}

function OptionCard({ option, onPreview, loading, riskReason }: {
  option: GovernanceOption;
  onPreview: () => void;
  loading: boolean;
  riskReason?: string;
}) {
  return (
    <article className={option.recommended ? "analysis-option recommended" : "analysis-option"}>
      <header>
        <span>{option.recommended && <Tag color="success">推荐</Tag>}<strong>{option.rationale}</strong></span>
        <Tag color={riskColors[option.risk]}>{riskLabels[option.risk]}</Tag>
      </header>
      <div className="option-metrics"><span>置信度 {Math.round(option.confidence * 100)}%</span><span>{operationLabels[option.operation_type]}</span></div>
      {riskReason && <p>风险说明：{riskReason}</p>}
      {option.preconditions.length > 0 && <p>前置条件：{option.preconditions.join("；")}</p>}
      <Button loading={loading} onClick={onPreview}>采用并预览</Button>
    </article>
  );
}

function optionFromResolution(solution: AutoExecutableResolution): GovernanceOption {
  return {
    option_id: solution.solution_id,
    operation_type: solution.action.operation_type,
    target_entity_id: solution.action.target_entity_id,
    proposed_changes: solution.action.proposed_changes,
    rationale: solution.rationale,
    evidence_refs: solution.evidence_refs,
    risk: solution.risk,
    confidence: solution.confidence,
    preconditions: solution.preconditions,
    recommended: solution.recommended,
  };
}

function V3ResolutionList({ output, onPreview, loading }: {
  output: CauseAnalysisV3;
  onPreview: (option: GovernanceOption) => void;
  loading: boolean;
}) {
  return (
    <div className="analysis-options">
      {output.solutions.map((solution) => {
        if (solution.mode === "auto_executable") {
          const option = optionFromResolution(solution);
          return <OptionCard key={solution.solution_id} option={option} loading={loading} riskReason={solution.risk_reason} onPreview={() => onPreview(option)} />;
        }
        if (solution.mode === "needs_information") {
          return (
            <section className="analysis-resolution" key={solution.solution_id}>
              <Alert type="warning" showIcon message={solution.title} description={solution.rationale} />
              <p>风险说明：{solution.risk_reason}</p>
              <ul>{solution.information_requests.map((request) => <li key={`${request.request_type}-${request.question}`}><strong>{request.question}</strong><span>{request.reason}，建议查看：{request.source_hint}</span></li>)}</ul>
            </section>
          );
        }
        return (
          <section className="analysis-resolution" key={solution.solution_id}>
            <Alert type="warning" showIcon icon={<ShieldAlert size={17} />} message={solution.title} description={solution.rationale} />
            <p>风险说明：{solution.risk_reason}</p>
            <ol>{solution.manual_steps.map((step) => <li key={step.order}>{step.instruction}</li>)}</ol>
          </section>
        );
      })}
    </div>
  );
}

export function AnalysisModal({ open, difference, onClose, onProposalSaved }: {
  open: boolean;
  difference: DifferenceItem;
  onClose: () => void;
  onProposalSaved?: () => void;
}) {
  const queryClient = useQueryClient();
  const [view, setView] = useState<View>("analysis");
  const [preview, setPreview] = useState<GovernanceProposalPreview>();
  const [pendingAI, setPendingAI] = useState<AIProposalRequest>();
  const [pendingManual, setPendingManual] = useState<ManualProposalRequest>();
  const [manualValues, setManualValues] = useState<Record<string, string>>({});
  const [rationale, setRationale] = useState("");
  const [conflictMessage, setConflictMessage] = useState<string>();

  useEffect(() => {
    setView("analysis");
    setPreview(undefined);
    setPendingAI(undefined);
    setPendingManual(undefined);
    setManualValues({});
    setRationale("");
    setConflictMessage(undefined);
  }, [difference.id, open]);

  function handleProposalError(error: Error) {
    if (!(error instanceof ApiError) || error.status !== 409) return;
    setConflictMessage("数据版本已变化，请重新打开分析后确认。");
    setPreview(undefined);
    setPendingAI(undefined);
    setPendingManual(undefined);
    setView("analysis");
    void queryClient.invalidateQueries({ queryKey: ["differences", difference.task_id] });
    void queryClient.invalidateQueries({ queryKey: queryKeys.analysis(difference.id) });
  }

  const analysis = useQuery({
    queryKey: queryKeys.analysis(difference.id),
    queryFn: ({ signal }) => reconciliationApi.getAnalysis(difference.id, signal),
    enabled: open && difference.analysis_status !== "pending",
    staleTime: Number.POSITIVE_INFINITY,
  });
  const editorSchema = useQuery({
    queryKey: queryKeys.editorSchema(difference.entity_type),
    queryFn: ({ signal }) => reconciliationApi.getEditorSchema(difference.entity_type, signal),
    enabled: open && view === "manual",
  });

  useEffect(() => {
    if (!editorSchema.data) return;
    setManualValues((current) => {
      if (Object.keys(current).length > 0) return current;
      return Object.fromEntries(editorSchema.data.fields.map((field) => [
        field.name,
        displayFieldValue(field.name, difference.evidence.target_payload?.[field.name] ?? "") === "未设置"
          ? ""
          : String(difference.evidence.target_payload?.[field.name] ?? ""),
      ]));
    });
  }, [difference.evidence.target_payload, editorSchema.data]);

  const aiPreview = useMutation({
    mutationFn: (request: AIProposalRequest) => reconciliationApi.previewAIProposal(difference.id, request),
    onSuccess: (result, request) => {
      setPendingAI(request);
      setPendingManual(undefined);
      setPreview(result);
      setView("preview");
    },
    onError: handleProposalError,
  });
  const manualPreview = useMutation({
    mutationFn: (request: ManualProposalRequest) => reconciliationApi.previewManualProposal(difference.id, request),
    onSuccess: (result, request) => {
      setPendingManual(request);
      setPendingAI(undefined);
      setPreview(result);
      setView("preview");
    },
    onError: handleProposalError,
  });
  const confirm = useMutation({
    mutationFn: () => {
      if (pendingAI) return reconciliationApi.confirmAIProposal(difference.id, pendingAI);
      if (pendingManual) return reconciliationApi.confirmManualProposal(difference.id, pendingManual);
      throw new Error("没有可确认的治理方案");
    },
    onSuccess: () => {
      setView("success");
      void queryClient.invalidateQueries({ queryKey: ["differences", difference.task_id] });
      void queryClient.invalidateQueries({ queryKey: queryKeys.proposals(difference.id) });
      onProposalSaved?.();
    },
    onError: handleProposalError,
  });

  const manualChanges = useMemo(() => Object.fromEntries(
    Object.entries(manualValues).filter(([field, value]) => {
      const before = difference.evidence.target_payload?.[field];
      return value !== "" && value !== String(before ?? "");
    }),
  ), [difference.evidence.target_payload, manualValues]);

  function previewOption(option: GovernanceOption) {
    aiPreview.mutate({
      analysis_id: analysis.data!.id,
      option_id: option.option_id,
      expected_difference_version: difference.version,
    });
  }

  function previewManual() {
    manualPreview.mutate({
      expected_difference_version: difference.version,
      operation_type: executableOperation(difference.proposed_action),
      target_entity_id: difference.evidence.target_entity_id,
      changes: manualChanges,
      rationale,
    });
  }

  const error = aiPreview.error ?? manualPreview.error ?? confirm.error;
  const output = analysis.data?.output;
  const v3Output = output && "solutions" in output ? output : undefined;
  const v2Output = output && "options" in output ? output : undefined;

  return (
    <Modal
      className="analysis-modal"
      width={760}
      open={open}
      title="差异治理分析"
      footer={null}
      onCancel={onClose}
      destroyOnHidden
    >
      {difference.analysis_status === "pending" && <AnalysisAnimation />}

      {difference.analysis_status !== "pending" && analysis.isLoading && <div className="modal-loading"><Spin /><span>正在读取分析结果</span></div>}
      {analysis.isError && <Alert type="error" showIcon message="分析结果读取失败" description="请稍后重试，或转为人工修改。" />}

      {view === "analysis" && output && (
        <div className="analysis-result">
          <section className="analysis-explanation">
            <span className="analysis-result-icon"><Sparkles size={18} /></span>
            <div>
              <small>成因分析</small>
              <h3>{v3Output?.issue_title ?? v2Output?.cause}</h3>
              {v3Output && <p>{v3Output.cause_summary}</p>}
              <p>{output.evidence_summary}</p>
              {v3Output && <p>业务影响：{v3Output.business_impact}</p>}
            </div>
          </section>
          <div className="analysis-context">
            {difference.evidence.fields.map((field) => (
              <div key={field.field}><strong>{fieldLabel(field.field)}</strong><span>{displayFieldValue(field.field, field.source_value)}</span><ArrowRight size={14} /><span>{displayFieldValue(field.field, field.target_value)}</span></div>
            ))}
          </div>
          <div className="analysis-provenance">{analysis.data?.provenance.provider === "deterministic" ? "规则分析" : "企业模型分析"}</div>

          {v2Output?.manual_only ? (
            <Alert className="manual-only-alert" type="warning" showIcon icon={<ShieldAlert size={17} />} message="仅支持人工处理" description={v2Output.manual_reason} />
          ) : v2Output ? (
            <div className="analysis-options">
              {v2Output.options.map((option) => <OptionCard key={option.option_id} option={option} loading={aiPreview.isPending} onPreview={() => previewOption(option)} />)}
            </div>
          ) : v3Output ? <V3ResolutionList output={v3Output} loading={aiPreview.isPending} onPreview={previewOption} /> : null}
          <div className="modal-command-row">
            <Button icon={<UserRound size={15} />} onClick={() => setView("manual")}>人工修改</Button>
          </div>
        </div>
      )}

      {view === "analysis" && analysis.data && !output && (
        <div className="analysis-result">
          <Alert type="warning" showIcon message="AI 分析未生成可用结果" description="可以重试任务分析，或通过人工编辑器生成待执行方案。" />
          <div className="modal-command-row"><Button icon={<UserRound size={15} />} onClick={() => setView("manual")}>人工修改</Button></div>
        </div>
      )}

      {view === "manual" && (
        <div className="manual-editor">
          <header><UserRound size={18} /><div><h3>人工修改</h3><p>只开放后端字段策略允许的属性。</p></div></header>
          {editorSchema.isLoading && <div className="modal-loading"><Spin /><span>正在读取可编辑字段</span></div>}
          {editorSchema.data?.fields.map((field) => (
            <label className="manual-field" key={field.name}>
              <span>{fieldLabel(field.name)}</span>
              {field.field_type === "status" ? (
                <Select aria-label={fieldLabel(field.name)} value={manualValues[field.name]} options={[{ value: "active", label: "启用" }, { value: "inactive", label: "停用" }]} onChange={(value) => setManualValues((current) => ({ ...current, [field.name]: value }))} />
              ) : (
                <Input aria-label={fieldLabel(field.name)} type={field.field_type === "email" ? "email" : "text"} value={manualValues[field.name] ?? ""} onChange={(event) => setManualValues((current) => ({ ...current, [field.name]: event.target.value }))} />
              )}
            </label>
          ))}
          <label className="manual-field manual-rationale"><span>修改原因</span><Input.TextArea aria-label="修改原因" rows={3} value={rationale} onChange={(event) => setRationale(event.target.value)} /></label>
          <div className="modal-command-row">
            <Button onClick={() => setView("analysis")}>返回分析</Button>
            <Button type="primary" loading={manualPreview.isPending} disabled={Object.keys(manualChanges).length === 0 || rationale.trim().length < 3} onClick={previewManual}>预览人工方案</Button>
          </div>
        </div>
      )}

      {view === "preview" && preview && (
        <div>
          <ChangePreview preview={preview} />
          <div className="modal-command-row">
            <Button onClick={() => setView(pendingManual ? "manual" : "analysis")}>返回修改</Button>
            <Button type="primary" loading={confirm.isPending} onClick={() => confirm.mutate()}>确认生成待执行方案</Button>
          </div>
        </div>
      )}

      {view === "success" && (
        <div className="proposal-success">
          <CheckCircle2 size={34} />
          <h3>已进入待治理执行</h3>
          <p>后续治理执行会读取这份确定版本的方案，完成预检、审核和写入。</p>
          <Button type="primary" onClick={onClose}>完成</Button>
        </div>
      )}
      {(conflictMessage || error) && <Alert className="modal-error" type="error" showIcon message={conflictMessage ?? "请求未完成，请稍后重试。"} />}
    </Modal>
  );
}
