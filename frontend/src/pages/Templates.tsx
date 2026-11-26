import { useMemo, useState } from "react";
import { api } from "../api";
import { navigate } from "../App";
import { usePolling } from "../components";

type Template = {
  id: string;
  name: string;
  category: string;
  description: string;
  minutes_saved: number;
  tags: string[];
  requires: string[];
  blocks: number;
  valid: boolean;
};

export default function Templates() {
  const { data, error } = usePolling(() => api<Template[]>("/api/templates"), 0);
  const [cat, setCat] = useState("Все");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [vibe, setVibe] = useState(false);
  const cats = useMemo(() => ["Все", ...Array.from(new Set((data || []).map((t) => t.category)))], [data]);
  const shown = (data || []).filter(
    (t) =>
      (cat === "Все" || t.category === cat) &&
      (!q || `${t.name} ${t.description} ${t.tags.join(" ")}`.toLowerCase().includes(q.toLowerCase())),
  );

  const use = async (t: Template) => {
    setBusy(t.id);
    try {
      const p = await api<{ id: number }>(`/api/templates/${t.id}/use`, { method: "POST", json: {} });
      navigate(`/designer/${p.id}`);
    } catch (e) {
      alert((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="page wide">
      <header className="page-head">
        <h1>Шаблоны роботов</h1>
        <input className="search" placeholder="Поиск: выписка, ФНС, OCR, Citrix…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button onClick={() => setVibe(true)}>✨ Процесс по описанию</button>
      </header>
      <p className="muted lead">
        Готовые процессы для типовых задач банка и бэк-офиса. Создайте процесс из шаблона и подставьте свои адреса, пути,
        селекторы и имена секретов — граф и логика уже собраны.
      </p>
      <div className="chips">
        {cats.map((c) => (
          <button key={c} className={`chip ${c === cat ? "on" : ""}`} onClick={() => setCat(c)}>
            {c}
            {c !== "Все" && <span className="chip-n">{(data || []).filter((t) => t.category === c).length}</span>}
          </button>
        ))}
      </div>
      {error && <div className="error-box">{error}</div>}
      <div className="tpl-grid">
        {shown.map((t) => (
          <article key={t.id} className="tpl-card">
            <div className="tpl-top">
              <span className="badge">{t.category}</span>
              <span className="muted small">{t.blocks} блоков</span>
            </div>
            <h3>{t.name}</h3>
            <p className="muted">{t.description}</p>
            <div className="tpl-tags">
              {t.tags.map((g) => (
                <span key={g} className="tag">{g}</span>
              ))}
            </div>
            {t.requires.length > 0 && <div className="small muted">Нужно: {t.requires.join(", ")}</div>}
            <div className="tpl-foot">
              <span className="saved" title="Экономия ручного труда за один запуск">
                ⏱ {t.minutes_saved} мин / запуск
              </span>
              <button onClick={() => use(t)} disabled={busy === t.id}>
                {busy === t.id ? "Создаю…" : "Создать процесс"}
              </button>
            </div>
          </article>
        ))}
      </div>
      {vibe && <VibeDialog onClose={() => setVibe(false)} />}
    </div>
  );
}

const EXAMPLES = [
  "Каждое утро скачай курсы ЦБ, запиши в Excel и отправь сводку в Telegram",
  "Прочитай письма от ФНС, сохрани XML-вложения и поставь решения в очередь «Решения ФНС»",
  "Для каждого ИНН из Excel проверь контрольную сумму, запроси ЕГРЮЛ и отметь ликвидированные компании",
];

export function VibeDialog({ onClose }: { onClose: () => void }) {
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ id: number; errors: string[] }>("/api/vibe", { method: "POST", json: { prompt } });
      navigate(`/designer/${r.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal wide" onSubmit={submit} onClick={(e) => e.stopPropagation()}>
        <h3>✨ Процесс по описанию</h3>
        <p className="muted small">
          Опишите задачу своими словами — модель ИИ (Claude, YandexGPT, GigaChat или локальная — раздел «Модели ИИ»)
          соберёт схему из блоков платформы. Схема откроется в дизайнере: проверьте параметры и запустите.
        </p>
        <textarea autoFocus rows={5} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Что должен делать робот?" />
        <div className="presets">
          {EXAMPLES.map((x) => (
            <button type="button" key={x} className="link small" onClick={() => setPrompt(x)}>
              {x}
            </button>
          ))}
        </div>
        {error && <div className="error-box">{error}</div>}
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Отмена
          </button>
          <button type="submit" disabled={busy || prompt.trim().length < 5}>
            {busy ? "Модель думает…" : "Создать схему"}
          </button>
        </div>
      </form>
    </div>
  );
}
