import { useState } from "react";
import { api, fmtTime, Process } from "../api";
import { navigate } from "../App";
import { RunDialog, usePolling } from "../components";

export default function Processes() {
  const { data, error, reload } = usePolling(() => api<Process[]>("/api/processes"), 0);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [running, setRunning] = useState<Process | null>(null);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const p = await api<Process>("/api/processes", { method: "POST", json: { name: name.trim() } });
      navigate(`/designer/${p.id}`);
    } catch (err) {
      setCreateError((err as Error).message);
    }
  };

  const openRun = async (p: Process) => {
    setRunning(await api<Process>(`/api/processes/${p.id}`));
  };

  const remove = async (p: Process) => {
    if (!confirm(`Удалить процесс «${p.name}» и его расписания? История запусков сохранится.`)) return;
    await api(`/api/processes/${p.id}`, { method: "DELETE" });
    reload();
  };

  return (
    <div className="page">
      <header className="page-head">
        <h1>Процессы</h1>
        <button onClick={() => setCreating(true)}>Новый процесс</button>
      </header>
      {error && <div className="error-box">{error}</div>}
      {data && data.length === 0 && (
        <div className="empty">Процессов пока нет. Создайте первый и соберите его в дизайнере.</div>
      )}
      {data && data.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Название</th>
              <th>Описание</th>
              <th>Версия</th>
              <th>Изменён</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.map((p) => (
              <tr key={p.id}>
                <td>
                  <a href={`#/designer/${p.id}`} className="strong">
                    {p.name}
                  </a>
                </td>
                <td className="muted">{p.description}</td>
                <td>v{p.version}</td>
                <td>{fmtTime(p.updated_at)}</td>
                <td className="actions">
                  <button className="secondary small" onClick={() => openRun(p)}>
                    Запустить
                  </button>
                  <a className="button secondary small" href={`#/jobs?process=${p.id}`}>
                    Запуски
                  </a>
                  <button className="danger small" onClick={() => remove(p)}>
                    Удалить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {creating && (
        <div className="modal-backdrop" onClick={() => setCreating(false)}>
          <form className="modal" onSubmit={create} onClick={(e) => e.stopPropagation()}>
            <h3>Новый процесс</h3>
            <label className="field">
              <span>Название</span>
              <input autoFocus value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            {createError && <div className="error-box">{createError}</div>}
            <div className="row end">
              <button type="button" className="secondary" onClick={() => setCreating(false)}>
                Отмена
              </button>
              <button type="submit" disabled={!name.trim()}>
                Создать
              </button>
            </div>
          </form>
        </div>
      )}
      {running && (
        <RunDialog
          processId={running.id}
          definition={running.definition}
          onClose={() => setRunning(null)}
          onStarted={(job) => navigate(`/jobs/${job.id}`)}
        />
      )}
    </div>
  );
}
