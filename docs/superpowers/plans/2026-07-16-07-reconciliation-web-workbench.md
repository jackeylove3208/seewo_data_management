# Reconciliation Web Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a complete operational Web workbench for paired CSV tasks, difference/analysis review, stable batch selection, execution monitoring, immutable history, on-demand reports, and compensation rollback.

**Architecture:** Build a quiet, table-centered React application with route-level feature boundaries and server state owned by TanStack Query. Generate TypeScript contracts from FastAPI OpenAPI; the browser sends selections and commands but never computes authoritative risk, operator identity, execution eligibility, or rollback permission. Long-running stages use persisted backend progress over SSE with cursor reconnect and polling fallback; Celery/Redis are introduced only after the synchronous seven-module flow passes end-to-end.

**Tech Stack:** React 19, TypeScript 5, Vite, React Router, TanStack Query, Ant Design, Lucide React, Vitest, Testing Library, MSW, Playwright; backend Celery, Redis, FastAPI SSE.

## Global Constraints

- The first screen is the working dashboard, not a marketing landing page.
- Use task lists, tables, split detail surfaces, status indicators, dialogs, and full-width page bands; do not nest cards.
- The user uploads exactly one authoritative third-party CSV and one Seewo target CSV with explicit labels.
- Backend state is authoritative for eligibility, risk, operator, report actions, rollback actions, and progress.
- A difference checkbox is disabled until current-version analysis succeeds.
- Selection is keyed by `(difference_id, difference_version)` and remains stable across filtering and pagination.
- Batch confirmation displays exact create/update/move/disable/skip/manual-review and high-risk counts returned by backend.
- A changed validated plan requires a fresh confirmation.
- Execution detail separates success, failure, verification failure, blocked, and retryable operations.
- Report generation is optional from an execution record; rollback always requires visible preflight review.
- SSE reconnects from the last event ID and falls back to polling without a full page reload.
- Responsive views must not overlap or truncate controls at desktop and mobile widths.

---

## File Map

- `frontend/src/app/`: router, providers, application layout, route error boundary.
- `frontend/src/api/`: generated OpenAPI types, base client, endpoint modules, query keys.
- `frontend/src/features/`: dashboard, task creation/detail, difference workbench, batch/execution, history/detail, report, rollback.
- `frontend/src/components/`: shared status, table, entity-diff, and feedback primitives.
- `frontend/tests/unit/` and `frontend/tests/e2e/`: behavior and complete-chain tests.
- `backend/app/workers/` and `backend/app/api/routes/progress.py`: post-synchronous background wrappers and persisted SSE.

### Task 1: Bootstrap the frontend shell and quality gates

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/app/providers.tsx`
- Create: `frontend/src/app/router.tsx`
- Create: `frontend/src/app/layout/AppLayout.tsx`
- Create: `frontend/src/styles/index.css`
- Test: `frontend/tests/unit/app/AppLayout.test.tsx`

**Interfaces:**
- Consumes: `VITE_API_BASE_URL`.
- Produces: route shell for `/`, `/tasks/new`, `/tasks/:taskId`, `/tasks/:taskId/differences`, `/executions/:executionId`, `/reports/:reportId`, and `/executions/:executionId/rollback`.

- [ ] **Step 1: Write the failing shell test**

```tsx
import { render, screen } from '@testing-library/react';
import { AppProviders } from '@/app/providers';
import { AppLayout } from '@/app/layout/AppLayout';

it('renders operational navigation', () => {
  render(<AppProviders><AppLayout /></AppProviders>);
  expect(screen.getByRole('link', { name: '对账任务' })).toBeVisible();
  expect(screen.getByRole('link', { name: '执行记录' })).toBeVisible();
});
```

- [ ] **Step 2: Install and run the focused test**

Run: `cd frontend && npm install && npm test -- AppLayout.test.tsx`

Expected: FAIL because the application shell does not exist.

- [ ] **Step 3: Add exact scripts and dependencies**

```json
{
  "name": "organization-reconciliation-web",
  "private": true,
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "test:watch": "vitest",
    "lint": "eslint . --max-warnings=0",
    "typecheck": "tsc -b --pretty false",
    "openapi": "openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.d.ts",
    "e2e": "playwright test"
  },
  "dependencies": {
    "@ant-design/icons": "^6.0.0", "@tanstack/react-query": "^5.0.0", "antd": "^6.0.0",
    "lucide-react": "^0.468.0", "react": "^19.0.0", "react-dom": "^19.0.0", "react-router": "^7.0.0"
  },
  "devDependencies": {
    "@playwright/test": "^1.50.0", "@testing-library/jest-dom": "^6.0.0",
    "@testing-library/react": "^16.0.0", "@testing-library/user-event": "^14.0.0",
    "@types/react": "^19.0.0", "@types/react-dom": "^19.0.0", "@vitejs/plugin-react": "^4.0.0",
    "eslint": "^9.0.0", "jsdom": "^26.0.0", "msw": "^2.0.0", "openapi-typescript": "^7.0.0",
    "typescript": "^5.7.0", "vite": "^6.0.0", "vitest": "^3.0.0"
  }
}
```

- [ ] **Step 4: Implement providers and restrained layout**

```tsx
export function AppProviders({ children }: PropsWithChildren) {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: { queries: { staleTime: 10_000, retry: 1, refetchOnWindowFocus: false } },
  }));
  return <ConfigProvider theme={{ token: { borderRadius: 6, colorPrimary: '#176b5b' } }}>
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  </ConfigProvider>;
}

export function AppLayout() {
  return <Layout className="app-shell">
    <Header className="app-header"><Link to="/" className="brand">组织数据治理</Link>
      <nav><Link to="/">对账任务</Link><Link to="/executions">执行记录</Link></nav>
    </Header>
    <Content className="app-content"><Outlet /></Content>
  </Layout>;
}
```

```css
:root { font-family: Inter, "PingFang SC", "Microsoft YaHei", sans-serif; color: #18201f; background: #f5f7f6; }
* { box-sizing: border-box; }
.app-shell { min-height: 100vh; }
.app-header { display: flex; align-items: center; gap: 32px; height: 56px; padding: 0 24px; background: #fff; border-bottom: 1px solid #dfe5e3; }
.app-header nav { display: flex; gap: 20px; }
.app-content { width: min(1440px, 100%); margin: 0 auto; padding: 20px 24px 40px; }
@media (max-width: 640px) { .app-header { padding: 0 12px; gap: 16px; } .app-content { padding: 12px; } }
```

- [ ] **Step 5: Verify and commit**

Run: `cd frontend && npm test -- AppLayout.test.tsx && npm run typecheck && npm run build`

Expected: shell test PASS and production build succeeds.

```bash
git add frontend
git commit -m "feat: bootstrap reconciliation web shell"
```

### Task 2: Generate typed API clients and shared server-state behavior

**Files:**
- Create: `frontend/src/api/schema.d.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/queryKeys.ts`
- Create: `frontend/src/api/uploads.ts`
- Create: `frontend/src/api/fieldMappings.ts`
- Create: `frontend/src/api/tasks.ts`
- Create: `frontend/src/api/differences.ts`
- Create: `frontend/src/api/executions.ts`
- Create: `frontend/src/api/reports.ts`
- Create: `frontend/src/api/rollbacks.ts`
- Test: `frontend/tests/unit/api/client.test.ts`

**Interfaces:**
- Consumes: FastAPI OpenAPI, browser `fetch`, multipart uploads, idempotency keys.
- Produces: typed endpoint functions and normalized `ApiProblem` errors used by all features.

- [ ] **Step 1: Write error and idempotency tests**

```ts
it('maps field validation errors without losing field paths', async () => {
  server.use(http.post('/api/reconciliation-tasks', () => HttpResponse.json({
    title: 'Validation failed', errors: [{ field: 'target_upload_id', message: 'required' }],
  }, { status: 422 })));
  await expect(createTask(validTaskRequest(), 'task-key')).rejects.toMatchObject({
    status: 422, errors: [{ field: 'target_upload_id', message: 'required' }],
  });
});
```

- [ ] **Step 2: Generate contracts and run the test**

Run: `cd backend && uv run uvicorn app.main:app --port 8000` in one terminal, then `cd frontend && npm run openapi && npm test -- client.test.ts`.

Expected: generated types succeed; test FAIL because base client is absent.

- [ ] **Step 3: Implement one typed request primitive**

```ts
export class ApiProblem extends Error {
  constructor(public status: number, public body: { title?: string; errors?: Array<{ field: string; message: string }> }) {
    super(body.title ?? `Request failed (${status})`);
  }
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL ?? ''}${path}`, {
    ...init, headers: { Accept: 'application/json', ...init.headers },
  });
  if (!response.ok) throw new ApiProblem(response.status, await response.json());
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}
```

- [ ] **Step 4: Add endpoint wrappers with server keys**

```ts
export const queryKeys = {
  tasks: (filters: object) => ['tasks', filters] as const,
  task: (id: string) => ['task', id] as const,
  differences: (taskId: string, filters: object) => ['differences', taskId, filters] as const,
  execution: (id: string) => ['execution', id] as const,
};

export function createTask(body: CreateTaskRequest, key = crypto.randomUUID()) {
  return apiRequest<TaskResponse>('/api/reconciliation-tasks', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key }, body: JSON.stringify(body),
  });
}

export function listFieldMappings() {
  return apiRequest<FieldMappingSummary[]>('/api/field-mappings');
}

export function previewFieldMapping(uploadId: string, mappingVersion: string) {
  return apiRequest<FieldMappingPreviewResponse>(`/api/uploads/${uploadId}/mapping-preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mapping_version: mappingVersion }),
  });
}
```

- [ ] **Step 5: Verify and commit**

Run: `cd frontend && npm test -- client.test.ts && npm run typecheck`

Expected: success, empty response, validation, network error, multipart, and idempotency tests PASS.

```bash
git add frontend/src/api frontend/tests/unit/api
git commit -m "feat: add typed reconciliation api clients"
```

### Task 3: Build dashboard, task list, and paired CSV creation

**Files:**
- Create: `frontend/src/features/dashboard/DashboardPage.tsx`
- Create: `frontend/src/features/task-create/TaskCreatePage.tsx`
- Create: `frontend/src/features/task-create/PairedUploadForm.tsx`
- Create: `frontend/src/features/task-create/FieldMappingPreview.tsx`
- Create: `frontend/src/components/feedback/QueryState.tsx`
- Modify: `frontend/src/app/router.tsx`
- Test: `frontend/tests/unit/features/TaskCreatePage.test.tsx`
- Test: `frontend/tests/unit/features/DashboardPage.test.tsx`

**Interfaces:**
- Consumes: task list, upload, and task creation clients.
- Produces: `/` task table and `/tasks/new` paired upload form including tenant, scope, full/partial mode, entity coverage, two reusable field-mapping profiles, sample-row preview, and validation summary.

- [ ] **Step 1: Write loading/error/form tests**

```tsx
it('does not submit until both source roles are present', async () => {
  renderRoute('/tasks/new');
  await userEvent.upload(screen.getByLabelText('第三方权威数据 CSV'), csvFile('source.csv'));
  await userEvent.click(screen.getByRole('button', { name: '创建对账任务' }));
  expect(await screen.findByText('请选择希沃目标数据 CSV')).toBeVisible();
  expect(createTaskSpy).not.toHaveBeenCalled();
});

it('offers retry after task list failure', async () => {
  server.use(http.get('/api/reconciliation-tasks', () => HttpResponse.error()));
  renderRoute('/');
  expect(await screen.findByRole('button', { name: '重试' })).toBeVisible();
});
```

- [ ] **Step 2: Run page tests**

Run: `cd frontend && npm test -- TaskCreatePage.test.tsx DashboardPage.test.tsx`

Expected: FAIL because pages do not exist.

- [ ] **Step 3: Implement paired upload with source-role labels**

```tsx
export function PairedUploadForm() {
  const [form] = Form.useForm<TaskFormValues>();
  const authoritativeUpload = Form.useWatch('authoritative', form);
  const targetUpload = Form.useWatch('target', form);
  const navigate = useNavigate();
  const submit = async (values: TaskFormValues) => {
    const task = await createTask({
      authoritative_upload_id: values.authoritative.id, target_upload_id: values.target.id,
      tenant_id: values.tenantId, scope_id: values.scopeId,
      snapshot_mode: values.snapshotMode, entity_types: values.entityTypes,
      schema_version: 'canonical-v1',
      authoritative_mapping_version: values.authoritativeMappingVersion,
      target_mapping_version: values.targetMappingVersion,
    });
    navigate(`/tasks/${task.id}`);
  };
  return <Form form={form} layout="vertical" onFinish={submit}>
    <UploadField name="authoritative" label="第三方权威数据 CSV" required customRequest={uploadCsvImmediately} />
    <Form.Item name="authoritativeMappingVersion" label="第三方字段映射" rules={[{ required: true }]}>
      <FieldMappingPreview role="authoritative" upload={authoritativeUpload} />
    </Form.Item>
    <UploadField name="target" label="希沃目标数据 CSV" required customRequest={uploadCsvImmediately} />
    <Form.Item name="targetMappingVersion" label="希沃字段映射" rules={[{ required: true }]}>
      <FieldMappingPreview role="target" upload={targetUpload} />
    </Form.Item>
    <Form.Item name="snapshotMode" label="快照范围" initialValue="full"><Segmented options={[{ label: '完整范围', value: 'full' }, { label: '部分范围', value: 'partial' }]} /></Form.Item>
    <Button type="primary" htmlType="submit">创建对账任务</Button>
  </Form>;
}
```

- [ ] **Step 4: Implement task table states**

```tsx
export function FieldMappingPreview({ role, upload, value, onChange }: {
  role: SourceRole; upload?: StoredUpload; value?: string; onChange?: (version: string) => void;
}) {
  const profiles = useQuery({ queryKey: ['field-mappings'], queryFn: listFieldMappings });
  const preview = useQuery({
    queryKey: ['mapping-preview', upload?.id, value],
    queryFn: () => previewFieldMapping(upload!.id, value!),
    enabled: Boolean(upload?.id && value),
  });
  return <section aria-label={`${role} 字段映射`}>
    <Select aria-label="字段映射配置" value={value} onChange={onChange}
      options={(profiles.data ?? []).filter(item => item.source_role === role).map(item => ({ label: item.name, value: item.version }))} />
    {preview.data && <><Descriptions size="small" items={preview.data.columns.map(column => ({
      key: column.canonical_field, label: column.source_column, children: column.canonical_field,
    }))} /><Table size="small" pagination={false} dataSource={preview.data.sample_rows} /></>}
  </section>;
}

const columns: ColumnsType<TaskSummary> = [
  { title: '任务', dataIndex: 'id', render: id => <Link to={`/tasks/${id}`}>{shortId(id)}</Link> },
  { title: '范围', dataIndex: 'scope_id' },
  { title: '阶段', dataIndex: 'stage', render: value => <StageStatus value={value} /> },
  { title: '状态', dataIndex: 'status', render: value => <StatusBadge value={value} /> },
  { title: '创建时间', dataIndex: 'created_at', render: formatDateTime },
];
```

- [ ] **Step 5: Verify and commit**

Run: `cd frontend && npm test -- TaskCreatePage.test.tsx DashboardPage.test.tsx && npm run typecheck`

Expected: loading, empty, error/retry, both-file validation, server field errors, upload progress, partial warning, and navigation tests PASS.

```bash
git add frontend/src/features/dashboard frontend/src/features/task-create frontend/src/components/feedback frontend/src/app/router.tsx frontend/tests/unit/features
git commit -m "feat: create and list reconciliation tasks"
```

### Task 4: Add persisted progress, SSE reconnect, and polling fallback

**Files:**
- Create: `backend/app/models/progress.py`
- Create: `backend/app/repositories/progress.py`
- Create: `backend/app/api/routes/progress.py`
- Create: `backend/app/workers/celery_app.py`
- Create: `backend/app/workers/reconciliation.py`
- Create: `backend/app/workers/analysis.py`
- Create: `backend/app/workers/execution.py`
- Create: `backend/app/workers/reporting.py`
- Modify: `backend/pyproject.toml`
- Modify: `infra/docker-compose.yml`
- Create: `frontend/src/hooks/useTaskProgress.ts`
- Create: `frontend/src/features/task-detail/TaskDetailPage.tsx`
- Test: `backend/tests/integration/api/test_progress.py`
- Test: `frontend/tests/unit/features/TaskDetailPage.test.tsx`

**Interfaces:**
- Consumes: synchronous service calls from modules 1-6 and persisted stage events.
- Produces: idempotent Celery wrappers, `GET /api/reconciliation-tasks/{id}/events`, reconnect cursors, polling fallback, stage timeline.

- [ ] **Step 1: Prove synchronous E2E before adding workers**

Run: `cd backend && uv run pytest tests/e2e -q`

Expected: synchronous upload-through-rollback tests PASS. Do not continue if they fail.

- [ ] **Step 2: Write progress persistence/reconnect tests**

```python
async def test_sse_replays_only_events_after_cursor(client, progress_repo, task_id) -> None:
    first = await progress_repo.append(task_id, stage="ingestion", progress=20)
    second = await progress_repo.append(task_id, stage="matching", progress=50)
    response = await client.get(f"/api/reconciliation-tasks/{task_id}/events", headers={"Last-Event-ID": str(first.sequence)})
    assert f"id: {second.sequence}" in response.text
    assert f"id: {first.sequence}" not in response.text
```

- [ ] **Step 3: Persist progress and wrap existing services idempotently**

Run: `cd backend && uv add 'celery>=5.5,<6' 'redis>=6,<7'`

Expected: Celery and Redis are added only after the synchronous end-to-end suite has passed.

```python
@celery_app.task(bind=True, autoretry_for=(TransientWorkflowError,), retry_backoff=True, max_retries=3)
def reconcile_task(self, task_id: str) -> None:
    run_async(reconciliation_workflow.run(UUID(task_id), worker_run_id=self.request.id))

@celery_app.task(bind=True, autoretry_for=(TransientWorkflowError,), retry_backoff=True, max_retries=3)
def analyze_task(self, task_id: str) -> None:
    run_async(analysis_service.analyze_pending_for_task(UUID(task_id), worker_run_id=self.request.id))

@celery_app.task(bind=True, autoretry_for=(TransientWorkflowError,), retry_backoff=True, max_retries=3)
def execute_batch(self, batch_id: str) -> None:
    run_async(execution_service.execute(UUID(batch_id), worker_run_id=self.request.id))

@celery_app.task(bind=True, autoretry_for=(TransientWorkflowError,), retry_backoff=True, max_retries=3)
def generate_report(self, report_job_id: str) -> None:
    run_async(report_service.run(UUID(report_job_id), worker_run_id=self.request.id))

@celery_app.task(bind=True, autoretry_for=(TransientWorkflowError,), retry_backoff=True, max_retries=3)
def execute_rollback(self, rollback_job_id: str) -> None:
    run_async(rollback_service.run(UUID(rollback_job_id), worker_run_id=self.request.id))

async def publish_stage(task_id: UUID, stage: str, progress: int, status: str) -> ProgressEvent:
    return await progress_repo.append_idempotent(task_id, stage, progress, status)
```

Add Redis to Compose and Celery dependencies only in this task. PostgreSQL progress events remain the source of truth; Celery state is never returned to the UI.

- [ ] **Step 4: Implement EventSource with polling fallback**

```ts
export function useTaskProgress(taskId: string) {
  const queryClient = useQueryClient();
  const [transport, setTransport] = useState<'sse' | 'polling'>('sse');
  useEffect(() => {
    const source = new EventSource(`/api/reconciliation-tasks/${taskId}/events`);
    source.onmessage = event => queryClient.setQueryData(queryKeys.task(taskId), JSON.parse(event.data));
    source.onerror = () => { source.close(); setTransport('polling'); };
    return () => source.close();
  }, [queryClient, taskId]);
  useQuery({ queryKey: queryKeys.task(taskId), queryFn: () => getTask(taskId),
             refetchInterval: transport === 'polling' ? 3000 : false });
  return { transport };
}
```

- [ ] **Step 5: Render fixed stage timeline and failure detail**

```tsx
const stages = ['数据接入', '生成快照', '标准化', '实体匹配', '差异检测', '成因分析'];
return <main><PageHeader title={`任务 ${shortId(task.id)}`} status={<StatusBadge value={task.status} />} />
  <Steps current={task.stage_index} status={task.status === 'failed' ? 'error' : 'process'} items={stages.map(title => ({ title }))} />
  {task.error && <Alert type="error" message={task.error.title} description={task.error.detail} action={<Button onClick={retry}>重试</Button>} />}
  <IngestionSummary counts={task.ingestion_summary} quarantineDownload={task.quarantine_download_url} />
</main>;
```

- [ ] **Step 6: Verify and commit**

Run: `cd backend && uv run pytest tests/integration/api/test_progress.py -q && cd ../frontend && npm test -- TaskDetailPage.test.tsx`

Expected: persisted ordering, reconnect, worker retry idempotency, SSE updates, polling fallback, error/retry, and cleanup tests PASS.

```bash
git add backend/app/models/progress.py backend/app/repositories/progress.py backend/app/api/routes/progress.py backend/app/workers infra/docker-compose.yml frontend/src/hooks frontend/src/features/task-detail frontend/tests/unit/features/TaskDetailPage.test.tsx
git commit -m "feat: stream persisted reconciliation progress"
```

### Task 5: Build the difference workbench and stable selection

**Files:**
- Create: `frontend/src/features/difference-workbench/DifferenceWorkbenchPage.tsx`
- Create: `frontend/src/features/difference-workbench/DifferenceTable.tsx`
- Create: `frontend/src/features/difference-workbench/DifferenceDetailDrawer.tsx`
- Create: `frontend/src/features/difference-workbench/useDifferenceSelection.ts`
- Create: `frontend/src/components/entity-diff/EntityDiff.tsx`
- Test: `frontend/tests/unit/features/DifferenceWorkbench.test.tsx`

**Interfaces:**
- Consumes: difference list/detail with analysis state, evidence, cause, recommendation, risk, confidence, eligibility.
- Produces: filterable paginated table, side-by-side detail, analysis trigger/progress, stable selected version refs.

- [ ] **Step 1: Write analysis gate and stable selection tests**

```tsx
it('disables selection while required analysis is pending', async () => {
  renderRoute(`/tasks/${TASK_ID}/differences`);
  const row = await screen.findByText('张三');
  expect(within(row.closest('tr')!).getByRole('checkbox')).toBeDisabled();
  expect(within(row.closest('tr')!).getByText('分析中')).toBeVisible();
});

it('keeps exact version selection after filtering and paging', async () => {
  renderRoute(`/tasks/${TASK_ID}/differences`);
  await userEvent.click((await screen.findAllByRole('checkbox'))[1]);
  await userEvent.click(screen.getByRole('tab', { name: '结构冲突' }));
  expect(screen.getByText('已选择 1 项')).toBeVisible();
});
```

- [ ] **Step 2: Run workbench tests**

Run: `cd frontend && npm test -- DifferenceWorkbench.test.tsx`

Expected: FAIL because workbench is missing.

- [ ] **Step 3: Implement version-keyed selection**

```ts
type DifferenceRef = { id: string; version: number };
const keyOf = (ref: DifferenceRef) => `${ref.id}:${ref.version}`;

export function useDifferenceSelection() {
  const [selected, setSelected] = useState(new Map<string, DifferenceRef>());
  const toggle = (ref: DifferenceRef) => setSelected(current => {
    const next = new Map(current); const key = keyOf(ref);
    next.has(key) ? next.delete(key) : next.set(key, ref); return next;
  });
  const clearStale = (currentVersions: Record<string, number>) => setSelected(current =>
    new Map([...current].filter(([, ref]) => currentVersions[ref.id] === ref.version)));
  return { selected: [...selected.values()], toggle, clearStale };
}
```

- [ ] **Step 4: Implement dense table and evidence detail**

```tsx
<Table rowKey={row => `${row.id}:${row.version}`} pagination={false} columns={[
  { title: '实体', dataIndex: 'entity_type' },
  { title: '差异类型', dataIndex: 'difference_type', filters: differenceTypeFilters },
  { title: '第三方值', dataIndex: ['summary', 'source'] },
  { title: '希沃值', dataIndex: ['summary', 'target'] },
  { title: '成因', dataIndex: ['analysis', 'cause'] },
  { title: '建议', dataIndex: ['analysis', 'recommended_action'] },
  { title: '风险', dataIndex: ['analysis', 'risk'], render: value => <RiskBadge value={value} /> },
  { title: '置信度', dataIndex: ['analysis', 'confidence'], render: value => value == null ? '-' : `${Math.round(value * 100)}%` },
]} rowSelection={{ selectedRowKeys, getCheckboxProps: row => ({ disabled: !row.execution_eligible }) }} />
```

Detail drawer renders `EntityDiff` side-by-side fields, highlighted changed fields, organization context, raw row refs, match method/evidence, persisted analysis, model/Skill provenance, and no mutation action.

- [ ] **Step 5: Verify and commit**

Run: `cd frontend && npm test -- DifferenceWorkbench.test.tsx && npm run typecheck`

Expected: loading/error/empty, filters, pagination, detail evidence, pending/failed analysis, disabled checkbox, stable selection, stale-version removal, and analysis rendering tests PASS.

```bash
git add frontend/src/features/difference-workbench frontend/src/components/entity-diff frontend/tests/unit/features/DifferenceWorkbench.test.tsx
git commit -m "feat: review and select analyzed differences"
```

### Task 6: Build batch confirmation and execution monitoring

**Files:**
- Create: `frontend/src/features/batch-confirmation/BatchConfirmationPage.tsx`
- Create: `frontend/src/features/execution-monitor/ExecutionMonitorPage.tsx`
- Create: `frontend/src/components/tables/OperationTable.tsx`
- Test: `frontend/tests/unit/features/BatchExecution.test.tsx`

**Interfaces:**
- Consumes: selected version refs, backend preview/preflight, confirmation, batch detail/progress, retry eligibility, target-version download.
- Produces: exact scope review, high-risk confirmation, fresh-plan handling, per-operation monitor, eligible retry actions.

- [ ] **Step 1: Write counts, stale plan, and partial failure tests**

```tsx
it('shows server operation counts before confirmation', async () => {
  renderRoute('/tasks/task-1/batch-confirmation', { state: selectedRefs });
  expect(await screen.findByText('新增 2')).toBeVisible();
  expect(screen.getByText('移动 1')).toBeVisible();
  expect(screen.getByText('高风险 1')).toBeVisible();
});

it('requires a fresh confirmation after plan changes', async () => {
  server.use(http.post('/api/execution-batches', () => HttpResponse.json({ code: 'plan_changed' }, { status: 409 })));
  renderBatchConfirmation();
  await userEvent.click(await screen.findByRole('button', { name: '确认执行' }));
  expect(await screen.findByText('方案已变化，请重新确认')).toBeVisible();
});
```

- [ ] **Step 2: Run batch tests**

Run: `cd frontend && npm test -- BatchExecution.test.tsx`

Expected: FAIL because confirmation/monitor pages are absent.

- [ ] **Step 3: Render exact backend preview and confirmation**

```tsx
<Descriptions column={{ xs: 2, sm: 3, lg: 6 }} items={operationTypes.map(type => ({
  key: type, label: operationLabels[type], children: preview.counts[type] ?? 0,
}))} />
{preview.high_risk_count > 0 && <Checkbox checked={acknowledged} onChange={e => setAcknowledged(e.target.checked)}>
  已核对高风险操作范围
</Checkbox>}
<Button type="primary" danger={preview.high_risk_count > 0} disabled={preview.high_risk_count > 0 && !acknowledged} onClick={confirm}>确认执行</Button>
```

- [ ] **Step 4: Render operation outcomes and eligible retry**

```tsx
<OperationTable operations={batch.operations} columns={['entity', 'operation', 'before', 'after', 'status', 'error', 'verification']} />
<Button icon={<RotateCcw size={16} />} disabled={batch.retryable_operation_ids.length === 0}
        onClick={() => retryOperations(batch.id, batch.retryable_operation_ids)}>重试失败项</Button>
{batch.target_version_download_url && <Button icon={<Download size={16} />} href={batch.target_version_download_url}>下载希沃 CSV</Button>}
```

- [ ] **Step 5: Verify and commit**

Run: `cd frontend && npm test -- BatchExecution.test.tsx && npm run typecheck`

Expected: selected/excluded counts, all operation types, high risk, conflicts, plan change, progress, partial failure, verification failure, retry eligibility, and download tests PASS.

```bash
git add frontend/src/features/batch-confirmation frontend/src/features/execution-monitor frontend/src/components/tables frontend/tests/unit/features/BatchExecution.test.tsx
git commit -m "feat: confirm and monitor governance batches"
```

### Task 7: Build execution history, report, and rollback views

**Files:**
- Create: `frontend/src/features/execution-history/ExecutionHistoryPage.tsx`
- Create: `frontend/src/features/execution-detail/ExecutionDetailPage.tsx`
- Create: `frontend/src/features/report-viewer/ReportViewerPage.tsx`
- Create: `frontend/src/features/rollback-review/RollbackReviewPage.tsx`
- Test: `frontend/tests/unit/features/HistoryReportRollback.test.tsx`

**Interfaces:**
- Consumes: immutable history/detail, permitted actions, report job/content, rollback preflight/conflicts/confirmation.
- Produces: filtered history, audit timeline, optional report action/viewer, safe compensation review.

- [ ] **Step 1: Write backend-permission and conflict tests**

```tsx
it('shows report and rollback only when backend permits them', async () => {
  renderRoute(`/executions/${EXECUTION_ID}`);
  expect(await screen.findByRole('button', { name: '生成治理报告' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '回滚此版本' })).toBeDisabled();
});

it('explains rollback conflicts and disables confirmation', async () => {
  renderRoute(`/executions/${EXECUTION_ID}/rollback`);
  expect(await screen.findByText('后续班级依赖该部门')).toBeVisible();
  expect(screen.getByRole('button', { name: '确认回滚' })).toBeDisabled();
});
```

- [ ] **Step 2: Run history/report tests**

Run: `cd frontend && npm test -- HistoryReportRollback.test.tsx`

Expected: FAIL because pages are absent.

- [ ] **Step 3: Build immutable history/detail**

History filters use task, operator, date range, status, and rollback state from the API. Detail shows operator, task/snapshot/plan refs, source/output CSV versions, before/after, attempts/errors/verification, and audit timeline.

```tsx
<Timeline items={record.audit_events.map(event => ({
  color: statusColor(event.status),
  children: <><strong>{event.label}</strong><div>{formatDateTime(event.created_at)} · {event.operator_id}</div></>,
}))} />
```

- [ ] **Step 4: Implement optional report status/viewer**

```tsx
const generateReport = useMutation({ mutationFn: () => requestReport(execution.id),
  onSuccess: job => navigate(`/reports/${job.id}`) });
<Button icon={<FileText size={16} />} disabled={!execution.permitted_actions.generate_report}
        onClick={() => generateReport.mutate()}>生成治理报告</Button>
```

Viewer displays fixed snapshot refs, statistics, causes, plans, operator, outcomes, failures, rollback state, report version, and download link.

- [ ] **Step 5: Implement rollback preflight review**

```tsx
<Alert type={preflight.allowed ? 'warning' : 'error'}
       message={preflight.allowed ? '回滚将创建新的补偿执行记录' : '当前版本不能直接回滚'} />
<Table dataSource={preflight.conflicts} columns={conflictColumns} pagination={false} />
<Button danger disabled={!preflight.allowed || !acknowledged} onClick={confirmRollback}>确认回滚</Button>
```

- [ ] **Step 6: Verify and commit**

Run: `cd frontend && npm test -- HistoryReportRollback.test.tsx && npm run typecheck`

Expected: history filters, actor, immutable values, audit timeline, permitted actions, report progress/content/version, conflict explanation, blocked and allowed rollback tests PASS.

```bash
git add frontend/src/features/execution-history frontend/src/features/execution-detail frontend/src/features/report-viewer frontend/src/features/rollback-review frontend/tests/unit/features/HistoryReportRollback.test.tsx
git commit -m "feat: add audit reports and rollback review"
```

### Task 8: Verify the complete chain in Playwright

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/tests/e2e/reconciliation-chain.spec.ts`
- Create: `frontend/tests/e2e/responsive.spec.ts`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: running frontend/backend/PostgreSQL/Redis/Celery and synthetic CSV fixtures.
- Produces: browser proof of upload → snapshots → match/difference → mandatory analysis → selection → execution → history → report → rollback.

- [ ] **Step 1: Write the complete user flow**

```ts
test('completes reconciliation, report, and rollback chain', async ({ page }) => {
  await page.goto('/tasks/new');
  await page.getByLabel('第三方权威数据 CSV').setInputFiles('../docs/sample-data/third-party.csv');
  await page.getByLabel('希沃目标数据 CSV').setInputFiles('../docs/sample-data/seewo.csv');
  await page.getByRole('button', { name: '创建对账任务' }).click();
  await expect(page.getByText('差异检测')).toBeVisible();
  await page.getByRole('link', { name: /查看差异/ }).click();
  await expect(page.getByText('成因')).toBeVisible();
  await page.locator('tbody input[type=checkbox]:not(:disabled)').first().check();
  await page.getByRole('button', { name: /批量治理/ }).click();
  await page.getByRole('button', { name: '确认执行' }).click();
  await expect(page.getByText('执行成功')).toBeVisible();
  await page.getByRole('button', { name: '生成治理报告' }).click();
  await expect(page.getByRole('heading', { name: '组织数据治理报告' })).toBeVisible();
  await page.goBack();
  await page.getByRole('button', { name: '回滚此版本' }).click();
  await page.getByLabel('已核对回滚影响').check();
  await page.getByRole('button', { name: '确认回滚' }).click();
  await expect(page.getByText('补偿执行成功')).toBeVisible();
});
```

- [ ] **Step 2: Add responsive overlap checks**

```ts
for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  test(`workbench has no horizontal document overflow at ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport); await page.goto('/');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await expect(page).toHaveScreenshot(`dashboard-${viewport.width}.png`, { fullPage: true });
  });
}
```

- [ ] **Step 3: Run all validation commands**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run mypy app`

Expected: backend suite, lint, and types PASS.

Run: `cd frontend && npm test && npm run lint && npm run typecheck && npm run build && npm run e2e`

Expected: unit tests, lint, type check, production build, complete chain, desktop/mobile screenshots, and overflow checks PASS.

Run: `openspec validate demo`

Expected: `Change 'demo' is valid`.

- [ ] **Step 4: Document exact local commands and commit**

Update `AGENTS.md` with real install, migration, backend/frontend dev, Celery worker, tests, lint, build, E2E, fixture, and OpenSpec commands.

```bash
git add frontend/playwright.config.ts frontend/tests/e2e AGENTS.md
git commit -m "test: verify complete web governance chain"
```

## Module Acceptance

Run: `docker compose -f infra/docker-compose.yml up -d`, start FastAPI, Celery, and Vite using documented commands, then run `cd frontend && npm run e2e`.

Expected: the complete chain works without manual database changes; every screen has loading, empty, error, retry, and permitted-action states where applicable; desktop/mobile screenshots show no overlap; client-supplied operator or policy decisions never replace backend truth.
