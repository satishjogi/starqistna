import React, { useEffect, useState } from "react";
import api from "../../../lib/api";

export default function AuditLogTab() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState({ resource: "all", action: "all", actor_email: "" });

  const load = (f = filter) => {
    setLoading(true);
    const params = new URLSearchParams();
    if (f.resource && f.resource !== "all") params.set("resource", f.resource);
    if (f.action && f.action !== "all") params.set("action", f.action);
    if (f.actor_email) params.set("actor_email", f.actor_email);
    params.set("limit", "300");
    api
      .get(`/admin/audit-logs?${params.toString()}`)
      .then(({ data }) => setLogs(data.items || []))
      .catch(() => setLogs([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load(filter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter.resource, filter.action]);

  return (
    <div className="mt-6 space-y-4" data-testid="audit-log-section">
      <div className="te-card p-4">
        <div className="te-overline mb-3">Filters</div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div>
            <label className="te-label">Resource</label>
            <select
              className="te-input"
              value={filter.resource}
              onChange={(e) => setFilter({ ...filter, resource: e.target.value })}
              data-testid="audit-filter-resource"
            >
              <option value="all">All</option>
              <option value="terminal">Terminal</option>
              <option value="schedule">Schedule</option>
              <option value="promo_code">Promo code</option>
            </select>
          </div>
          <div>
            <label className="te-label">Action</label>
            <select
              className="te-input"
              value={filter.action}
              onChange={(e) => setFilter({ ...filter, action: e.target.value })}
              data-testid="audit-filter-action"
            >
              <option value="all">All</option>
              <option value="create">Create</option>
              <option value="update">Update</option>
              <option value="delete">Delete</option>
              <option value="toggle">Toggle</option>
              <option value="bulk_create">Bulk create</option>
              <option value="delete_range">Delete range</option>
            </select>
          </div>
          <div>
            <label className="te-label">Actor email contains</label>
            <input
              className="te-input"
              placeholder="admin@…"
              value={filter.actor_email}
              onChange={(e) => setFilter({ ...filter, actor_email: e.target.value })}
              data-testid="audit-filter-actor"
            />
          </div>
          <div className="flex items-end">
            <button
              type="button"
              onClick={() => load(filter)}
              className="te-btn-outline w-full"
              data-testid="audit-refresh-btn"
            >
              {loading ? "Loading…" : "Refresh"}
            </button>
          </div>
        </div>
      </div>

      <div className="te-card p-0 overflow-hidden">
        <div className="grid grid-cols-12 gap-2 px-4 py-3 bg-black text-white text-[10px] font-mono uppercase tracking-wider">
          <div className="col-span-3">When</div>
          <div className="col-span-3">Actor</div>
          <div className="col-span-2">Action</div>
          <div className="col-span-2">Resource</div>
          <div className="col-span-2">Details</div>
        </div>
        {logs.length === 0 && (
          <div className="p-6 text-sm text-zinc-500" data-testid="audit-empty">
            {loading ? "Loading audit trail…" : "No audit entries match these filters yet."}
          </div>
        )}
        {logs.map((row) => (
          <div
            key={row.id}
            className="grid grid-cols-12 gap-2 px-4 py-3 border-t border-black/5 text-xs items-start"
            data-testid={`audit-row-${row.id}`}
          >
            <div className="col-span-3 font-mono text-[11px]">{row.created_at}</div>
            <div className="col-span-3">
              <div className="font-bold truncate">{row.actor_email || "—"}</div>
              {row.ip && <div className="font-mono text-[10px] text-zinc-500">{row.ip}</div>}
            </div>
            <div className="col-span-2">
              <span className="inline-block px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider bg-zinc-100 border border-black/10">
                {row.action}
              </span>
            </div>
            <div className="col-span-2 font-mono text-[11px]">
              <div>{row.resource}</div>
              {row.resource_id && <div className="text-zinc-500 truncate">{row.resource_id}</div>}
            </div>
            <div className="col-span-2 font-mono text-[10px] text-zinc-600 break-words">
              {row.details && Object.keys(row.details).length > 0
                ? JSON.stringify(row.details)
                : "—"}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
