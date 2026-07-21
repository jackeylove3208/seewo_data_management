import { Alert, Modal } from "antd";
import { useState } from "react";

import { ingestionApi } from "../../api/ingestion";
import { removeStoredTask } from "../../data/taskHistory";
import type { TaskHistoryItem } from "../../types/domain";

export function useTaskDeletion() {
  const [selectedTask, setSelectedTask] = useState<TaskHistoryItem>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();

  async function confirmDelete() {
    if (!selectedTask || pending) return;
    setPending(true);
    setError(undefined);
    try {
      await ingestionApi.deleteTask(selectedTask.id);
      removeStoredTask(selectedTask.id);
      setSelectedTask(undefined);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除任务失败，请稍后重试");
    } finally {
      setPending(false);
    }
  }

  function requestDelete(task: TaskHistoryItem) {
    if (task.isDemo || pending) return;
    setError(undefined);
    setSelectedTask(task);
  }

  const confirmation = (
    <Modal
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
