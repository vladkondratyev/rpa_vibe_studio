import { useState } from "react";
import { api, fmtTime, ROLE_LABEL } from "../api";
import { usePolling } from "../components";

type User = { id: number; username: string; full_name: string; role: string; source: string; active: boolean; last_login: string | null };

const ROLE_HELP: Record<string, string> = {
  viewer: "смотрит процессы, запуски, аналитику",
  operator: "+ запускает и останавливает, работает с очередями",
  developer: "+ создаёт процессы, расписания, секреты, OCR-шаблоны",
  admin: "+ пользователи, модели ИИ, аудит",
  robot: "только API роботов — для персональных токенов роботов",
};

export default function Users() {
  const { data, error, reload } = usePolling(() => api<User[]>("/api/users"), 0);
  const [edit, setEdit] = useState<(Partial<User> & { password?: string }) | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!edit) return;
    try {
      const body = { username: edit.username, full_name: edit.full_name || "", role: edit.role || "viewer", active: edit.active ?? true, password: edit.password || null };
      if (edit.id) await api(`/api/users/${edit.id}`, { method: "PUT", json: body });
      else await api("/api/users", { method: "POST", json: body });
      setEdit(null);
      reload();
    } catch (x) {
      setErr((x as Error).message);
    }
  };
  const issue = async (u: User) => {
    const r = await api<{ token: string }>(`/api/users/${u.id}/tokens`, { method: "POST", json: { name: "API" } });
    setToken(r.token);
  };
  const remove = async (u: User) => {
    if (!confirm(`Удалить пользователя ${u.username}?`)) return;
    await api(`/api/users/${u.id}`, { method: "DELETE" });
    reload();
  };
  return (
    <div className="page">
      <header className="page-head">
        <h1>Пользователи и роли</h1>
        <button onClick={() => { setEdit({ role: "operator", active: true }); setErr(null); }}>Добавить</button>
      </header>
      <p className="muted lead">
        Локальные учётные записи и вход через LDAP / Active Directory (RPA_LDAP_URL; роль — по группам RPA_LDAP_ROLES).
        Для роботов создайте пользователя с ролью «Робот» и выпустите ему API-токен.
      </p>
      {error && <div className="error-box">{error}</div>}
      <table className="table">
        <thead><tr><th>Логин</th><th>ФИО</th><th>Роль</th><th>Источник</th><th>Последний вход</th><th /></tr></thead>
        <tbody>
          {(data || []).map((u) => (
            <tr key={u.id} className={u.active ? "" : "disabled"}>
              <td className="strong">{u.username}</td>
              <td>{u.full_name}</td>
              <td><span className="badge">{ROLE_LABEL[u.role] || u.role}</span></td>
              <td>{u.source === "ldap" ? "LDAP / AD" : "локальный"}</td>
              <td>{fmtTime(u.last_login)}</td>
              <td className="actions">
                <button className="secondary small" onClick={() => issue(u)}>API-токен</button>
                <button className="secondary small" onClick={() => { setEdit(u); setErr(null); }}>Изменить</button>
                <button className="danger small" onClick={() => remove(u)}>Удалить</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {edit && (
        <div className="modal-backdrop" onClick={() => setEdit(null)}>
          <form className="modal" onSubmit={save} onClick={(e) => e.stopPropagation()}>
            <h3>{edit.id ? edit.username : "Новый пользователь"}</h3>
            {!edit.id && <label className="field"><span>Логин</span><input autoFocus value={edit.username || ""} onChange={(e) => setEdit({ ...edit, username: e.target.value })} /></label>}
            <label className="field"><span>ФИО</span><input value={edit.full_name || ""} onChange={(e) => setEdit({ ...edit, full_name: e.target.value })} /></label>
            <label className="field">
              <span>Роль</span>
              <select value={edit.role} onChange={(e) => setEdit({ ...edit, role: e.target.value })}>
                {Object.keys(ROLE_HELP).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]} — {ROLE_HELP[r]}</option>)}
              </select>
            </label>
            {edit.source !== "ldap" && (
              <label className="field"><span>Пароль {edit.id ? "(пусто — не менять)" : ""}</span><input type="password" autoComplete="new-password" value={edit.password || ""} onChange={(e) => setEdit({ ...edit, password: e.target.value })} /></label>
            )}
            <label className="field checkbox"><input type="checkbox" checked={edit.active ?? true} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} /><span>Активен</span></label>
            {err && <div className="error-box">{err}</div>}
            <div className="row end">
              <button type="button" className="secondary" onClick={() => setEdit(null)}>Отмена</button>
              <button type="submit" disabled={!edit.username}>Сохранить</button>
            </div>
          </form>
        </div>
      )}
      {token && (
        <div className="modal-backdrop" onClick={() => setToken(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>API-токен</h3>
            <p className="muted small">Скопируйте сейчас — повторно его не показать. Для робота: <code>--token</code> или RPA_TOKEN.</p>
            <pre className="json">{token}</pre>
            <div className="row end"><button onClick={() => { navigator.clipboard?.writeText(token); setToken(null); }}>Скопировать и закрыть</button></div>
          </div>
        </div>
      )}
    </div>
  );
}
