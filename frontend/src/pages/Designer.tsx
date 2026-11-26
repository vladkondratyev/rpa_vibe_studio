import {
  Background,
  Connection,
  Controls,
  Edge,
  Handle,
  MiniMap,
  Node,
  NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, BlockDef, Definition, ParamDef, Process } from "../api";
import { navigate } from "../App";
import { parseValue, RunDialog, showValue } from "../components";

type BlockData = { blockType: string; params: Record<string, unknown>; label?: string; invalid?: boolean };
type BlockNode = Node<BlockData, "block">;

const CatalogCtx = createContext<Record<string, BlockDef>>({});

const CATEGORY_COLORS: Record<string, string> = {
  Логика: "#6366f1",
  Web: "#0ea5e9",
  Excel: "#16a34a",
  Файлы: "#d97706",
};

let idCounter = 0;
const newId = () => `n${Date.now().toString(36)}${(idCounter++).toString(36)}`;

function edgeStyle(handle?: string | null) {
  return handle === "error"
    ? { style: { stroke: "#dc2626", strokeDasharray: "5 4" }, animated: false }
    : { style: { stroke: "#64748b" } };
}

/** Короткое описание блока на холсте: значение первого обязательного параметра. */
function summary(def: BlockDef | undefined, params: Record<string, unknown>): string {
  if (!def) return "";
  const p = def.params.find((x) => x.required && x.kind !== "code") || def.params[0];
  if (!p) return "";
  const v = params[p.name] ?? p.default;
  const s = showValue(v);
  return s ? `${p.label}: ${s.length > 38 ? s.slice(0, 38) + "…" : s}` : "";
}

function BlockNodeView({ data, selected }: NodeProps<BlockNode>) {
  const catalog = useContext(CatalogCtx);
  const def = catalog[data.blockType];
  const color = CATEGORY_COLORS[def?.category || ""] || "#64748b";
  const outputs = def?.outputs || [];
  const isStart = data.blockType === "core.start";
  return (
    <div className={`block-node ${selected ? "selected" : ""} ${data.invalid ? "invalid" : ""}`}>
      {!isStart && <Handle type="target" position={Position.Left} id="in" />}
      <div className="block-head" style={{ background: color }}>
        {def?.title || data.blockType}
      </div>
      <div className="block-body">
        {data.label && <div className="block-label">{data.label}</div>}
        <div className="block-summary">{summary(def, data.params)}</div>
      </div>
      {outputs.map((o, i) => (
        <div key={o.name}>
          {o.label && (
            <span className="handle-label" style={{ top: `${((i + 1) * 100) / (outputs.length + 1)}%` }}>
              {o.label}
            </span>
          )}
          <Handle
            type="source"
            position={Position.Right}
            id={o.name}
            style={{ top: `${((i + 1) * 100) / (outputs.length + 1)}%` }}
          />
        </div>
      ))}
      {def?.error_output && (
        <Handle type="source" position={Position.Bottom} id="error" className="error-handle" title="Ошибка" />
      )}
    </div>
  );
}

const nodeTypes = { block: BlockNodeView };

function toFlow(def: Definition): { nodes: BlockNode[]; edges: Edge[] } {
  return {
    nodes: def.nodes.map((n, i) => ({
      id: n.id,
      type: "block",
      position: n.position || { x: 80 + i * 260, y: 160 },
      data: { blockType: n.type, params: n.params || {}, label: n.label },
    })),
    edges: def.edges.map((e) => ({
      id: e.id || `e-${e.source}-${e.sourceHandle || "out"}`,
      source: e.source,
      sourceHandle: e.sourceHandle || "out",
      target: e.target,
      targetHandle: "in",
      ...edgeStyle(e.sourceHandle),
    })),
  };
}

function fromFlow(nodes: BlockNode[], edges: Edge[], base: Definition): Definition {
  return {
    variables: base.variables,
    outputs: base.outputs,
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.data.blockType,
      ...(n.data.label ? { label: n.data.label } : {}),
      params: n.data.params,
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
    })),
    edges: edges.map((e) => ({ id: e.id, source: e.source, sourceHandle: e.sourceHandle || "out", target: e.target })),
  };
}

export default function Designer(props: { processId: number; focusNode: string | null }) {
  return (
    <ReactFlowProvider>
      <DesignerInner {...props} />
    </ReactFlowProvider>
  );
}

function DesignerInner({ processId, focusNode }: { processId: number; focusNode: string | null }) {
  const [catalog, setCatalog] = useState<BlockDef[]>([]);
  const [proc, setProc] = useState<Process | null>(null);
  const [meta, setMeta] = useState<Definition>({ variables: [], outputs: [], nodes: [], edges: [] });
  const [nodes, setNodes, onNodesChange] = useNodesState<BlockNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const flow = useReactFlow();
  const wrapper = useRef<HTMLDivElement>(null);
  const clipboard = useRef<{ nodes: BlockNode[]; edges: Edge[] } | null>(null);

  const byType = useMemo(() => Object.fromEntries(catalog.map((b) => [b.type, b])), [catalog]);

  useEffect(() => {
    Promise.all([api<BlockDef[]>("/api/blocks"), api<Process>(`/api/processes/${processId}`)])
      .then(([blocks, p]) => {
        setCatalog(blocks);
        setProc(p);
        const def = p.definition!;
        setMeta({ ...def, variables: def.variables || [], outputs: def.outputs || [] });
        const f = toFlow(def);
        if (focusNode) f.nodes = f.nodes.map((n) => ({ ...n, selected: n.id === focusNode }));
        setNodes(f.nodes);
        setEdges(f.edges);
        setTimeout(() => {
          const target = focusNode && f.nodes.find((n) => n.id === focusNode);
          if (target) flow.setCenter(target.position.x + 110, target.position.y + 40, { zoom: 1.2 });
          else flow.fitView({ padding: 0.2, maxZoom: 1.1 });
        }, 50);
      })
      .catch((e) => setLoadError((e as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [processId]);

  const markDirty = () => {
    setDirty(true);
    setStatus(null);
  };

  const onConnect = useCallback(
    (c: Connection) => {
      if (c.source === c.target) return;
      const handle = c.sourceHandle || "out";
      setEdges((eds) => [
        // из одного выхода — только одно ребро: новое заменяет старое
        ...eds.filter((e) => !(e.source === c.source && (e.sourceHandle || "out") === handle)),
        { id: `e-${c.source}-${handle}`, source: c.source, sourceHandle: handle, target: c.target, targetHandle: "in", ...edgeStyle(handle) },
      ]);
      markDirty();
    },
    [setEdges],
  );

  const addBlock = (type: string, position?: { x: number; y: number }) => {
    const def = byType[type];
    if (!def) return;
    const params: Record<string, unknown> = {};
    def.params.forEach((p) => {
      if (p.default !== null && p.default !== undefined) params[p.name] = p.default;
    });
    let pos = position;
    if (!pos) {
      const rect = wrapper.current?.getBoundingClientRect();
      pos = flow.screenToFlowPosition({
        x: (rect?.left || 0) + (rect?.width || 600) / 2 - 110,
        y: (rect?.top || 0) + (rect?.height || 400) / 2 - 40,
      });
    }
    const id = newId();
    setNodes((nds) => [
      ...nds.map((n) => ({ ...n, selected: false })),
      { id, type: "block", position: pos!, data: { blockType: type, params }, selected: true },
    ]);
    markDirty();
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const type = e.dataTransfer.getData("application/rpa-block");
    if (type) addBlock(type, flow.screenToFlowPosition({ x: e.clientX - 110, y: e.clientY - 20 }));
  };

  const definition = () => fromFlow(nodes, edges, meta);

  const save = async (): Promise<boolean> => {
    if (!proc) return false;
    try {
      const p = await api<Process>(`/api/processes/${processId}`, {
        method: "PUT",
        json: { name: proc.name, description: proc.description, definition: definition() },
      });
      setProc({ ...p });
      setDirty(false);
      setStatus(`Сохранено, версия ${p.version}`);
      return true;
    } catch (e) {
      setStatus(`Ошибка сохранения: ${(e as Error).message}`);
      return false;
    }
  };

  const check = async () => {
    if (dirty && !(await save())) return;
    const r = await api<{ ok: boolean; errors: string[] }>(`/api/processes/${processId}/validate`, { method: "POST" });
    setErrors(r.errors);
    const bad = new Set(r.errors.map((e) => e.split(" ")[0]));
    setNodes((nds) => nds.map((n) => ({ ...n, data: { ...n.data, invalid: bad.has(n.id) } })));
    setStatus(r.ok ? "Ошибок не найдено" : null);
  };

  const run = async () => {
    if (dirty && !(await save())) return;
    setRunning(true);
  };

  // горячие клавиши: сохранить, копировать, вставить
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      const inField = (e.target as HTMLElement).closest("input, textarea, select");
      if (mod && e.key.toLowerCase() === "s") {
        e.preventDefault();
        save();
      } else if (mod && e.key.toLowerCase() === "c" && !inField) {
        const sel = nodes.filter((n) => n.selected);
        const ids = new Set(sel.map((n) => n.id));
        clipboard.current = { nodes: sel, edges: edges.filter((ed) => ids.has(ed.source) && ids.has(ed.target)) };
      } else if (mod && e.key.toLowerCase() === "v" && !inField && clipboard.current?.nodes.length) {
        const map: Record<string, string> = {};
        const copies = clipboard.current.nodes
          .filter((n) => n.data.blockType !== "core.start")
          .map((n) => {
            map[n.id] = newId();
            return { ...n, id: map[n.id], selected: true, position: { x: n.position.x + 40, y: n.position.y + 40 }, data: { ...n.data, params: structuredClone(n.data.params) } };
          });
        const copyEdges = clipboard.current.edges
          .filter((ed) => map[ed.source] && map[ed.target])
          .map((ed) => ({ ...ed, id: `e-${map[ed.source]}-${ed.sourceHandle}`, source: map[ed.source], target: map[ed.target] }));
        setNodes((nds) => [...nds.map((n) => ({ ...n, selected: false })), ...copies]);
        setEdges((eds) => [...eds, ...copyEdges]);
        clipboard.current = { nodes: copies, edges: copyEdges };
        markDirty();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (dirty) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const selected = nodes.filter((n) => n.selected);
  const current = selected.length === 1 ? selected[0] : null;

  const updateNode = (id: string, patch: Partial<BlockData>) => {
    setNodes((nds) => nds.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...patch, invalid: false } } : n)));
    markDirty();
  };

  const knownVars = useMemo(() => {
    const s = new Set<string>(meta.variables.map((v) => v.name).filter(Boolean));
    s.add("last_error");
    nodes.forEach((n) => {
      byType[n.data.blockType]?.params.forEach((p) => {
        const v = n.data.params[p.name];
        if (p.kind === "variable" && typeof v === "string" && v) s.add(v);
      });
    });
    return [...s].sort();
  }, [nodes, meta, byType]);

  if (loadError) return <div className="page"><div className="error-box">{loadError}</div></div>;
  if (!proc) return <div className="page muted">Загрузка…</div>;

  return (
    <CatalogCtx.Provider value={byType}>
      <div className="designer">
        <div className="designer-bar">
          <a href="#/processes" className="link" onClick={(e) => { if (dirty && !confirm("Есть несохранённые изменения. Уйти?")) e.preventDefault(); }}>
            ← Процессы
          </a>
          <strong>{proc.name}</strong>
          <span className="muted">v{proc.version}{dirty ? " · не сохранено" : ""}</span>
          <span className="spacer" />
          {status && <span className="muted">{status}</span>}
          <button className="secondary" onClick={check}>Проверить</button>
          <button className="secondary" onClick={save} disabled={!dirty}>Сохранить</button>
          <button onClick={run}>▶ Запустить</button>
        </div>
        <div className="designer-body">
          <Palette catalog={catalog} onAdd={(t) => addBlock(t)} />
          <div className="canvas" ref={wrapper} onDrop={onDrop} onDragOver={(e) => e.preventDefault()}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={(ch) => {
                onNodesChange(ch);
                if (ch.some((c) => c.type === "position" || c.type === "remove")) markDirty();
              }}
              onEdgesChange={(ch) => {
                onEdgesChange(ch);
                if (ch.some((c) => c.type === "remove")) markDirty();
              }}
              onConnect={onConnect}
              deleteKeyCode={["Delete", "Backspace"]}
              fitView
              snapToGrid
              snapGrid={[10, 10]}
              defaultEdgeOptions={{ type: "smoothstep" }}
            >
              <Background gap={20} />
              <Controls />
              <MiniMap pannable zoomable />
            </ReactFlow>
          </div>
          <aside className="inspector">
            {errors.length > 0 && (
              <div className="error-box">
                <strong>Ошибки проверки</strong>
                <ul>{errors.map((e) => <li key={e}>{e}</li>)}</ul>
              </div>
            )}
            {current ? (
              <NodeInspector
                node={current}
                def={byType[current.data.blockType]}
                onChange={(patch) => updateNode(current.id, patch)}
                onDelete={() => {
                  setNodes((nds) => nds.filter((n) => n.id !== current.id));
                  setEdges((eds) => eds.filter((e) => e.source !== current.id && e.target !== current.id));
                  markDirty();
                }}
              />
            ) : selected.length > 1 ? (
              <p className="muted">Выбрано блоков: {selected.length}. Delete — удалить, Ctrl+C / Ctrl+V — копировать.</p>
            ) : (
              <ProcessSettings
                proc={proc}
                meta={meta}
                onChange={(p, m) => {
                  setProc(p);
                  setMeta(m);
                  markDirty();
                }}
              />
            )}
          </aside>
        </div>
        <datalist id="known-vars">{knownVars.map((v) => <option key={v} value={v} />)}</datalist>
        {running && (
          <RunDialog
            processId={processId}
            definition={definition()}
            onClose={() => setRunning(false)}
            onStarted={(job) => navigate(`/jobs/${job.id}`)}
          />
        )}
      </div>
    </CatalogCtx.Provider>
  );
}

function Palette({ catalog, onAdd }: { catalog: BlockDef[]; onAdd: (type: string) => void }) {
  const [q, setQ] = useState("");
  const groups = useMemo(() => {
    const s = q.trim().toLowerCase();
    const out: Record<string, BlockDef[]> = {};
    catalog
      .filter((b) => b.type !== "core.start")
      .filter((b) => !s || b.title.toLowerCase().includes(s) || b.type.includes(s) || b.description.toLowerCase().includes(s))
      .forEach((b) => (out[b.category] = [...(out[b.category] || []), b]));
    return out;
  }, [catalog, q]);
  return (
    <aside className="palette">
      <input placeholder="Поиск блока…" value={q} onChange={(e) => setQ(e.target.value)} />
      {Object.entries(groups).map(([cat, blocks]) => (
        <div key={cat} className="palette-group">
          <div className="palette-cat" style={{ color: CATEGORY_COLORS[cat] }}>{cat}</div>
          {blocks.map((b) => (
            <div
              key={b.type}
              className="palette-item"
              draggable
              title={b.description || b.type}
              onDragStart={(e) => e.dataTransfer.setData("application/rpa-block", b.type)}
              onDoubleClick={() => onAdd(b.type)}
            >
              <span className="dot" style={{ background: CATEGORY_COLORS[cat] }} />
              {b.title}
            </div>
          ))}
        </div>
      ))}
      <p className="muted small">Перетащите блок на холст или дважды щёлкните.</p>
    </aside>
  );
}

function NodeInspector({
  node,
  def,
  onChange,
  onDelete,
}: {
  node: BlockNode;
  def?: BlockDef;
  onChange: (patch: Partial<BlockData>) => void;
  onDelete: () => void;
}) {
  if (!def) return <div className="error-box">Неизвестный блок {node.data.blockType}</div>;
  const setParam = (name: string, value: unknown) => onChange({ params: { ...node.data.params, [name]: value } });
  return (
    <div className="inspector-form">
      <h3>{def.title}</h3>
      {def.description && <p className="muted small">{def.description}</p>}
      <label className="field">
        <span>Подпись на холсте</span>
        <input value={node.data.label || ""} placeholder="необязательно" onChange={(e) => onChange({ label: e.target.value })} />
      </label>
      {def.params.map((p) => (
        <ParamField key={p.name} p={p} value={node.data.params[p.name]} onChange={(v) => setParam(p.name, v)} />
      ))}
      {def.error_output && (
        <p className="muted small">
          Красная точка снизу — ветка «ошибка»: если её подключить, сбой блока не остановит процесс, текст ошибки будет в
          переменной <code>last_error</code>.
        </p>
      )}
      <div className="muted small">id: {node.id} · {def.type}</div>
      {node.data.blockType !== "core.start" && (
        <button className="danger small" onClick={onDelete}>Удалить блок</button>
      )}
    </div>
  );
}

function ParamField({ p, value, onChange }: { p: ParamDef; value: unknown; onChange: (v: unknown) => void }) {
  const label = (
    <span>
      {p.label}
      {p.required && <b className="req"> *</b>}
    </span>
  );
  const v = value ?? "";
  let input;
  switch (p.kind) {
    case "boolean":
      return (
        <label className="field checkbox">
          <input type="checkbox" checked={value === true || value === "true"} onChange={(e) => onChange(e.target.checked)} />
          {label}
        </label>
      );
    case "select":
      input = (
        <select value={String(v)} onChange={(e) => onChange(e.target.value)}>
          {(p.options || []).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      );
      break;
    case "code":
    case "text":
      input = (
        <textarea
          className={p.kind === "code" ? "code" : ""}
          rows={p.kind === "code" ? 12 : 4}
          spellCheck={false}
          value={String(v)}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (p.kind === "code" && e.key === "Tab") {
              e.preventDefault();
              const t = e.currentTarget;
              const s = t.selectionStart;
              const next = t.value.slice(0, s) + "    " + t.value.slice(t.selectionEnd);
              onChange(next);
              requestAnimationFrame(() => t.setSelectionRange(s + 4, s + 4));
            }
          }}
        />
      );
      break;
    case "variable":
      input = <input list="known-vars" value={String(v)} onChange={(e) => onChange(e.target.value.replace(/[^\p{L}\p{N}_]/gu, ""))} />;
      break;
    case "number":
      input = <input value={String(v)} inputMode="decimal" onChange={(e) => onChange(e.target.value)} />;
      break;
    case "expression":
      input = <input className="code" value={String(v)} spellCheck={false} onChange={(e) => onChange(e.target.value)} />;
      break;
    default:
      input = <input value={String(v)} onChange={(e) => onChange(e.target.value)} />;
  }
  return (
    <label className="field">
      {label}
      {input}
      {p.help && <small className="muted">{p.help}</small>}
    </label>
  );
}

function ProcessSettings({ proc, meta, onChange }: { proc: Process; meta: Definition; onChange: (p: Process, m: Definition) => void }) {
  const vars = meta.variables;
  const setVars = (v: Definition["variables"]) => onChange(proc, { ...meta, variables: v });
  return (
    <div className="inspector-form">
      <h3>Процесс</h3>
      <label className="field">
        <span>Название</span>
        <input value={proc.name} onChange={(e) => onChange({ ...proc, name: e.target.value }, meta)} />
      </label>
      <label className="field">
        <span>Описание</span>
        <textarea rows={3} value={proc.description} onChange={(e) => onChange({ ...proc, description: e.target.value }, meta)} />
      </label>
      <h4>Входные переменные</h4>
      <p className="muted small">Значения по умолчанию; при запуске их можно переопределить. Значение — текст или JSON (числа, списки).</p>
      {vars.map((v, i) => (
        <div className="row var-row" key={i}>
          <input placeholder="имя" value={v.name} onChange={(e) => setVars(vars.map((x, j) => (j === i ? { ...x, name: e.target.value.replace(/[^\p{L}\p{N}_]/gu, "") } : x)))} />
          <input placeholder="по умолчанию" value={showValue(v.default)} onChange={(e) => setVars(vars.map((x, j) => (j === i ? { ...x, default: parseValue(e.target.value) } : x)))} />
          <button className="link" title="Удалить" onClick={() => setVars(vars.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <button className="secondary small" onClick={() => setVars([...vars, { name: "", default: "" }])}>+ Переменная</button>
      <label className="field">
        <span>Выходные переменные</span>
        <input
          placeholder="через запятую"
          value={meta.outputs.join(", ")}
          onChange={(e) => onChange(proc, { ...meta, outputs: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })}
        />
        <small className="muted">Их значения попадут в итог запуска.</small>
      </label>
      <h4>Подсказки</h4>
      <ul className="muted small hints">
        <li>В любом текстовом параметре: <code>{"{{ выражение }}"}</code>, например <code>{"{{ row['Сумма'] * 2 }}"}</code>.</li>
        <li>Выход «Тело» цикла можно не замыкать: в конце ветки выполнение вернётся в цикл.</li>
        <li>Ctrl+S — сохранить, Ctrl+C / Ctrl+V — копировать блоки, Delete — удалить.</li>
      </ul>
    </div>
  );
}
