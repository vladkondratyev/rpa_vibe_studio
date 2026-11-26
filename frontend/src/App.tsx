import { useEffect, useState } from "react";
import { setToken, setUnauthorizedHandler } from "./api";
import Designer from "./pages/Designer";
import JobDetail from "./pages/JobDetail";
import Jobs from "./pages/Jobs";
import Processes from "./pages/Processes";
import Robots from "./pages/Robots";
import Schedules from "./pages/Schedules";

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

const NAV = [
  { href: "#/processes", label: "Процессы", match: ["#/processes", "#/designer"] },
  { href: "#/jobs", label: "Запуски", match: ["#/jobs"] },
  { href: "#/robots", label: "Роботы", match: ["#/robots"] },
  { href: "#/schedules", label: "Расписания", match: ["#/schedules"] },
];

export default function App() {
  const hash = useHash();
  const [needToken, setNeedToken] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => setUnauthorizedHandler(() => setNeedToken(true)), []);

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
  } else if (parts[0] === "robots") {
    page = <Robots />;
  } else if (parts[0] === "schedules") {
    page = <Schedules />;
  } else {
    page = <Processes />;
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">◆</span> RPA Vibe Studio
        </div>
        <nav>
          {NAV.map((n) => (
            <a key={n.href} href={n.href} className={n.match.some((m) => path.startsWith(m)) ? "active" : ""}>
              {n.label}
            </a>
          ))}
        </nav>
        <button className="link small" onClick={() => setNeedToken(true)}>
          Токен доступа
        </button>
      </aside>
      <main className="main" key={reloadKey}>
        {page}
      </main>
      {needToken && (
        <div className="modal-backdrop">
          <form
            className="modal"
            onSubmit={(e) => {
              e.preventDefault();
              setToken(tokenInput.trim());
              setNeedToken(false);
              setReloadKey((k) => k + 1);
            }}
          >
            <h3>Токен доступа</h3>
            <p className="muted">Значение переменной RPA_TOKEN, заданной при запуске оркестратора.</p>
            <input autoFocus type="password" value={tokenInput} onChange={(e) => setTokenInput(e.target.value)} />
            <div className="row end">
              <button type="button" className="secondary" onClick={() => setNeedToken(false)}>
                Отмена
              </button>
              <button type="submit">Сохранить</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
