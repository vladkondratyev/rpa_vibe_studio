import { useEffect, useRef, useState } from "react";
import { api, Definition, Job, Robot, STATUS_LABEL } from "./api";

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge status-${status}`}>{STATUS_LABEL[status] || status}</span>;
}

/** Повторяет загрузку каждые `ms` миллисекунд, пока компонент на экране. */
export function usePolling<T>(load: () => Promise<T>, ms: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const d = await loadRef.current();
        if (alive) {
          setData(d);
          setError(null);
        }
      } catch (e) {
        if (alive) setError((e as Error).message);
      }
      if (alive && ms > 0) timer = window.setTimeout(tick, ms);
    };
    tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  const reload = async () => {
    try {
      setData(await loadRef.current());
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return { data, error, reload };
}

export function parseValue(raw: string): unknown {
  const t = raw.trim();
  if (t === "") return "";
  try {
    return JSON.parse(t);
  } catch {
    return raw;
  }
}

export function showValue(v: unknown): string {
  if (v === undefined || v === null) return "";
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** Диалог запуска: входные переменные, метки, конкретный робот. */
export function RunDialog({
  processId,
  definition,
  onClose,
  onStarted,
}: {
  processId: number;
  definition?: Definition;
  onClose: () => void;
  onStarted: (job: Job) => void;
}) {
  const vars = definition?.variables || [];
  const [inputs, setInputs] = useState<Record<string, string>>(
    Object.fromEntries(vars.map((v) => [v.name, showValue(v.default)])),
  );
  const [tags, setTags] = useState("");
  const [robot, setRobot] = useState("");
  const [robots, setRobots] = useState<Robot[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Robot[]>("/api/robots").then(setRobots).catch(() => {});
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const job = await api<Job>(`/api/processes/${processId}/run`, {
        method: "POST",
        json: {
          inputs: Object.fromEntries(Object.entries(inputs).map(([k, v]) => [k, parseValue(v)])),
          tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
          robot: robot || null,
        },
      });
      onStarted(job);
    } catch (err) {
      const details = (err as { details?: { errors?: string[] } }).details;
      setError([(err as Error).message, ...(details?.errors || [])].join("\n"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal" onSubmit={submit} onClick={(e) => e.stopPropagation()}>
        <h3>Запуск процесса</h3>
        {vars.length > 0 && <h4>Входные переменные</h4>}
        {vars.map((v) => (
          <label key={v.name} className="field">
            <span>{v.name}</span>
            <input value={inputs[v.name] ?? ""} onChange={(e) => setInputs({ ...inputs, [v.name]: e.target.value })} />
          </label>
        ))}
        <label className="field">
          <span>Робот</span>
          <select value={robot} onChange={(e) => setRobot(e.target.value)}>
            <option value="">Любой подходящий</option>
            {robots.map((r) => (
              <option key={r.id} value={r.name}>
                {r.name} {r.online ? "" : "(не в сети)"}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Требуемые метки</span>
          <input placeholder="например os=linux, web" value={tags} onChange={(e) => setTags(e.target.value)} />
        </label>
        {error && <pre className="error-box">{error}</pre>}
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Отмена
          </button>
          <button type="submit" disabled={busy}>
            Запустить
          </button>
        </div>
      </form>
    </div>
  );
}
