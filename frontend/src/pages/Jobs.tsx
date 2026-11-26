import { useState } from "react";
import { api, duration, fmtTime, Job } from "../api";
import { StatusBadge, usePolling } from "../components";

export default function Jobs({ processId }: { processId?: number }) {
  const [status, setStatus] = useState("");
  const { data, error } = usePolling(
    () => {
      const q = new URLSearchParams();
      if (processId) q.set("process_id", String(processId));
      if (status) q.set("status", status);
      return api<Job[]>(`/api/jobs?${q}`);
    },
    3000,
    [processId, status],
  );

  return (
    <div className="page">
      <header className="page-head">
        <h1>Запуски{processId && data?.[0] ? `: ${data[0].process_name}` : ""}</h1>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Все статусы</option>
          <option value="queued">В очереди</option>
          <option value="running">Выполняется</option>
          <option value="success">Успешно</option>
          <option value="failed">Ошибка</option>
          <option value="cancelled">Отменён</option>
        </select>
      </header>
      {processId && <a href="#/jobs" className="link small">Показать все процессы</a>}
      {error && <div className="error-box">{error}</div>}
      {data && data.length === 0 && <div className="empty">Запусков нет.</div>}
      {data && data.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>#</th>
              <th>Процесс</th>
              <th>Статус</th>
              <th>Робот</th>
              <th>Источник</th>
              <th>Создан</th>
              <th>Длительность</th>
            </tr>
          </thead>
          <tbody>
            {data.map((j) => (
              <tr key={j.id} className="clickable" onClick={() => (window.location.hash = `/jobs/${j.id}`)}>
                <td>{j.id}</td>
                <td>
                  {j.process_name} <span className="muted">v{j.process_version}</span>
                </td>
                <td>
                  <StatusBadge status={j.status} />
                </td>
                <td>{j.executed_by || <span className="muted">{j.robot_name || "любой"}</span>}</td>
                <td>{j.source === "schedule" ? "расписание" : "вручную"}</td>
                <td>{fmtTime(j.created_at)}</td>
                <td>{duration(j.started_at, j.finished_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
