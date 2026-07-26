import { useQuery } from "@tanstack/react-query";
import { ArrowRight, History } from "lucide-react";
import { Link } from "react-router-dom";
import { reportingApi } from "../../api/reporting";

export function ExecutionHistoryPage() {
  const query = useQuery({ queryKey: ["executions"], queryFn: reportingApi.listExecutions });
  return <main className="operations-page apple-page"><header className="operations-header"><div><h1>执行历史</h1><p>治理批次、报告和历史恢复记录</p></div></header>
    {query.isLoading && <p>正在加载执行记录...</p>}{query.isError && <p role="alert">执行记录加载失败</p>}
    <div className="execution-list">{query.data?.items.map(item => <Link key={item.id} to={`/executions/${item.id}`} className="execution-row"><History size={17}/><span><strong>{item.status}</strong><small>{item.confirmed_by} · {new Date(item.confirmed_at).toLocaleString()}</small></span><ArrowRight size={16}/></Link>)}</div>
  </main>;
}
