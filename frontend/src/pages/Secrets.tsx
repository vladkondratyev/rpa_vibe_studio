import { useState } from "react";
import { api, fmtTime } from "../api";
import { usePolling } from "../components";

type Secret = { id: number; name: string; description: string; updated_by: string; updated_at: string };

export default function Secrets() {
  const { data, error, reload } = usePolling(() => api<Secret[]>("/api/secrets"), 0);
  const [edit, setEdit] = useState<{ name: string; description: string; isNew: boolean } | null>(null);
  const [value, setValue] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!edit) return;
    try {
      await api(`/api/secrets/${encodeURIComponent(edit.name)}`, {
        method: "PUT",
        json: { name: edit.name, description: edit.description, value: value || null },
      });
      setEdit(null);
      setValue("");
      reload();
    } catch (x) {
      setErr((x as Error).message);
    }
  };
  const remove = async (s: Secret) => {
    if (!confirm(`Удалить секрет «${s.name}»? Процессы, которые его используют, перестанут работать.`)) return;
    await api(`/api/secrets/${encodeURIComponent(s.name)}`, { method: "DELETE" });
    reload();
  };
  return (
    <div className="page">
      <header className="page-head">
        <h1>Секреты</h1>
        <button onClick={() => { setEdit({ name: "", description: "", isNew: true }); setValue(""); setErr(null); }}>Добавить секрет</button>
      </header>
      <p className="muted lead">
        Пароли, токены и ключи API хранятся отдельно от процессов в зашифрованном виде (Fernet, ключ RPA_SECRET_KEY).
        В процессе указывается только имя: параметр «секрет» или <code>{"{{ secret('имя') }}"}</code>. Робот получает
        значения вместе с задачей, в журнале они заменяются на ***, каждая выдача пишется в аудит.
      </p>
      {error && <div className="error-box">{error}</div>}
      <table className="table">
        <thead><tr><th>Имя</th><th>Описание</th><th>Изменён</th><th>Кем</th><th /></tr></thead>
        <tbody>
          {(data || []).map((s) => (
            <tr key={s.id}>
              <td className="code strong">{s.name}</td>
              <td className="muted">{s.description}</td>
              <td>{fmtTime(s.updated_at)}</td>
              <td>{s.updated_by}</td>
              <td className="actions">
                <button className="secondary small" onClick={() => { setEdit({ name: s.name, description: s.description, isNew: false }); setValue(""); setErr(null); }}>Изменить</button>
                <button className="danger small" onClick={() => remove(s)}>Удалить</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && data.length === 0 && <div className="empty">Секретов пока нет.</div>}
      {edit && (
        <div className="modal-backdrop" onClick={() => setEdit(null)}>
          <form className="modal" onSubmit={save} onClick={(e) => e.stopPropagation()}>
            <h3>{edit.isNew ? "Новый секрет" : `Секрет ${edit.name}`}</h3>
            {edit.isNew && (
              <label className="field">
                <span>Имя</span>
                <input autoFocus placeholder="portal_password" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value.replace(/[^\w.\-]/g, "") })} />
              </label>
            )}
            <label className="field">
              <span>Значение {edit.isNew ? "" : "(пусто — не менять)"}</span>
              <input type="password" autoComplete="new-password" value={value} onChange={(e) => setValue(e.target.value)} />
              <small className="muted">Значение нельзя посмотреть после сохранения — только заменить.</small>
            </label>
            <label className="field">
              <span>Описание</span>
              <input value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })} />
            </label>
            {err && <div className="error-box">{err}</div>}
            <div className="row end">
              <button type="button" className="secondary" onClick={() => setEdit(null)}>Отмена</button>
              <button type="submit" disabled={!edit.name || (edit.isNew && !value)}>Сохранить</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
