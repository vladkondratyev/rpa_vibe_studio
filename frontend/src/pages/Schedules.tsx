import { useState } from "react";
import { api, fmtTime, Process, Schedule } from "../api";
import { parseValue, usePolling } from "../components";

type Draft = { id?: number; process_id: number; cron: string; enabled: boolean; inputs: string; tags: string; robot: string };

const PRESETS: [string, string][] = [
  ["*/15 * * * *", "каждые 15 минут"],
  ["0 * * * *", "каждый час"],
  ["0 9 * * 1-5", "по будням в 9:00"],
  ["0 2 * * *", "каждый день в 2:00"],
  ["0 8 1 * *", "1-го числа в 8:00"],
];

export default function Schedules() {
  const { data, error, reload } = usePolling(() => api<Schedule[]>("/api/schedules"), 10000);
  const procs = usePolling(() => api<Process[]>("/api/processes"), 0);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const edit = (s?: Schedule) => {
    setFormError(null);
    setDraft(
      s
        ? { id: s.id, process_id: s.process_id, cron: s.cron, enabled: s.enabled, inputs: JSON.stringify(s.inputs || {}), tags: s.tags.join(", "), robot: s.robot || "" }
        : { process_id: procs.data?.[0]?.id || 0, cron: "0 9 * * 1-5", enabled: true, inputs: "{}", tags: "", robot: "" },
    );
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft) return;
    const inputs = parseValue(draft.inputs || "{}");
    if (typeof inputs !== "object" || inputs === null || Array.isArray(inputs)) {
      setFormError("Входные данные должны быть JSON-объектом, например {\"folder\": \"/data\"}");
      return;
    }
    const body = {
      process_id: draft.process_id,
      cron: draft.cron.trim(),
      enabled: draft.enabled,
      inputs,
      tags: draft.tags.split(",").map((t) => t.trim()).filter(Boolean),
      robot: draft.robot.trim() || null,
    };
    try {
      await api(draft.id ? `/api/schedules/${draft.id}` : "/api/schedules", { method: draft.id ? "PUT" : "POST", json: body });
      setDraft(null);
      reload();
    } catch (err) {
      setFormError((err as Error).message);
    }
  };

  const toggle = async (s: Schedule) => {
    await api(`/api/schedules/${s.id}`, {
      method: "PUT",
      json: { process_id: s.process_id, cron: s.cron, enabled: !s.enabled, inputs: s.inputs, tags: s.tags, robot: s.robot },
    });
    reload();
  };

  const remove = async (s: Schedule) => {
    if (!confirm("Удалить расписание?")) return;
    await api(`/api/schedules/${s.id}`, { method: "DELETE" });
    reload();
  };

  return (
    <div className="page">
      <header className="page-head">
        <h1>Расписания</h1>
        <button onClick={() => edit()} disabled={!procs.data?.length}>Новое расписание</button>
      </header>
      {error && <div className="error-box">{error}</div>}
      {data && data.length === 0 && <div className="empty">Расписаний нет.</div>}
      {data && data.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Процесс</th>
              <th>Cron</th>
              <th>Метки / робот</th>
              <th>Следующий запуск</th>
              <th>Последний</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.map((s) => (
              <tr key={s.id} className={s.enabled ? "" : "disabled"}>
                <td className="strong">{s.process_name}</td>
                <td><code>{s.cron}</code></td>
                <td>
                  {s.tags.map((t) => <span className="tag" key={t}>{t}</span>)}
                  {s.robot && <span className="tag">{s.robot}</span>}
                </td>
                <td>{s.enabled ? fmtTime(s.next_run_at) : "выключено"}</td>
                <td>{fmtTime(s.last_run_at)}</td>
                <td className="actions">
                  <button className="secondary small" onClick={() => toggle(s)}>{s.enabled ? "Выключить" : "Включить"}</button>
                  <button className="secondary small" onClick={() => edit(s)}>Изменить</button>
                  <button className="danger small" onClick={() => remove(s)}>Удалить</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted small">Время в cron — локальное время сервера оркестратора. Пропущенные во время простоя запуски не догоняются.</p>

      {draft && (
        <div className="modal-backdrop" onClick={() => setDraft(null)}>
          <form className="modal" onSubmit={save} onClick={(e) => e.stopPropagation()}>
            <h3>{draft.id ? "Изменить расписание" : "Новое расписание"}</h3>
            <label className="field">
              <span>Процесс</span>
              <select value={draft.process_id} onChange={(e) => setDraft({ ...draft, process_id: Number(e.target.value) })}>
                {(procs.data || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Cron (минута час день месяц день_недели)</span>
              <input className="code" value={draft.cron} onChange={(e) => setDraft({ ...draft, cron: e.target.value })} />
            </label>
            <div className="presets">
              {PRESETS.map(([c, l]) => (
                <button type="button" key={c} className="link small" onClick={() => setDraft({ ...draft, cron: c })}>{l}</button>
              ))}
            </div>
            <label className="field">
              <span>Входные данные (JSON)</span>
              <input className="code" value={draft.inputs} onChange={(e) => setDraft({ ...draft, inputs: e.target.value })} />
            </label>
            <label className="field">
              <span>Требуемые метки</span>
              <input value={draft.tags} placeholder="os=linux, web" onChange={(e) => setDraft({ ...draft, tags: e.target.value })} />
            </label>
            <label className="field">
              <span>Конкретный робот</span>
              <input value={draft.robot} placeholder="необязательно" onChange={(e) => setDraft({ ...draft, robot: e.target.value })} />
            </label>
            <label className="field checkbox">
              <input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} /> Включено
            </label>
            {formError && <div className="error-box">{formError}</div>}
            <div className="row end">
              <button type="button" className="secondary" onClick={() => setDraft(null)}>Отмена</button>
              <button type="submit">Сохранить</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
