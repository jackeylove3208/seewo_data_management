import { Alert, Button, Progress, Tag } from "antd";
import { RotateCcw, UserRound, X } from "lucide-react";

import type { MatchingQualityResult, RematchingJobProgress } from "../../api/reconciliation";

const labels: Record<string, string> = {
  organization_unit: "组织单位",
  class: "班级",
  teacher: "教师",
  student: "学生",
  membership: "成员关系",
};

interface Props {
  progress: RematchingJobProgress | null;
  quality: MatchingQualityResult | null;
  loadFailed?: boolean;
  onReload?: () => void;
  onRetry?: () => void;
  onCancel?: () => void;
  onManualMapping?: () => void;
}

export function MatchingRecoveryPanel({ progress, quality, loadFailed, onReload, onRetry, onCancel, onManualMapping }: Props) {
  if (loadFailed) {
    return <Alert type="error" showIcon message="匹配进度读取失败" description="正在通过轮询重新连接。" action={<Button size="small" onClick={onReload}>重新读取</Button>} />;
  }
  if (!progress && quality?.passed) {
    return <Alert type="success" showIcon message="无需 AI 二次匹配" description="首次匹配已覆盖全部实体，已直接进入质量评估。" />;
  }
  if (!progress && !quality) return null;
  const total = progress?.indexed ?? 0;
  const percent = total ? Math.round(((progress?.processed ?? 0) / total) * 100) : 0;
  const unresolved = progress ? progress.manual_review + progress.conflict + progress.no_match : 0;
  const updatedAt = progress?.updated_at ? new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Shanghai" }).format(new Date(progress.updated_at)) : null;
  if (quality && !quality.passed) {
    const types = [...new Set(quality.failures.flatMap((failure) => failure.affected_entity_types))].map((type) => labels[type] ?? type).join("、");
    return <Alert type="warning" showIcon message="匹配质量未通过，差异检测已暂停" description={<div><p>{types}</p>{quality.failures.map((failure) => <p key={failure.reason}>{failure.reason}（<span>{`实际值 ${formatRatio(failure.observed_value)}`}</span>，<span>{`阈值 ${formatRatio(failure.threshold)}`}</span>）</p>)}<p>当前仅更新匹配判断，不会修改三方系统、希沃或 CSV 数据。</p></div>} action={<><Button icon={<RotateCcw size={14} />} onClick={onRetry}>重试匹配</Button><Button icon={<UserRound size={14} />} onClick={onManualMapping}>人工确认映射</Button></>} />;
  }
  return <section className="matching-recovery-panel" aria-label="实体匹配恢复">
    <div className="section-title-row"><div><h2>{progress?.status === "completed" ? "实体匹配已完成" : "实体匹配恢复中"}</h2><p>仅处理首次匹配未确认的实体，结果可追溯且不会写入外部系统。</p></div>{progress?.status === "running" && <Button icon={<X size={14} />} onClick={onCancel}>取消恢复</Button>}</div>
    <Progress percent={percent} />
    <div className="matching-recovery-stages" aria-label="实体匹配子阶段"><Tag color={progress?.status === "indexing" ? "processing" : undefined}>首次匹配</Tag><Tag color={progress?.status === "indexing" ? "processing" : undefined}>向量索引</Tag><Tag color={progress?.status === "running" ? "processing" : undefined}>AI 恢复</Tag><Tag color={progress?.status === "assigning" ? "processing" : undefined}>全局分配</Tag><Tag color={progress?.status === "evaluating_quality" ? "processing" : undefined}>质量评估</Tag></div>
    <div className="matching-recovery-counts"><span>首次未匹配 <strong>{progress?.initial_unresolved ?? 0}</strong></span><span>AI 已恢复 <strong>{progress?.ai_recovered ?? 0}</strong></span><span>剩余人工 <strong>{unresolved}</strong></span><span>冲突 <strong>{progress?.conflict ?? 0}</strong></span><span>失败 <strong>{progress?.failed ?? 0}</strong></span>{progress?.no_match ? <span>未找到候选 <strong>{progress.no_match}</strong></span> : null}</div>
    {updatedAt && <p className="matching-recovery-updated">最近更新 {updatedAt}</p>}
    {quality?.passed && <Alert type="success" message="质量门禁已通过" description="可以继续进入差异检测。" />}
  </section>;
}

function formatRatio(value: number) { return value <= 1 ? `${(value * 100).toFixed(1)}%` : String(value); }
