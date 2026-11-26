import { useEffect, useState } from "react";
import { api, can, Me } from "../api";
import { usePolling } from "../components";

type Conn = { id?: number; name: string; provider: string; provider_title?: string; model: string; base_url: string; folder_id: string; secret_name: string; verify: boolean; is_default: boolean; description: string };
type Provider = { id: string; title: string; default_model: string };

const HINTS: Record<string, { model: string; base: string; folder: string; key: string }> = {
  anthropic: { model: "claude-opus-5-5", base: "пусто — api.anthropic.com", folder: "—", key: "API-ключ Anthropic (sk-ant-…)" },
  yandexgpt: { model: "yandexgpt/latest, yandexgpt-lite/latest", base: "пусто — llm.api.cloud.yandex.net", folder: "ID каталога Yandex Cloud (b1g…)", key: "API-ключ сервисного аккаунта (AQVN…) или IAM-токен" },
  gigachat: { model: "GigaChat-2-Max, GigaChat-2-Pro", base: "пусто — gigachat.devices.sberbank.ru/api/v1", folder: "scope: GIGACHAT_API_PERS / GIGACHAT_API_CORP", key: "Authorization key (Base64) из личного кабинета" },
  openai: { model: "qwen2.5:14b, llama3.1, deepseek-r1…", base: "http://ollama:11434/v1, http://vllm:8000/v1", folder: "—", key: "токен сервера, если нужен" },
};

export default function Models({ me }: { me: Me | null }) {
  const { data, error, reload } = usePolling(() => api<Conn[]>("/api/ai"), 0);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [edit, setEdit] = useState<Conn | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [test, setTest] = useState<Record<number, string>>({});
  const [secrets, setSecrets] = useState<string[]>([]);
  const admin = can(me, "admin");
  useEffect(() => {
    api<Provider[]>("/api/ai/providers").then(setProviders).catch(() => {});
    api<{ name: string }[]>("/api/secrets").then((l) => setSecrets(l.map((s) => s.name))).catch(() => {});
  }, []);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!edit) return;
    try {
      if (edit.id) await api(`/api/ai/${edit.id}`, { method: "PUT", json: edit });
      else await api("/api/ai", { method: "POST", json: edit });
      setEdit(null);
      reload();
    } catch (x) {
      setErr((x as Error).message);
    }
  };
  const runTest = async (c: Conn) => {
    setTest({ ...test, [c.id!]: "проверяю…" });
    const r = await api<{ ok: boolean; answer?: string; error?: string }>(`/api/ai/${c.id}/test`, { method: "POST" });
    setTest((t) => ({ ...t, [c.id!]: r.ok ? `✓ ${r.answer}` : `✗ ${r.error}` }));
  };
  const remove = async (c: Conn) => {
    if (!confirm(`Удалить подключение ${c.name}?`)) return;
    await api(`/api/ai/${c.id}`, { method: "DELETE" });
    reload();
  };
  const hint = edit ? HINTS[edit.provider] : null;
  return (
    <div className="page">
      <header className="page-head">
        <h1>Модели ИИ</h1>
        {admin && <button onClick={() => { setEdit({ name: "", provider: "yandexgpt", model: "", base_url: "", folder_id: "", secret_name: "", verify: true, is_default: !data?.length, description: "" }); setErr(null); }}>Подключить модель</button>}
      </header>
      <p className="muted lead">
        Внешние и локальные модели для блоков «ИИ», OCR через ИИ и генерации процессов по описанию: <b>YandexGPT</b>,
        <b> GigaChat</b>, <b>Claude</b> и любые OpenAI-совместимые серверы внутри контура (Ollama, vLLM, LM Studio).
        Ключ API хранится в разделе «Секреты». Подключение Yandex Cloud используется и блоками Vision OCR, SpeechKit, Переводчик.
      </p>
      {error && <div className="error-box">{error}</div>}
      <div className="cards models">
        {(data || []).map((c) => (
          <div key={c.id} className="card model-card">
            <div className="row">
              <span className={`prov prov-${c.provider}`}>{c.provider_title}</span>
              {c.is_default && <span className="badge status-success">по умолчанию</span>}
            </div>
            <h3 className="code">{c.name}</h3>
            <div className="small">Модель: <b>{c.model}</b></div>
            {c.folder_id && <div className="small muted">Каталог/scope: {c.folder_id}</div>}
            {c.base_url && <div className="small muted">{c.base_url}</div>}
            <div className="small muted">Ключ: {c.secret_name ? <code>{c.secret_name}</code> : "—"}</div>
            {test[c.id!] && <div className="small test-result">{test[c.id!]}</div>}
            {admin && (
              <div className="row end">
                <button className="secondary small" onClick={() => runTest(c)}>Проверить</button>
                <button className="secondary small" onClick={() => { setEdit(c); setErr(null); }}>Изменить</button>
                <button className="danger small" onClick={() => remove(c)}>Удалить</button>
              </div>
            )}
          </div>
        ))}
      </div>
      {data && data.length === 0 && <div className="empty">Подключений нет. Добавьте YandexGPT, GigaChat, Claude или локальную модель.</div>}
      {edit && hint && (
        <div className="modal-backdrop" onClick={() => setEdit(null)}>
          <form className="modal" onSubmit={save} onClick={(e) => e.stopPropagation()}>
            <h3>{edit.id ? `Подключение ${edit.name}` : "Новое подключение"}</h3>
            <label className="field"><span>Имя (для блоков)</span><input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value.replace(/[^\w.\-]/g, "") })} placeholder="yandex" /></label>
            <label className="field">
              <span>Провайдер</span>
              <select value={edit.provider} onChange={(e) => setEdit({ ...edit, provider: e.target.value })}>
                {providers.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
              </select>
            </label>
            <label className="field"><span>Модель</span><input value={edit.model} placeholder={hint.model} onChange={(e) => setEdit({ ...edit, model: e.target.value })} /></label>
            {hint.folder !== "—" && <label className="field"><span>{edit.provider === "gigachat" ? "Scope" : "Каталог (folder_id)"}</span><input value={edit.folder_id} placeholder={hint.folder} onChange={(e) => setEdit({ ...edit, folder_id: e.target.value })} /></label>}
            <label className="field"><span>Адрес API</span><input value={edit.base_url} placeholder={hint.base} onChange={(e) => setEdit({ ...edit, base_url: e.target.value })} /></label>
            <label className="field">
              <span>Секрет с ключом</span>
              <input list="ai-secrets" value={edit.secret_name} placeholder="имя секрета" onChange={(e) => setEdit({ ...edit, secret_name: e.target.value })} />
              <datalist id="ai-secrets">{secrets.map((s) => <option key={s} value={s} />)}</datalist>
              <small className="muted">{hint.key}. Создайте секрет в разделе «Секреты».</small>
            </label>
            <label className="field checkbox"><input type="checkbox" checked={edit.verify} onChange={(e) => setEdit({ ...edit, verify: e.target.checked })} /><span>Проверять TLS-сертификат</span></label>
            <label className="field checkbox"><input type="checkbox" checked={edit.is_default} onChange={(e) => setEdit({ ...edit, is_default: e.target.checked })} /><span>По умолчанию</span></label>
            {err && <div className="error-box">{err}</div>}
            <div className="row end">
              <button type="button" className="secondary" onClick={() => setEdit(null)}>Отмена</button>
              <button type="submit" disabled={!edit.name}>Сохранить</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
