import { CircleCheck, CircleX, LoaderCircle, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ingestionApi } from "../api/ingestion";

type ConnectionState = "checking" | "online" | "offline";

export function ConnectionStatus({ check = ingestionApi.readiness }: { check?: () => Promise<unknown> }) {
  const [state, setState] = useState<ConnectionState>("checking");

  const runCheck = useCallback(async () => {
    setState("checking");
    try {
      await check();
      setState("online");
    } catch {
      setState("offline");
    }
  }, [check]);

  useEffect(() => {
    void runCheck();
  }, [runCheck]);

  if (state === "online") {
    return <span className="connection-state online"><CircleCheck size={14} />后端已连接</span>;
  }

  return (
    <button className={`connection-state ${state}`} type="button" onClick={() => void runCheck()}>
      {state === "checking" ? <LoaderCircle className="spin" size={14} /> : <CircleX size={14} />}
      {state === "checking" ? "检查连接" : "后端未连接"}
      {state === "offline" && <RefreshCw size={12} />}
    </button>
  );
}
