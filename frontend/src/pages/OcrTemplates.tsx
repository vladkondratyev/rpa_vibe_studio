import { useRef, useState } from "react";
import { api } from "../api";
import { usePolling } from "../components";

type Field = {
  name: string; label?: string; required?: boolean; type?: string; validate?: string;
  anchor?: string; anchor_position?: string; regex?: string; zone?: number[]; yandex_entity?: string;
};
type Tpl = { id: string; name: string; category?: string; keywords?: string[]; yandex_model?: string; mrz?: boolean; fields: Field[] };
type Row = { id: string; builtin: boolean; customized: boolean; definition: Tpl };
type Word = { text: string; x: number; y: number; w: number; h: number };
type TestResult = {
  text: string; words: Word[]; entities: Record<string, string>;
  extracted?: { fields: Record<string, unknown>; sources: Record<string, string>; errors: Record<string, string>; missing: string[]; ok: boolean };
  classified?: { id: string; score: number } | null;
};

const TYPES = ["text", "date", "amount", "digits", "upper", "name"];
const CHECKS = ["", "inn", "snils", "ogrn", "kpp", "bik", "account", "passport"];
const SOURCE: Record<string, string> = { yandex: "Yandex Vision", mrz: "MRZ", zone: "зона", anchor: "подпись", regex: "шаблон", ai: "ИИ" };

export default function OcrTemplates() {
  const { data, error, reload } = usePolling(() => api<Row[]>("/api/ocr/templates"), 0);
  const [sel, setSel] = useState<Tpl | null>(null);
  const [selBuiltin, setSelBuiltin] = useState(false);
  const [raw, setRaw] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const open = (r: Row) => {
    setSel(JSON.parse(JSON.stringify(r.definition)));
    setSelBuiltin(r.builtin);
    setMsg(null);
  };
  const save = async () => {
    if (!sel) return;
    try {
      await api(`/api/ocr/templates/${sel.id}`, { method: "PUT", json: { definition: sel } });
      setMsg("Сохранено. Роботы получат шаблон со следующей задачей.");
      reload();
    } catch (e) {
      setMsg((e as Error).message);
    }
  };
  const reset = async () => {
    if (!sel || !confirm(selBuiltin ? "Вернуть встроенный шаблон к исходному?" : "Удалить шаблон?")) return;
    await api(`/api/ocr/templates/${sel.id}`, { method: "DELETE" });
    setSel(null);
    reload();
  };
  const create = () => {
    const id = prompt("id шаблона (латиница, например contract_loan)")?.trim();
    if (!id) return;
    setSel({ id, name: "Новый документ", category: "Свои документы", keywords: [], fields: [{ name: "number", label: "Номер", required: true, regex: "№\\s*(\\S+)" }] });
    setSelBuiltin(false);
  };

  return (
    <div className="page wide">
      <header className="page-head">
        <h1>OCR-шаблоны документов</h1>
        <button onClick={create}>Новый шаблон</button>
      </header>
      <p className="muted lead">
        Встроенный модуль распознавания: Tesseract (локально), Yandex Vision OCR или мультимодальная модель ИИ. Шаблон
        описывает, где искать каждое поле: зона на странице, подпись рядом («Фамилия»), регулярное выражение, поле
        модели Yandex Vision или машиночитаемая зона паспорта; ИНН, СНИЛС, ОГРН и ключ счёта проверяются контрольными
        суммами. Блок «Извлечь поля по шаблону» отправит документ на ручную проверку, если что-то не сошлось.
      </p>
      {error && <div className="error-box">{error}</div>}
      <div className="ocr-layout">
        <aside className="ocr-list">
          {(data || []).map((r) => (
            <button key={r.id} className={`ocr-item ${sel?.id === r.id ? "on" : ""}`} onClick={() => open(r)}>
              <div className="strong">{r.definition.name}</div>
              <div className="small muted">
                {r.id} · {r.definition.fields.length} полей {r.builtin ? (r.customized ? "· изменён" : "· встроенный") : "· свой"}
              </div>
            </button>
          ))}
        </aside>
        {sel ? (
          <section className="ocr-editor">
            <div className="row">
              <h2>{sel.name}</h2>
              <span className="spacer" />
              <button className="secondary small" onClick={() => setRaw(!raw)}>{raw ? "Форма" : "JSON"}</button>
              <button className="danger small" onClick={reset}>{selBuiltin ? "Сбросить" : "Удалить"}</button>
              <button onClick={save}>Сохранить</button>
            </div>
            {msg && <div className="muted small">{msg}</div>}
            {raw ? (
              <RawEditor tpl={sel} onChange={setSel} />
            ) : (
              <TemplateForm tpl={sel} onChange={setSel} />
            )}
            <Tester tpl={sel} onZone={(i, z) => setSel({ ...sel, fields: sel.fields.map((f, j) => (j === i ? { ...f, zone: z } : f)) })} />
          </section>
        ) : (
          <div className="empty">Выберите шаблон слева или создайте свой.</div>
        )}
      </div>
    </div>
  );
}

function RawEditor({ tpl, onChange }: { tpl: Tpl; onChange: (t: Tpl) => void }) {
  const [text, setText] = useState(JSON.stringify(tpl, null, 2));
  const [err, setErr] = useState<string | null>(null);
  return (
    <>
      <textarea className="code" rows={18} value={text} onChange={(e) => {
        setText(e.target.value);
        try { onChange(JSON.parse(e.target.value)); setErr(null); } catch (x) { setErr((x as Error).message); }
      }} />
      {err && <div className="error-box">{err}</div>}
    </>
  );
}

function TemplateForm({ tpl, onChange }: { tpl: Tpl; onChange: (t: Tpl) => void }) {
  const setF = (i: number, patch: Partial<Field>) => onChange({ ...tpl, fields: tpl.fields.map((f, j) => (j === i ? { ...f, ...patch } : f)) });
  return (
    <>
      <div className="grid2">
        <label className="field"><span>Название</span><input value={tpl.name} onChange={(e) => onChange({ ...tpl, name: e.target.value })} /></label>
        <label className="field"><span>Ключевые слова для определения типа</span><input value={(tpl.keywords || []).join(", ")} onChange={(e) => onChange({ ...tpl, keywords: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></label>
        <label className="field">
          <span>Модель Yandex Vision</span>
          <select value={tpl.yandex_model || ""} onChange={(e) => onChange({ ...tpl, yandex_model: e.target.value || undefined })}>
            {["", "page", "passport", "driver-license-front", "driver-license-back", "vehicle-registration-front", "table", "handwritten"].map((m) => <option key={m} value={m}>{m || "page (текст)"}</option>)}
          </select>
        </label>
        <label className="field checkbox"><input type="checkbox" checked={!!tpl.mrz} onChange={(e) => onChange({ ...tpl, mrz: e.target.checked })} /><span>Разбирать MRZ паспорта РФ</span></label>
      </div>
      <h4>Поля</h4>
      <div className="fields-table">
        <div className="ft-head"><span>Поле</span><span>Подпись</span><span>Рег. выражение</span><span>Тип</span><span>Проверка</span><span>Зона</span><span /></div>
        {tpl.fields.map((f, i) => (
          <div key={i} className="ft-row">
            <span>
              <input className="code" value={f.name} onChange={(e) => setF(i, { name: e.target.value.replace(/[^\w]/g, "") })} />
              <input placeholder="название" value={f.label || ""} onChange={(e) => setF(i, { label: e.target.value })} />
              <label className="small"><input type="checkbox" checked={!!f.required} onChange={(e) => setF(i, { required: e.target.checked })} /> обязательное</label>
            </span>
            <span>
              <input placeholder="напр. Фамилия" value={f.anchor || ""} onChange={(e) => setF(i, { anchor: e.target.value || undefined })} />
              <select value={f.anchor_position || "right"} onChange={(e) => setF(i, { anchor_position: e.target.value })}>
                <option value="right">справа</option><option value="below">ниже</option><option value="above">выше</option>
              </select>
            </span>
            <span><input className="code" placeholder="ИНН\s*(\d{10})" value={f.regex || ""} onChange={(e) => setF(i, { regex: e.target.value || undefined })} /></span>
            <span><select value={f.type || "text"} onChange={(e) => setF(i, { type: e.target.value })}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select></span>
            <span><select value={f.validate || ""} onChange={(e) => setF(i, { validate: e.target.value || undefined })}>{CHECKS.map((t) => <option key={t} value={t}>{t || "—"}</option>)}</select></span>
            <span className="small code">{f.zone ? f.zone.map((v) => v.toFixed(2)).join(", ") : "—"}{f.zone && <button className="link small" onClick={() => setF(i, { zone: undefined })}>✕</button>}</span>
            <span><button className="link" title="Удалить поле" onClick={() => onChange({ ...tpl, fields: tpl.fields.filter((_, j) => j !== i) })}>✕</button></span>
          </div>
        ))}
      </div>
      <button className="secondary small" onClick={() => onChange({ ...tpl, fields: [...tpl.fields, { name: `field${tpl.fields.length + 1}`, label: "" }] })}>+ Поле</button>
    </>
  );
}

function Tester({ tpl, onZone }: { tpl: Tpl; onZone: (i: number, z: number[]) => void }) {
  const [img, setImg] = useState<string | null>(null);
  const [b64, setB64] = useState<string | null>(null);
  const [engine, setEngine] = useState("tesseract");
  const [res, setRes] = useState<TestResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [drawFor, setDrawFor] = useState<number | null>(null);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);

  const load = (file: File) => {
    const r = new FileReader();
    r.onload = () => {
      const url = String(r.result);
      setB64(url);
      setImg(file.type === "application/pdf" ? null : url);
      setRes(null);
    };
    r.readAsDataURL(file);
  };
  const run = async () => {
    if (!b64) return;
    setBusy(true);
    setErr(null);
    try {
      setRes(await api<TestResult>("/api/ocr/test", { method: "POST", json: { file_b64: b64, template: tpl, engine } }));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const rel = (e: React.MouseEvent) => {
    const r = box.current!.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  };
  return (
    <div className="tester">
      <h4>Проверка на образце</h4>
      <div className="row">
        <label className="button secondary small">
          Загрузить скан (JPG, PNG, PDF)
          <input type="file" accept="image/*,application/pdf" hidden onChange={(e) => e.target.files?.[0] && load(e.target.files[0])} />
        </label>
        <select className="auto" value={engine} onChange={(e) => setEngine(e.target.value)}>
          <option value="tesseract">Tesseract (локально)</option>
          <option value="yandex">Yandex Vision OCR</option>
          <option value="ai">Модель ИИ</option>
        </select>
        <button className="small" disabled={!b64 || busy} onClick={run}>{busy ? "Распознаю…" : "Распознать"}</button>
        {img && (
          <select className="auto" value={drawFor ?? ""} onChange={(e) => setDrawFor(e.target.value === "" ? null : Number(e.target.value))}>
            <option value="">Нарисовать зону для поля…</option>
            {tpl.fields.map((f, i) => <option key={i} value={i}>{f.label || f.name}</option>)}
          </select>
        )}
      </div>
      {err && <div className="error-box">{err}</div>}
      <div className="tester-body">
        {img && (
          <div
            ref={box}
            className={`sample ${drawFor !== null ? "drawing" : ""}`}
            onMouseDown={(e) => { if (drawFor === null) return; const p = rel(e); setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y }); }}
            onMouseMove={(e) => { if (!drag) return; const p = rel(e); setDrag({ ...drag, x1: p.x, y1: p.y }); }}
            onMouseUp={() => {
              if (drag && drawFor !== null) {
                const z = [Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1), Math.abs(drag.x1 - drag.x0), Math.abs(drag.y1 - drag.y0)];
                if (z[2] > 0.01 && z[3] > 0.005) onZone(drawFor, z.map((v) => Math.round(v * 1000) / 1000));
              }
              setDrag(null);
              setDrawFor(null);
            }}
          >
            <img src={img} alt="образец" draggable={false} />
            {res?.words.map((w, i) => (
              <div key={i} className="word-box" title={w.text} style={{ left: `${w.x * 100}%`, top: `${w.y * 100}%`, width: `${w.w * 100}%`, height: `${w.h * 100}%` }} />
            ))}
            {tpl.fields.filter((f) => f.zone).map((f) => (
              <div key={f.name} className="zone-box" style={{ left: `${f.zone![0] * 100}%`, top: `${f.zone![1] * 100}%`, width: `${f.zone![2] * 100}%`, height: `${f.zone![3] * 100}%` }}>
                <span>{f.label || f.name}</span>
              </div>
            ))}
            {drag && (
              <div className="zone-box new" style={{ left: `${Math.min(drag.x0, drag.x1) * 100}%`, top: `${Math.min(drag.y0, drag.y1) * 100}%`, width: `${Math.abs(drag.x1 - drag.x0) * 100}%`, height: `${Math.abs(drag.y1 - drag.y0) * 100}%` }} />
            )}
          </div>
        )}
        {res && (
          <div className="tester-result">
            {res.extracted && (
              <>
                <div className={`badge ${res.extracted.ok ? "status-success" : "status-failed"}`}>{res.extracted.ok ? "Все обязательные поля распознаны" : "Нужна проверка"}</div>
                <table className="table compact">
                  <tbody>
                    {tpl.fields.map((f) => (
                      <tr key={f.name} className={res.extracted!.errors[f.name] || res.extracted!.missing.includes(f.name) ? "warn-row" : ""}>
                        <td>{f.label || f.name}</td>
                        <td className="strong">{String(res.extracted!.fields[f.name] ?? "—")}</td>
                        <td className="small muted">{SOURCE[res.extracted!.sources[f.name]] || ""}{res.extracted!.errors[f.name] ? ` · ${res.extracted!.errors[f.name]}` : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
            <details>
              <summary>Распознанный текст</summary>
              <pre className="json">{res.text}</pre>
            </details>
          </div>
        )}
      </div>
    </div>
  );
}
