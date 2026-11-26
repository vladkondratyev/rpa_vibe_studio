import { useEffect, useState } from "react";
import { api, can, Me, ROLE_LABEL, setToken, setUnauthorizedHandler } from "./api";
import { Logo } from "./components";
import Analytics from "./pages/Analytics";
import Audit from "./pages/Audit";
import Designer from "./pages/Designer";
import JobDetail from "./pages/JobDetail";
import Jobs from "./pages/Jobs";
import Models from "./pages/Models";
import OcrTemplates from "./pages/OcrTemplates";
import Processes from "./pages/Processes";
import Queues from "./pages/Queues";
import Robots from "./pages/Robots";
import Schedules from "./pages/Schedules";
import Secrets from "./pages/Secrets";
import Templates from "./pages/Templates";
import Users from "./pages/Users";

function useHash(): string {
  const [hash, setHash] = useState(window.location.hash || "#/processes");
  useEffect(() => {
    const on = () => setHash(window.location.hash || "#/processes");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

export function navigate(path: string) {
  window.location.hash = path;
}

type NavItem = { href: string; label: string; match: string[]; role?: string };
const NAV: { title: string; items: NavItem[] }[] = [
  {
    title: "Автоматизация",
    items: [
      { href: "#/processes", label: "Процессы", match: ["#/processes", "#/designer"] },
      { href: "#/templates", label: "Шаблоны", match: ["#/templates"] },
      { href: "#/jobs", label: "Запуски", match: ["#/jobs"] },
      { href: "#/schedules", label: "Расписания", match: ["#/schedules"] },
      { href: "#/queues", label: "Очереди", match: ["#/queues"] },
    ],
  },
  {
    title: "Эксплуатация",
    items: [
      { href: "#/analytics", label: "Аналитика", match: ["#/analytics"] },
      { href: "#/robots", label: "Роботы", match: ["#/robots"] },
    ],
  },
  {
    title: "Настройки",
    items: [
      { href: "#/ocr", label: "OCR-шаблоны", match: ["#/ocr"], role: "developer" },
      { href: "#/models", label: "Модели ИИ", match: ["#/models"], role: "viewer" },
      { href: "#/secrets", label: "Секреты", match: ["#/secrets"], role: "developer" },
      { href: "#/users", label: "Пользователи", match: ["#/users"], role: "admin" },
      { href: "#/audit", label: "Аудит", match: ["#/audit"], role: "admin" },
    ],
  },
];

export default function App() {
  const hash = useHash();
  const [needLogin, setNeedLogin] = useState(false);
  const [me, setMe] = useState<Me | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => setUnauthorizedHandler(() => setNeedLogin(true)), []);
  useEffect(() => {
    api<Me>("/api/auth/me").then(setMe).catch(() => {});
  }, [reloadKey]);

  const [path, query] = hash.split("?");
  const parts = path.replace(/^#\//, "").split("/");
  const params = new URLSearchParams(query || "");

  let page;
  if (parts[0] === "designer" && parts[1]) {
    page = <Designer key={parts[1]} processId={Number(parts[1])} focusNode={params.get("node")} />;
  } else if (parts[0] === "jobs" && parts[1]) {
    page = <JobDetail key={parts[1]} jobId={Number(parts[1])} />;
  } else if (parts[0] === "jobs") {
    page = <Jobs processId={params.get("process") ? Number(params.get("process")) : undefined} />;
  } else if (parts[0] === "robots") page = <Robots />;
  else if (parts[0] === "schedules") page = <Schedules />;
  else if (parts[0] === "templates") page = <Templates />;
  else if (parts[0] === "queues") page = <Queues queueId={parts[1] ? Number(parts[1]) : undefined} />;
  else if (parts[0] === "analytics") page = <Analytics />;
  else if (parts[0] === "secrets") page = <Secrets />;
  else if (parts[0] === "models") page = <Models me={me} />;
  else if (parts[0] === "ocr") page = <OcrTemplates />;
  else if (parts[0] === "users") page = <Users />;
  else if (parts[0] === "audit") page = <Audit />;
  else page = <Processes />;

  const logout = () => {
    setToken("");
    setMe(null);
    setNeedLogin(true);
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <a className="brand" href="#/processes">
          <Logo size={30} />
          <span>
            RPA Vibe <span className="brand-mark">Studio</span>
          </span>
        </a>
        <nav>
          {NAV.map((g) => (
            <div key={g.title} className="nav-group">
              <div className="nav-title">{g.title}</div>
              {g.items
                .filter((n) => !n.role || !me || can(me, n.role))
                .map((n) => (
                  <a key={n.href} href={n.href} className={n.match.some((m) => path.startsWith(m)) ? "active" : ""}>
                    {n.label}
                  </a>
                ))}
            </div>
          ))}
        </nav>
        <div className="me">
          {me && (
            <div className="small">
              <div className="strong">{me.username}</div>
              <div className="muted-dark">{ROLE_LABEL[me.role] || me.role}</div>
            </div>
          )}
          <button className="link small" onClick={logout}>
            {me ? "Выйти" : "Войти"}
          </button>
        </div>
      </aside>
      <main className="main" key={reloadKey}>
        {page}
      </main>
      {needLogin && (
        <LoginDialog
          onDone={() => {
            setNeedLogin(false);
            setReloadKey((k) => k + 1);
          }}
          onCancel={() => setNeedLogin(false)}
        />
      )}
    </div>
  );
}

function LoginDialog({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [mode, setMode] = useState<"password" | "token">("password");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [token, setTokenInput] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (mode === "token") {
      setToken(token.trim());
      onDone();
      return;
    }
    try {
      setToken("");
      const r = await api<{ token: string }>("/api/auth/login", { method: "POST", json: { username, password } });
      setToken(r.token);
      onDone();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="modal-backdrop login-backdrop">
      <form className="modal login" onSubmit={submit}>
        <div className="login-logo">
          <Logo size={64} />
          <h3>RPA Vibe Studio</h3>
        </div>
        <div className="tabs-inline">
          <button type="button" className={mode === "password" ? "on" : ""} onClick={() => setMode("password")}>
            Логин и пароль
          </button>
          <button type="button" className={mode === "token" ? "on" : ""} onClick={() => setMode("token")}>
            Токен
          </button>
        </div>
        {mode === "password" ? (
          <>
            <label className="field">
              <span>Логин</span>
              <input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
            </label>
            <label className="field">
              <span>Пароль</span>
              <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <p className="muted small">Доменная учётная запись работает, если оркестратор подключён к LDAP / Active Directory.</p>
          </>
        ) : (
          <label className="field">
            <span>Токен доступа</span>
            <input autoFocus type="password" value={token} onChange={(e) => setTokenInput(e.target.value)} />
            <small className="muted">RPA_TOKEN оркестратора или персональный API-токен.</small>
          </label>
        )}
        {error && <div className="error-box">{error}</div>}
        <div className="row end">
          <button type="button" className="secondary" onClick={onCancel}>
            Отмена
          </button>
          <button type="submit">Войти</button>
        </div>
      </form>
    </div>
  );
}
