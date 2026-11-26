export type ParamDef = {
  name: string;
  label: string;
  kind: "string" | "text" | "code" | "expression" | "number" | "boolean" | "select" | "variable" | "secret";
  required: boolean;
  default: unknown;
  options: string[] | null;
  help: string;
};

export type BlockDef = {
  type: string;
  title: string;
  category: string;
  description: string;
  params: ParamDef[];
  outputs: { name: string; label: string }[];
  error_output: boolean;
};

export type ProcessNode = {
  id: string;
  type: string;
  label?: string;
  params: Record<string, unknown>;
  position?: { x: number; y: number };
};

export type ProcessEdge = { id?: string; source: string; sourceHandle?: string; target: string };

export type Definition = {
  variables: { name: string; default: unknown }[];
  outputs: string[];
  nodes: ProcessNode[];
  edges: ProcessEdge[];
};

export type Process = {
  id: number;
  name: string;
  description: string;
  version: number;
  minutes_saved?: number;
  updated_at: string;
  definition?: Definition;
};

export type Job = {
  id: number;
  process_id: number | null;
  process_name: string;
  process_version: number;
  status: "queued" | "running" | "success" | "failed" | "cancelled";
  error: string | null;
  failed_node: string | null;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  required_tags: string[];
  robot_name: string | null;
  executed_by: string | null;
  source: string;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type Robot = {
  id: number;
  name: string;
  hostname: string;
  os: string;
  tags: string[];
  version: string;
  current_job_id: number | null;
  online: boolean;
  last_seen: string;
};

export type Schedule = {
  id: number;
  process_id: number;
  process_name: string | null;
  cron: string;
  enabled: boolean;
  inputs: Record<string, unknown>;
  tags: string[];
  robot: string | null;
  next_run_at: string | null;
  last_run_at: string | null;
};

export type LogLine = { id: number; ts: string; level: string; node_id: string | null; message: string };
export type ArtifactInfo = { id: number; name: string; content_type: string; node_id: string | null; created_at: string };

export type Me = { username: string; role: string; user_id: number | null };
export const ROLE_LABEL: Record<string, string> = {
  admin: "Администратор",
  developer: "Разработчик",
  operator: "Оператор",
  viewer: "Наблюдатель",
  robot: "Робот",
};
const ROLE_ORDER = ["viewer", "operator", "developer", "admin"];
export function can(me: Me | null, role: string): boolean {
  if (!me) return false;
  return ROLE_ORDER.indexOf(me.role) >= ROLE_ORDER.indexOf(role);
}

export function download(name: string, data: unknown) {
  const blob = new Blob([typeof data === "string" ? data : JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
}

const TOKEN_KEY = "rpa-token";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setToken(token: string) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* хранилище недоступно — токен живёт до перезагрузки */
  }
  memoryToken = token;
}

let memoryToken = getToken();

export class ApiError extends Error {
  constructor(public status: number, message: string, public details?: unknown) {
    super(message);
  }
}

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (memoryToken) headers.Authorization = `Bearer ${memoryToken}`;
  let body = init.body;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const res = await fetch(path, { ...init, headers, body });
  if (res.status === 401) {
    onUnauthorized();
    throw new ApiError(401, "Требуется вход");
  }
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* тело не JSON */
    }
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "message" in detail
          ? String((detail as { message: string }).message)
          : `Ошибка ${res.status}`;
    throw new ApiError(res.status, message, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export async function fetchBlob(path: string): Promise<string> {
  const res = await fetch(path, { headers: memoryToken ? { Authorization: `Bearer ${memoryToken}` } : {} });
  if (!res.ok) throw new ApiError(res.status, "Не удалось загрузить файл");
  return URL.createObjectURL(await res.blob());
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "medium" });
}

export function duration(a: string | null, b: string | null): string {
  if (!a) return "—";
  const ms = (b ? new Date(b).getTime() : Date.now()) - new Date(a).getTime();
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 60 ? `${s} с` : `${Math.floor(s / 60)} мин ${s % 60} с`;
}

export const STATUS_LABEL: Record<string, string> = {
  queued: "В очереди",
  running: "Выполняется",
  success: "Успешно",
  failed: "Ошибка",
  cancelled: "Отменён",
};
