import type { EntityType } from "../../types/domain";
import type { AssistantResponse, TaskCreationAssistant, TaskIntentDraft } from "./types";

const allEntityTypes: EntityType[] = ["organization_unit", "class", "teacher", "student"];

const entityKeywords: Array<{ type: EntityType; words: string[] }> = [
  { type: "organization_unit", words: ["部门", "组织"] },
  { type: "class", words: ["班级", "班"] },
  { type: "teacher", words: ["教师", "老师"] },
  { type: "student", words: ["学生"] },
];

const scopePatterns = [
  /([一二三四五六七八九]年级)/,
  /(小学部|初中部|高中部)/,
  /(全校)/,
];

export function createInitialDraft(): TaskIntentDraft {
  return {
    title: "全校组织数据核对",
    scopeLabel: "全校",
    snapshotMode: "full",
    entityTypes: [...allEntityTypes],
  };
}

export function createEmptyTaskIntentDraft(): TaskIntentDraft {
  return {
    title: "",
    scopeLabel: "",
    snapshotMode: "full",
    entityTypes: [],
  };
}

function entityTypesFrom(message: string) {
  return entityKeywords
    .filter(({ words }) => words.some((word) => message.includes(word)))
    .map(({ type }) => type);
}

function scopeFrom(message: string) {
  return scopePatterns.map((pattern) => message.match(pattern)?.[1]).find(Boolean);
}

function titleFor(scope: string, entityTypes: EntityType[]) {
  const names: Record<EntityType, string> = {
    organization_unit: "部门",
    class: "班级",
    teacher: "教师",
    student: "学生",
  };
  const entityLabel = entityTypes.length === allEntityTypes.length
    ? "组织数据"
    : entityTypes.map((type) => names[type]).join("、");
  return `${scope}${entityLabel}核对`;
}

function respond(request: { draft: TaskIntentDraft; message: string }): Promise<AssistantResponse> {
  const message = request.message.trim();
  if (/(直接|立即).*(修复|执行|删除|回退)|回退.*(操作|任务|数据)/.test(message)) {
    return Promise.resolve({
      kind: "guardrail",
      message: "我不能直接执行治理或回退。先创建对账任务，之后请在问题审核和回退确认页面完成操作。",
      patch: {},
    });
  }

  const recognizedTypes = entityTypesFrom(message);
  const scope = scopeFrom(message) ?? request.draft.scopeLabel;
  const entityTypes = recognizedTypes.length > 0 ? recognizedTypes : request.draft.entityTypes;
  const snapshotMode = scope === "全校" && !/(只|部分|指定)/.test(message) ? "full" : "partial";
  const patch = {
    scopeLabel: scope,
    entityTypes,
    snapshotMode,
    title: titleFor(scope, entityTypes),
  } satisfies AssistantResponse["patch"];
  const typesLabel = entityTypes.length === allEntityTypes.length ? "全部实体" : titleFor("", entityTypes).replace("核对", "");
  const nextDraft = { ...request.draft, ...patch };
  const missingFields = [
    !nextDraft.scopeLabel.trim() ? "核对范围" : undefined,
    nextDraft.entityTypes.length === 0 ? "实体类型" : undefined,
  ].filter((field): field is string => Boolean(field));

  return Promise.resolve({
    kind: "normal",
    message: missingFields.length > 0
      ? `已记录同步需求，还需要补充${missingFields.join("和")}。`
      : `已记录${scope}的${typesLabel}同步需求。`,
    patch,
  });
}

export const deterministicTaskAssistant: TaskCreationAssistant = { respond };
