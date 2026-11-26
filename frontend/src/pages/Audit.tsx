import { useState } from "react";
import { api, fmtTime } from "../api";
import { usePolling } from "../components";

type Entry = { id: number; ts: string; username: string; action: string; entity: string; entity_id: string; details: Record<string, unknown> };

const ACTIONS: Record<string, string> = {
  "auth.login": "Вход", "auth.failed": "Неудачный вход", "process.create": "Создан процесс", "process.update": "Изменён процесс",
  "process.delete": "Удалён процесс", "process.run": "Запуск процесса", "process.restore": "Откат версии",
  "process.import": "Импорт процесса", "process.from_template": "Процесс из шаблона", "process.vibe": "Процесс по описанию (ИИ)",
  "job.cancel": "Остановка запуска", "secret.write": "Запись секрета", "secret.delete": "Удаление секрета",
  "secret.deliver": "Выдача секретов роботу", "user.create": "Создан пользователь", "user.update": "Изменён пользователь",
  "user.delete": "Удалён пользователь", "token.create": "Выпущен API-токен",
};

export default function Audit() {
  const [action, setAction] = useState("");
  const { data, error } = usePolling(() => api<Entry[]>(`/api/audit${action ? `?action=${action}` : ""}`), 10000, [action]);
  return (
    <div className="page wide">
      <header className="page-head">
        <h1>Аудит</h1>
        <select className="auto" value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">Все действия</option>
          <option value="auth">Входы</option>
          <option value="process">Процессы</option>
          <option value="secret">Секреты</option>
          <option value="user">Пользователи</option>
          <option value="queue">Очереди</option>
        </select>
      </header>
      {error && <div className="error-box">{error}</div>}
      <table className="table">
        <thead><tr><th>Время</th><th>Кто</th><th>Действие</th><th>Объект</th><th>Подробности</th></tr></thead>
        <tbody>
          {(data || []).map((a) => (
            <tr key={a.id} className={a.action === "auth.failed" ? "warn-row" : ""}>
              <td className="small">{fmtTime(a.ts)}</td>
              <td>{a.username}</td>
              <td>{ACTIONS[a.action] || a.action}</td>
              <td className="small">{a.entity} {a.entity_id && `#${a.entity_id}`}</td>
              <td className="code small ellipsis">{Object.keys(a.details || {}).length ? JSON.stringify(a.details) : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
