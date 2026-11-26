import { useState } from "react";
import { api } from "../api";
import { usePolling } from "../components";

type Day = { date: string; success: number; failed: number; cancelled: number };
type ProcStat = { name: string; process_id: number | null; runs: number; success: number; failed: number; success_rate: number | null; avg_seconds: number | null; hours_saved: number };
type RobotStat = { name: string; runs: number; busy_seconds: number; utilization: number };
type Data = {
  days: number;
  totals: { runs: number; success: number; failed: number; success_rate: number | null; hours_saved: number; fte_saved: number; money_saved: number | null };
  daily: Day[];
  processes: ProcStat[];
  robots: RobotStat[];
  queues: { id: number; name: string; counts: Record<string, number>; overdue: number }[];
};

const SERIES: { key: keyof Omit<Day, "date">; label: string; color: string }[] = [
  { key: "success", label: "Успешно", color: "var(--ok)" },
  { key: "failed", label: "Ошибка", color: "var(--danger)" },
  { key: "cancelled", label: "Отменён", color: "#9ca3af" },
];

const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 1000) / 10}%`);
const num = (v: number) => v.toLocaleString("ru-RU");

export default function Analytics() {
  const [days, setDays] = useState(30);
  const { data, error } = usePolling(() => api<Data>(`/api/analytics?days=${days}`), 30000, [days]);
  return (
    <div className="page wide">
      <header className="page-head">
        <h1>Аналитика</h1>
        <div className="seg">
          {[7, 30, 90].map((d) => (
            <button key={d} className={d === days ? "on" : ""} onClick={() => setDays(d)}>
              {d} дней
            </button>
          ))}
        </div>
      </header>
      {error && <div className="error-box">{error}</div>}
      {data && (
        <>
          <div className="kpis">
            <Kpi label="Сэкономлено часов" value={num(data.totals.hours_saved)} hint={`≈ ${data.totals.fte_saved} FTE за период`} accent />
            {data.totals.money_saved != null && <Kpi label="Экономия, ₽" value={num(data.totals.money_saved)} hint="по ставке RPA_HOUR_COST" />}
            <Kpi label="Запусков" value={num(data.totals.runs)} hint={`${num(data.totals.success)} успешно`} />
            <Kpi label="Успешность" value={pct(data.totals.success_rate)} hint={`${num(data.totals.failed)} с ошибкой`} />
          </div>
          <section className="panel">
            <h3>Запуски по дням</h3>
            <DailyChart daily={data.daily} />
          </section>
          <div className="grid2">
            <section className="panel">
              <h3>Процессы: сэкономленные часы</h3>
              <Bars rows={data.processes.slice(0, 10).map((p) => ({ label: p.name, value: p.hours_saved, note: `${p.runs} запусков · ${pct(p.success_rate)}` }))} unit="ч" />
              {data.processes.length === 0 && <div className="muted small">Нет запусков за период.</div>}
              <p className="muted small">Укажите в настройках процесса «экономию ручного труда за запуск» — так считаются часы и FTE.</p>
            </section>
            <section className="panel">
              <h3>Загрузка роботов</h3>
              <Bars rows={data.robots.map((r) => ({ label: r.name, value: Math.round(r.utilization * 1000) / 10, note: `${r.runs} запусков · ${Math.round(r.busy_seconds / 3600)} ч работы` }))} unit="%" max={100} />
              {data.robots.length === 0 && <div className="muted small">Роботы не подключены.</div>}
            </section>
          </div>
          <section className="panel">
            <h3>Процессы</h3>
            <table className="table">
              <thead><tr><th>Процесс</th><th>Запусков</th><th>Успешно</th><th>Ошибок</th><th>Успешность</th><th>Среднее время</th><th>Часов сэкономлено</th></tr></thead>
              <tbody>
                {data.processes.map((p) => (
                  <tr key={p.name}>
                    <td className="strong">{p.process_id ? <a href={`#/jobs?process=${p.process_id}`}>{p.name}</a> : p.name}</td>
                    <td>{p.runs}</td><td>{p.success}</td><td>{p.failed}</td><td>{pct(p.success_rate)}</td>
                    <td>{p.avg_seconds == null ? "—" : p.avg_seconds < 60 ? `${p.avg_seconds} с` : `${Math.round(p.avg_seconds / 6) / 10} мин`}</td>
                    <td>{p.hours_saved}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          {data.queues.length > 0 && (
            <section className="panel">
              <h3>Очереди</h3>
              <table className="table">
                <thead><tr><th>Очередь</th><th>Новые</th><th>В работе</th><th>Обработано</th><th>Ошибки</th><th>Просрочено SLA</th></tr></thead>
                <tbody>
                  {data.queues.map((q) => (
                    <tr key={q.id} className="clickable" onClick={() => (window.location.hash = `/queues/${q.id}`)}>
                      <td className="strong">{q.name}</td><td>{q.counts.new}</td><td>{q.counts.in_progress}</td><td>{q.counts.done}</td><td>{q.counts.failed}</td>
                      <td>{q.overdue ? <span className="badge status-failed">{q.overdue}</span> : 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function Kpi({ label, value, hint, accent }: { label: string; value: string; hint?: string; accent?: boolean }) {
  return (
    <div className={`kpi ${accent ? "accent" : ""}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {hint && <div className="kpi-hint">{hint}</div>}
    </div>
  );
}

function DailyChart({ daily }: { daily: Day[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 900, H = 220, padL = 34, padB = 26, padT = 10;
  const max = Math.max(1, ...daily.map((d) => d.success + d.failed + d.cancelled));
  const step = (W - padL) / daily.length;
  const bw = Math.max(3, Math.min(26, step - 4));
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / max);
  const ticks = [0, Math.ceil(max / 2), max];
  const every = Math.ceil(daily.length / 10);
  return (
    <div className="chart">
      <div className="legend">
        {SERIES.map((s) => (
          <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg" onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W} y1={y(t)} y2={y(t)} className="grid-line" />
            <text x={padL - 6} y={y(t) + 4} className="axis" textAnchor="end">{t}</text>
          </g>
        ))}
        {daily.map((d, i) => {
          const x = padL + i * step + (step - bw) / 2;
          let acc = 0;
          const segs = SERIES.filter((s) => d[s.key] > 0);
          return (
            <g key={d.date} onMouseEnter={() => setHover(i)}>
              <rect x={padL + i * step} y={padT} width={step} height={H - padT - padB} fill="transparent" />
              {segs.map((s, si) => {
                const v = d[s.key];
                const top = y(acc + v);
                const h = y(acc) - top - (si > 0 ? 2 : 0);
                acc += v;
                const last = si === segs.length - 1;
                return (
                  <path
                    key={s.key}
                    d={roundedTop(x, top, bw, Math.max(1, h), last ? Math.min(4, bw / 2) : 0)}
                    fill={s.color}
                    opacity={hover === null || hover === i ? 1 : 0.45}
                  />
                );
              })}
              {i % every === 0 && (
                <text x={x + bw / 2} y={H - 8} className="axis" textAnchor="middle">
                  {d.date.slice(8, 10)}.{d.date.slice(5, 7)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: `${((padL + hover * step + step / 2) / W) * 100}%` }}>
          <b>{new Date(daily[hover].date).toLocaleDateString("ru-RU")}</b>
          {SERIES.map((s) => (
            <div key={s.key}><i style={{ background: s.color }} />{s.label}: {daily[hover][s.key]}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function roundedTop(x: number, y: number, w: number, h: number, r: number): string {
  if (r <= 0) return `M${x},${y}h${w}v${h}h${-w}z`;
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}z`;
}

function Bars({ rows, unit, max }: { rows: { label: string; value: number; note: string }[]; unit: string; max?: number }) {
  const top = max ?? Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="hbars">
      {rows.map((r) => (
        <div key={r.label} className="hbar" title={`${r.label}: ${r.value} ${unit} · ${r.note}`}>
          <div className="hbar-label">
            <span className="ellipsis">{r.label}</span>
            <b>{r.value} {unit}</b>
          </div>
          <div className="hbar-track"><div className="hbar-fill" style={{ width: `${Math.max(1, (r.value / top) * 100)}%` }} /></div>
          <div className="muted small">{r.note}</div>
        </div>
      ))}
    </div>
  );
}
