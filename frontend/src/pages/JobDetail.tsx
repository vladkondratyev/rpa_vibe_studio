import { useEffect, useRef, useState } from "react";
import { api, ArtifactInfo, duration, fetchBlob, fmtTime, Job, LogLine } from "../api";
import { StatusBadge } from "../components";

const ACTIVE = ["queued", "running"];

export default function JobDetail({ jobId }: { jobId: number }) {
  const [job, setJob] = useState<Job | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showDebug, setShowDebug] = useState(false);
  const lastId = useRef(0);
  const logBox = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    let timer: number;
    const tick = async () => {
      try {
        const [j, newLogs] = await Promise.all([
          api<Job>(`/api/jobs/${jobId}`),
          api<LogLine[]>(`/api/jobs/${jobId}/logs?after=${lastId.current}`),
        ]);
        if (!alive) return;
        setJob(j);
        if (newLogs.length) {
          lastId.current = newLogs[newLogs.length - 1].id;
          setLogs((l) => [...l, ...newLogs]);
        }
        setArtifacts(await api<ArtifactInfo[]>(`/api/jobs/${jobId}/artifacts`));
        if (ACTIVE.includes(j.status)) timer = window.setTimeout(tick, 1500);
      } catch (e) {
        if (alive) setError((e as Error).message);
      }
    };
    tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [jobId]);

  useEffect(() => {
    const el = logBox.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 80) el.scrollTop = el.scrollHeight;
  }, [logs]);

  const cancel = async () => setJob(await api<Job>(`/api/jobs/${jobId}/cancel`, { method: "POST" }));

  if (error) return <div className="page"><div className="error-box">{error}</div></div>;
  if (!job) return <div className="page muted">Загрузка…</div>;

  const visible = showDebug ? logs : logs.filter((l) => l.level !== "debug");

  return (
    <div className="page">
      <a href="#/jobs" className="link small">← Запуски</a>
      <header className="page-head">
        <h1>
          Запуск #{job.id}: {job.process_name} <span className="muted">v{job.process_version}</span>
        </h1>
        <StatusBadge status={job.status} />
        {ACTIVE.includes(job.status) && (
          <button className="danger" onClick={cancel} disabled={job.cancel_requested}>
            {job.cancel_requested ? "Останавливается…" : "Остановить"}
          </button>
        )}
      </header>

      <div className="cards">
        <div className="card"><div className="muted small">Робот</div>{job.executed_by || "—"}</div>
        <div className="card"><div className="muted small">Создан</div>{fmtTime(job.created_at)}</div>
        <div className="card"><div className="muted small">Начат</div>{fmtTime(job.started_at)}</div>
        <div className="card"><div className="muted small">Длительность</div>{duration(job.started_at, job.finished_at)}</div>
      </div>

      {job.error && (
        <div className="error-box">
          <strong>Ошибка</strong>
          {job.failed_node && job.process_id && (
            <a className="link small" href={`#/designer/${job.process_id}?node=${encodeURIComponent(job.failed_node)}`}>
              {" "}показать блок в дизайнере
            </a>
          )}
          <pre>{job.error}</pre>
        </div>
      )}

      <div className="grid2">
        <section>
          <h3>Входные данные</h3>
          <pre className="json">{JSON.stringify(job.inputs, null, 2)}</pre>
        </section>
        <section>
          <h3>Результат</h3>
          <pre className="json">{JSON.stringify(job.outputs, null, 2)}</pre>
        </section>
      </div>

      {artifacts.length > 0 && (
        <section>
          <h3>Скриншоты и файлы</h3>
          <div className="artifacts">{artifacts.map((a) => <ArtifactThumb key={a.id} a={a} />)}</div>
        </section>
      )}

      <section>
        <div className="row">
          <h3>Журнал</h3>
          <span className="spacer" />
          <label className="checkbox small">
            <input type="checkbox" checked={showDebug} onChange={(e) => setShowDebug(e.target.checked)} /> отладка
          </label>
        </div>
        <div className="log" ref={logBox}>
          {visible.length === 0 && <div className="muted">Записей пока нет.</div>}
          {visible.map((l) => (
            <div key={l.id} className={`log-line lvl-${l.level}`}>
              <span className="log-ts">{new Date(l.ts).toLocaleTimeString("ru-RU")}</span>
              <span className="log-node">{l.node_id || ""}</span>
              <span className="log-msg">{l.message}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function ArtifactThumb({ a }: { a: ArtifactInfo }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let u: string | null = null;
    fetchBlob(`/api/artifacts/${a.id}`).then((x) => setUrl((u = x))).catch(() => {});
    return () => {
      if (u) URL.revokeObjectURL(u);
    };
  }, [a.id]);
  return (
    <a className="artifact" href={url || undefined} target="_blank" rel="noreferrer" download={a.content_type.startsWith("image/") ? undefined : a.name}>
      {url && a.content_type.startsWith("image/") ? <img src={url} alt={a.name} /> : <div className="file">{a.name}</div>}
      <div className="muted small">{a.name}{a.node_id ? ` · ${a.node_id}` : ""}</div>
    </a>
  );
}
