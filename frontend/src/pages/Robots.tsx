import { api, fmtTime, Robot } from "../api";
import { usePolling } from "../components";

export default function Robots() {
  const { data, error, reload } = usePolling(() => api<Robot[]>("/api/robots"), 5000);
  const server = window.location.origin.replace(":5173", ":8000");

  const remove = async (r: Robot) => {
    if (!confirm(`Удалить робота «${r.name}» из списка? Если агент запущен, он зарегистрируется снова.`)) return;
    await api(`/api/robots/${r.id}`, { method: "DELETE" });
    reload();
  };

  return (
    <div className="page">
      <header className="page-head">
        <h1>Роботы</h1>
      </header>
      {error && <div className="error-box">{error}</div>}
      {data && data.length === 0 && <div className="empty">Ни один робот ещё не подключался.</div>}
      {data && data.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Имя</th>
              <th>Состояние</th>
              <th>ОС / хост</th>
              <th>Метки</th>
              <th>Последняя связь</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.map((r) => (
              <tr key={r.id}>
                <td className="strong">{r.name}</td>
                <td>
                  {!r.online ? (
                    <span className="badge status-cancelled">Не в сети</span>
                  ) : r.current_job_id ? (
                    <a href={`#/jobs/${r.current_job_id}`} className="badge status-running">Запуск #{r.current_job_id}</a>
                  ) : (
                    <span className="badge status-success">Свободен</span>
                  )}
                </td>
                <td>{r.os} <span className="muted">{r.hostname}</span></td>
                <td>{r.tags.map((t) => <span key={t} className="tag">{t}</span>)}</td>
                <td>{fmtTime(r.last_seen)}</td>
                <td className="actions">
                  <button className="danger small" onClick={() => remove(r)}>Удалить</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <section className="help">
        <h3>Подключить робота</h3>
        <p className="muted">На машине робота (Windows, Linux или macOS) с установленным Python 3.9+:</p>
        <pre className="json">{`pip install -r requirements.txt
python -m playwright install chromium
python -m rpa_robot agent --server ${server} --name robot-01 --tags web,excel --token <RPA_TOKEN>`}</pre>
        <p className="muted small">
          Метка <code>os=windows</code> / <code>os=linux</code> / <code>os=macos</code> добавляется автоматически. Задача достанется роботу, у
          которого есть все требуемые метки.
        </p>
      </section>
    </div>
  );
}
