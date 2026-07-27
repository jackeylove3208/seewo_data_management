export type EntityType = "organization_unit" | "class" | "teacher" | "student";

export type DifferenceType = "missing" | "redundant" | "attribute" | "structure";

export type RiskLevel = "low" | "medium" | "high";

export interface DifferenceIssue {
  id: string;
  field: string;
  type: DifferenceType;
  sourceValue: string;
  targetValue: string;
  recommendation: string;
  risk: RiskLevel;
  selectable: boolean;
}

export interface DifferencePerson {
  id: string;
  entityType: EntityType;
  name: string;
  context: string;
  issues: DifferenceIssue[];
}

export type TaskStatus = "ready" | "processing" | "terminated" | "failed";

export interface TaskHistoryItem {
  id: string;
  title: string;
  createdAt: string;
  sourceFile: string;
  targetFile: string;
  sourceAccepted: number;
  targetAccepted: number;
  issueCount: number;
  status: TaskStatus;
  selectedEntityTypes: EntityType[];
  entityCounts?: Partial<Record<EntityType, { source: number; target: number }>>;
  isDemo?: boolean;
  workflowVersion?: string;
  taskKind?: "sync" | "rollback";
  reportId?: string | null;
  rollbackEligible?: boolean;
  deletionEligible?: boolean;
  operationSummary?: { succeeded: number; failed: number; blocked: number };
}

export interface EntitySummary {
  type: EntityType;
  label: string;
  sourceCount: number;
  targetCount: number;
  issueCount: number;
}
