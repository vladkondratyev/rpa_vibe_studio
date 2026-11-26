import { useEffect, useState } from "react";
import { api, fmtTime, Process } from "../api";
import { navigate } from "../App";
import { parseValue, usePolling } from "../components";

type Queue = {
  id: number;
  name: string;
  description: string;
  max_retries: number;
  sla_minutes: number;
  process_id: number | null;
  process_name: string | null;
  max_parallel: number;
  robot_tags: string[];
  counts: Record<string, number>;
  overdue: number;
};
type Item = {
  id: number;
  reference: string | null;
  data: Record<string, unknown>;
  priority: number;
  status: string;
  retries: number;
  result: unknown;
  error: string | null;
  business_error: boolean;
  locked_by: string | null;
  job_id: number | null;
  created_at: string;
  finished_at: string | null;
  deadline: string | null;
  overdue: boolean;
};

const ITEM_STATUS: Record<string, string> = { new: "Новый", in_progress: "В работе", done: "Обработан", failed: "Ошибка" };
const ITEM_CLASS: Record<string, string> = { new: "queued", in_progress: "running", done: "success", failed: "failed" };

export default function Queues({ queueId }: { queueId?: number }) {
  const { data, error, reload } = usePolling(() => api<Queue[]>("/api/queues"), 5000);
  const [edit, setEdit] = useState<Partial<Queue> | null>(null);
  if (queueId) {
    const q = data?.find((x) => x.id === queueId);
    return q ? <QueueItems queue={q} onBack={() => navigate("/queues")} /> : <div className="page muted">Загрузка…</div>;
  }
  return (
    <div className="page">
      <header className="page-head">
        <h1>Очереди</h1>
        <button onClick={() => setEdit({ max_retries: 2, sla_minutes: 0, max_parallel: 1, robot_tags: [] })}>Новая очередь</button>
      </header>
      <p className="muted lead">
        Транзакционная обработка: заявки, платежи, документы. Каждый элемент берёт ровно один робот; технические сбои
        повторяются, бизнес-исключения уходят в «Ошибки» без повтора. Триггер запускает процесс, когда в очереди
        появляются элементы — до N роботов параллельно.
      </p>
      {error && <div className="error-box">{error}</div>}
      {data && data.length === 0 && <div className="empty">Очередей пока нет.</div>}
      {data && data.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Очередь</th>
              <th>Новые</th>
              <th>В работе</th>
              <th>Обработано</th>
              <th>Ошибки</th>
              <th>SLA</th>
              <th>Триггер</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.map((q) => (
              <tr key={q.id} className="clickable" onClick={() => navigate(`/queues/${q.id}`)}>
                <td>
                  <div className="strong">{q.name}</div>
                  <div className="muted small">{q.description}</div>
                </td>
                <td>{q.counts.new}</td>
                <td>{q.counts.in_progress}</td>
                <td>{q.counts.done}</td>
                <td>{q.counts.failed ? <span className="badge status-failed">{q.counts.failed}</span> : 0}</td>
                <td>
                  {q.sla_minutes ? `${q.sla_minutes} мин` : "—"}
                  {q.overdue > 0 && <span className="badge status-failed"> просрочено {q.overdue}</span>}
                </td>
                <td className="small">{q.process_name ? `${q.process_name} ×${q.max_parallel}` : "—"}</td>
                <td className="actions" onClick={(e) => e.stopPropagation()}>
                  <button className="secondary small" onClick={() => setEdit(q)}>Настроить</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {edit && <QueueDialog queue={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); }} />}
    </div>
  );
}

function QueueDialog({ queue, onClose, onSaved }: { queue: Partial<Queue>; onClose: () => void; onSaved: () => void }) {
  const [q, setQ] = useState<Partial<Queue>>(queue);
  const [procs, setProcs] = useState<Process[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<Process[]>("/api/processes").then(setProcs).catch(() => {});
  }, []);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const body = { ...q, robot_tags: q.robot_tags || [], process_id: q.process_id || null };
      if (q.id) await api(`/api/queues/${q.id}`, { method: "PUT", json: body });
      else await api("/api/queues", { method: "POST", json: body });
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const remove = async () => {
    if (!q.id || !confirm(`Удалить очередь «${q.name}» со всеми элементами?`)) return;
    await api(`/api/queues/${q.id}`, { method: "DELETE" });
    onSaved();
  };
  const num = (k: keyof Queue) => (e: React.ChangeEvent<HTMLInputElement>) => setQ({ ...q, [k]: Number(e.target.value) || 0 });
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal" onSubmit={save} onClick={(e) => e.stopPropagation()}>
        <h3>{q.id ? "Настройки очереди" : "Новая очередь"}</h3>
        <label className="field"><span>Название</span><input autoFocus value={q.name || ""} onChange={(e) => setQ({ ...q, name: e.target.value })} /></label>
        <label className="field"><span>Описание</span><input value={q.description || ""} onChange={(e) => setQ({ ...q, description: e.target.value })} /></label>
        <div className="grid2">
          <label className="field"><span>Повторов при сбое</span><input value={q.max_retries ?? 2} onChange={num("max_retries")} /></label>
          <label className="field"><span>SLA, минут (0 — нет)</span><input value={q.sla_minutes ?? 0} onChange={num("sla_minutes")} /></label>
        </div>
        <h4>Триггер</h4>
        <label className="field">
          <span>Запускать процесс при новых элементах</span>
          <select value={q.process_id || ""} onChange={(e) => setQ({ ...q, process_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">— не запускать —</option>
            {procs.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <div className="grid2">
          <label className="field"><span>Роботов параллельно</span><input value={q.max_parallel ?? 1} onChange={num("max_parallel")} /></label>
          <label className="field"><span>Метки роботов</span><input value={(q.robot_tags || []).join(", ")} onChange={(e) => setQ({ ...q, robot_tags: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></label>
        </div>
        {error && <div className="error-box">{error}</div>}
        <div className="row end">
          {q.id && <button type="button" className="danger" onClick={remove}>Удалить</button>}
          <span className="spacer" />
          <button type="button" className="secondary" onClick={onClose}>Отмена</button>
          <button type="submit" disabled={!q.name}>Сохранить</button>
        </div>
      </form>
    </div>
  );
}

function QueueItems({ queue, onBack }: { queue: Queue; onBack: () => void }) {
  const [status, setStatus] = useState("");
  const { data, reload } = usePolling(
    () => api<Item[]>(`/api/queues/${queue.id}/items${status ? `?status=${status}` : ""}`),
    4000,
    [status],
  );
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<Item | null>(null);
  const patch = async (it: Item, body: Record<string, unknown>) => {
    await api(`/api/queue-items/${it.id}`, { method: "PATCH", json: body });
    setOpen(null);
    reload();
  };
  return (
    <div className="page">
      <header className="page-head">
        <button className="link" onClick={onBack}>← Очереди</button>
        <h1>{queue.name}</h1>
        <button onClick={() => setAdding(true)}>Добавить элементы</button>
      </header>
      <div className="chips">
        {["", "new", "in_progress", "done", "failed"].map((s) => (
          <button key={s} className={`chip ${s === status ? "on" : ""}`} onClick={() => setStatus(s)}>
            {s ? ITEM_STATUS[s] : "Все"}
            {s && <span className="chip-n">{queue.counts[s] || 0}</span>}
          </button>
        ))}
      </div>
      <table className="table">
        <thead>
          <tr><th>#</th><th>Ключ</th><th>Данные</th><th>Статус</th><th>Попыток</th><th>Робот</th><th>Создан</th><th>Срок</th></tr>
        </thead>
        <tbody>
          {(data || []).map((it) => (
            <tr key={it.id} className="clickable" onClick={() => setOpen(it)}>
              <td>{it.id}</td>
              <td>{it.reference || "—"}</td>
              <td className="code small ellipsis">{JSON.stringify(it.data).slice(0, 90)}</td>
              <td>
                <span className={`badge status-${ITEM_CLASS[it.status]}`}>{ITEM_STATUS[it.status]}</span>
                {it.business_error && <span className="tag">бизнес</span>}
              </td>
              <td>{it.retries}</td>
              <td className="small">{it.locked_by || "—"}</td>
              <td className="small">{fmtTime(it.created_at)}</td>
              <td className="small">{it.deadline ? <span className={it.overdue ? "danger-text" : ""}>{fmtTime(it.deadline)}</span> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && data.length === 0 && <div className="empty">Элементов нет.</div>}
      {adding && <AddItems queueId={queue.id} onClose={() => { setAdding(false); reload(); }} />}
      {open && <ItemDialog item={open} onClose={() => setOpen(null)} onPatch={patch} />}
    </div>
  );
}

function AddItems({ queueId, onClose }: { queueId: number; onClose: () => void }) {
  const [text, setText] = useState('[\n  {"reference": "PAY-1", "priority": 0, "data": {"сумма": 1500, "получатель": "ООО Ромашка"}}\n]');
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const items = parseValue(text);
      if (!Array.isArray(items)) throw new Error("Нужен JSON-массив элементов");
      const r = await api<{ added: number }>(`/api/queues/${queueId}/items`, { method: "POST", json: { items } });
      alert(`Добавлено: ${r.added}`);
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal wide" onSubmit={submit} onClick={(e) => e.stopPropagation()}>
        <h3>Добавить элементы</h3>
        <p className="muted small">JSON-массив. Элементы с ключом (reference), который уже есть в очереди, пропускаются.</p>
        <textarea className="code" rows={10} value={text} onChange={(e) => setText(e.target.value)} />
        {error && <div className="error-box">{error}</div>}
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>Отмена</button>
          <button type="submit">Добавить</button>
        </div>
      </form>
    </div>
  );
}

function ItemDialog({ item, onClose, onPatch }: { item: Item; onClose: () => void; onPatch: (it: Item, b: Record<string, unknown>) => void }) {
  const [data, setData] = useState(JSON.stringify(item.data, null, 2));
  const [error, setError] = useState<string | null>(null);
  const saveData = (status?: string) => {
    try {
      onPatch(item, { data: JSON.parse(data), ...(status ? { status } : {}) });
    } catch (e) {
      setError(`JSON: ${(e as Error).message}`);
    }
  };
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <h3>Элемент #{item.id} {item.reference && <span className="muted">· {item.reference}</span>}</h3>
        <p className="small">
          <span className={`badge status-${ITEM_CLASS[item.status]}`}>{ITEM_STATUS[item.status]}</span>{" "}
          попыток: {item.retries} {item.job_id && <a href={`#/jobs/${item.job_id}`}>запуск #{item.job_id}</a>}
        </p>
        {item.error && <pre className="error-box">{item.error}</pre>}
        <h4>Данные (можно исправить перед повтором)</h4>
        <textarea className="code" rows={10} value={data} onChange={(e) => setData(e.target.value)} />
        {item.result != null && (<><h4>Результат</h4><pre className="json">{JSON.stringify(item.result, null, 2)}</pre></>)}
        {error && <div className="error-box">{error}</div>}
        <div className="row end">
          <button className="secondary" onClick={() => saveData()}>Сохранить данные</button>
          <button className="secondary" onClick={() => saveData("done")}>Отметить обработанным</button>
          <button onClick={() => saveData("new")}>Повторить</button>
        </div>
      </div>
    </div>
  );
}
