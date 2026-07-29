export type EntityType = "organization_unit" | "class" | "teacher" | "student";

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
  workflowVersion?: string;
  taskKind?: "sync" | "rollback";
  reportId?: string | null;
  rollbackEligible?: boolean;
  deletionEligible?: boolean;
  operationSummary?: { succeeded: number; failed: number; blocked: number };
  targetSourceKey?: string;
  targetSourceName?: string;
  targetSourceKind?: "database" | "local" | "upload" | "unknown";
  targetSourceIdentified?: boolean;
}

export interface EntitySummary {
  type: EntityType;
  label: string;
  sourceCount: number;
  targetCount: number;
  issueCount: number;
}
