import React, { useEffect, useMemo, useState } from "react";
import api from "../../../lib/api";
import EditScheduleModal from "./EditScheduleModal";

// SchedulesTab — list + Bulk Delete Wizard + inline edit.
// Bulk delete uses a two-step confirm flow:
//   1. Preview (server counts matching + blocked)
//   2. Execute (safe mode by default; ?force=true to nuke schedules with bookings)
export default function SchedulesTab() {
  const [schedules, setSchedules] = useState([]);
  const [terminals, setTerminals] = useState([]);
  const [routes, setRoutes] = useState([]);
  const [loading, setLoading] = useState(true);
  // Edit modal state — which schedule (if any) is being edited right now.
  const [editing, setEditing] = useState(null);

  // Bulk delete state
  const [filter, setFilter] = useState({
    from_terminal_id: "",
    to_terminal_id: "",
    start_date: "",
    end_date: "",
    route_id: "",
    unlinked_only: false,
  });
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [force, setForce] = useState(false);
  const [msg, setMsg] = useState("");

  const load = () => {
    setLoading(true);
    Promise.all([
      api.get("/admin/schedules"),
      api.get("/admin/terminals"),
      api.get("/admin/routes"),
    ]).then(([sr, tr, rr]) => {
      setSchedules(sr.data);
      setTerminals(tr.data);
      setRoutes(rr.data);
    }).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const terminalById = useMemo(() => Object.fromEntries(terminals.map((t) => [t.id, t])), [terminals]);
  const routeById = useMemo(() => Object.fromEntries(routes.map((r) => [r.id, r])), [routes]);

  const hasAnyFilter = filter.from_terminal_id || filter.to_terminal_id || filter.start_date || filter.end_date || filter.route_id || filter.unlinked_only;

  const buildPayload = () => {
    const p = { ...filter };
    Object.keys(p).forEach((k) => {
      if (p[k] === "" || p[k] === false) delete p[k];
    });
    return p;
  };

  const runPreview = async () => {
    setMsg("");
    setPreview(null);
    if (!hasAnyFilter) {
      setMsg("Select at least one filter to preview.");
      return;
    }
    setPreviewing(true);
    try {
      const { data } = await api.post("/admin/schedules/bulk-delete/preview", buildPayload());
      setPreview(data);
    } catch (e) {
      setMsg(e?.response?.data?.detail || "Preview failed");
    } finally {
      setPreviewing(false);
    }
  };

  const runExecute = async () => {
    if (!preview) return;
    const target = force ? preview.matched : preview.deletable;
    if (target === 0) {
      setMsg("Nothing to delete with current settings.");
      return;
    }
    const label = force ? `FORCE DELETE all ${preview.matched} schedules (including ${preview.blocked} with bookings)?` : `Delete ${preview.deletable} schedules (skipping ${preview.blocked} with bookings)?`;
    if (!window.confirm(`${label}\n\nThis cannot be undone.`)) return;
    setExecuting(true);
    setMsg("");
    try {
      const { data } = await api.post("/admin/schedules/bulk-delete", { ...buildPayload(), force });
      setMsg(`✓ Deleted ${data.deleted} schedule(s)${data.skipped ? ` · skipped ${data.skipped} with bookings` : ""}.`);
      setPreview(null);
      load();
    } catch (e) {
      setMsg(e?.response?.data?.detail || "Delete failed");
    } finally {
      setExecuting(false);
    }
  };

  return (
    <div className="mt-6 space-y-6" data-testid="schedules-tab">
      {/* Bulk delete panel */}
      <div className="te-card p-6" data-testid="bulk-delete-panel">
        <div className="flex items-baseline justify-between mb-4">
          <div>
            <div className="te-overline">Bulk delete schedules</div>
            <div className="text-sm text-zinc-500">Pick a filter, preview the impact, then delete. Schedules with confirmed bookings are protected unless you tick <span className="font-mono font-bold">force</span>.</div>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="te-label">Filter by route</label>
            <select className="te-input" value={filter.route_id} onChange={(e) => setFilter({ ...filter, route_id: e.target.value, unlinked_only: false })} data-testid="bulk-filter-route">
              <option value="">— any —</option>
              {routes.map((r) => (
                <option key={r.id} value={r.id}>{r.code} · {r.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="te-label">From terminal</label>
            <select className="te-input" value={filter.from_terminal_id} onChange={(e) => setFilter({ ...filter, from_terminal_id: e.target.value })} data-testid="bulk-filter-from">
              <option value="">— any —</option>
              {terminals.map((t) => <option key={t.id} value={t.id}>{t.code} · {t.name}</option>)}
            </select>
          </div>
          <div>
            <label className="te-label">To terminal</label>
            <select className="te-input" value={filter.to_terminal_id} onChange={(e) => setFilter({ ...filter, to_terminal_id: e.target.value })} data-testid="bulk-filter-to">
              <option value="">— any —</option>
              {terminals.map((t) => <option key={t.id} value={t.id}>{t.code} · {t.name}</option>)}
            </select>
          </div>
          <div>
            <label className="te-label">Departure date from</label>
            <input type="date" className="te-input" value={filter.start_date} onChange={(e) => setFilter({ ...filter, start_date: e.target.value })} data-testid="bulk-filter-start" />
          </div>
          <div>
            <label className="te-label">Departure date to</label>
            <input type="date" className="te-input" value={filter.end_date} onChange={(e) => setFilter({ ...filter, end_date: e.target.value })} data-testid="bulk-filter-end" />
          </div>
          <div>
            <label className="te-label">Special filter</label>
            <label className="flex items-center gap-2 mt-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={filter.unlinked_only}
                onChange={(e) => setFilter({ ...filter, unlinked_only: e.target.checked, route_id: e.target.checked ? "" : filter.route_id })}
                data-testid="bulk-filter-unlinked"
              />
              Only schedules <b>not linked</b> to any route
            </label>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 mt-5">
          <button
            onClick={runPreview}
            disabled={previewing || !hasAnyFilter}
            className="text-xs px-4 py-2 font-bold uppercase border border-black/20 hover:bg-zinc-50 disabled:opacity-40 disabled:cursor-not-allowed"
            data-testid="bulk-preview-btn"
          >
            {previewing ? "Previewing…" : "Preview count"}
          </button>
          <label className="flex items-center gap-2 text-xs font-bold uppercase text-red-600 cursor-pointer">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} data-testid="bulk-force-toggle" />
            Force delete (also removes schedules with bookings)
          </label>
        </div>

        {preview && (
          <div className="mt-4 border-2 border-black/10 bg-zinc-50 p-4" data-testid="bulk-preview-result">
            <div className="font-mono text-xs space-y-1">
              <div>Total matching:  <span className="font-black">{preview.matched}</span></div>
              <div>With bookings:   <span className="font-black text-red-600">{preview.blocked}</span> {preview.blocked > 0 && "← cannot delete unless force=true"}</div>
              <div>Safely deletable: <span className="font-black text-emerald-600">{preview.deletable}</span></div>
            </div>
            <button
              onClick={runExecute}
              disabled={executing || (!force && preview.deletable === 0) || (force && preview.matched === 0)}
              className="mt-3 text-xs px-4 py-2 font-bold uppercase bg-red-600 text-white hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed"
              data-testid="bulk-execute-btn"
            >
              {executing ? "Deleting…" : force ? `Force delete ${preview.matched}` : `Delete ${preview.deletable}`}
            </button>
          </div>
        )}

        {msg && <div className="mt-3 text-xs font-bold" data-testid="bulk-msg">{msg}</div>}
      </div>

      {/* Schedule list */}
      <div>
        <div className="te-overline mb-2">All schedules ({schedules.length})</div>
        <div className="space-y-2 max-h-[600px] overflow-auto pr-1">
          {loading && <div className="text-xs font-mono text-zinc-500">LOADING…</div>}
          {schedules.slice(0, 200).map((s) => {
            const from = terminalById[s.from_terminal_id];
            const to = terminalById[s.to_terminal_id];
            const route = s.route_id ? routeById[s.route_id] : null;
            return (
              <div key={s.id} className="te-card p-4 grid grid-cols-12 gap-3 text-sm items-center hover:bg-zinc-50" data-testid={`sched-row-${s.id}`}>
                <div className="col-span-2 font-mono text-xs">{s.departure_date}</div>
                <div className="col-span-1 font-mono text-xs">{s.departure_time}</div>
                <div className="col-span-3 text-xs">
                  {from ? `${from.code} → ${to?.code || "?"}` : "?"}
                  <div className="text-[10px] text-zinc-500">{from?.name} → {to?.name}</div>
                </div>
                <div className="col-span-2 text-xs">
                  {route ? (
                    <span className="font-mono text-[10px] font-bold bg-emerald-100 text-emerald-700 px-1.5 py-0.5">
                      ROUTE: {route.code}
                    </span>
                  ) : (
                    <span className="font-mono text-[10px] text-zinc-400">unlinked</span>
                  )}
                </div>
                <div className="col-span-1 font-mono text-xs">{s.currency?.toUpperCase()} {s.adult_fare?.toFixed(2)}</div>
                <div className="col-span-1 font-mono text-xs">{s.total_seats}s</div>
                <div className="col-span-1 text-[10px] text-zinc-400">{s.bus_type}</div>
                <div className="col-span-1 text-right">
                  {s.end_date && (
                    <div className={`inline-block text-[9px] font-mono font-bold uppercase px-1.5 py-0.5 mr-2 ${
                      s.end_date < s.departure_date
                        ? "bg-red-100 text-red-700"
                        : "bg-amber-100 text-amber-700"
                    }`} title={`Retires after ${s.end_date}`}>
                      Ends {s.end_date}
                    </div>
                  )}
                  <button onClick={() => setEditing(s)} className="text-xs font-mono font-bold uppercase text-[#002FA7] hover:underline"
                          data-testid={`edit-schedule-${s.id}`}>
                    Edit
                  </button>
                </div>
              </div>
            );
          })}
          {schedules.length > 200 && (
            <div className="text-[10px] font-mono text-zinc-400 text-center py-2">
              Showing first 200 of {schedules.length}. Use bulk delete to trim the DB.
            </div>
          )}
        </div>
      </div>

      {editing && (
        <EditScheduleModal
          schedule={editing}
          terminals={terminals}
          routes={routes}
          onClose={() => setEditing(null)}
          onSaved={load}
        />
      )}
    </div>
  );
}
