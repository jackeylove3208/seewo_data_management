import { Alert, Modal } from "antd";
import { useState } from "react";

import { ingestionApi } from "../../api/ingestion";
import { agentApi } from "../../api/agent";
import { ApiError } from "../../api/client";
import { removeStoredTask } from "../../data/taskHistory";
import type { TaskHistoryItem } from "../../types/domain";

export function useTaskDeletion(onDeleted?: (taskId: string) => void) {
  const [selectedTask, setSelectedTask] = useState<TaskHistoryItem>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();

  async function confirmDelete() {
    if (!selectedTask || pending) return;
    setPending(true);
    setError(undefined);
    try {
      if (["new-agent-v1", "agent-graph-v1"].includes(selectedTask.workflowVersion ?? "")) {
        await agentApi.deleteTask(selectedTask.id);
      } else {
        await ingestionApi.deleteTask(selectedTask.id);
      }
      removeStoredTask(selectedTask.id);
      onDeleted?.(selectedTask.id);
      setSelectedTask(undefined);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        removeStoredTask(selectedTask.id);
        onDeleted?.(selectedTask.id);
        setSelectedTask(undefined);
        return;
      }
      setError(caught instanceof Error ? caught.message : "删除任务失败，请稍后重试");
    } finally {
      setPending(false);
    }
  }

  function requestDelete(task: TaskHistoryItem) {
    if (pending) return;
    setError(undefined);
    setSelectedTask(task);
  }

  const confirmation = (
    <Modal
      rootClassName="apple-agent-modal"
      title="删除任务"
      open={Boolean(selectedTask)}
      okText="确认删除"
      cancelText="取消"
      okButtonProps={{ danger: true, loading: pending, disabled: pending }}
      closable={!pending}
      maskClosable={!pending}
      onCancel={() => { if (!pending) setSelectedTask(undefined); }}
      onOk={() => void confirmDelete()}
    >
      <p>确定要删除“{selectedTask?.title}”吗？</p>
      <p>治理执行开始前可删除；分析、方案、快照和上传文件将永久删除。</p>
      {error && <Alert role="alert" type="error" message={error} showIcon />}
    </Modal>
  );

  return { requestDelete, confirmation, pending };
}
