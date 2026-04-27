import React, { useEffect, useState } from "react";
import api from "../../../lib/api";

export default function TerminalsTab() {
  const [terminals, setTerminals] = useState([]);
  const [form, setForm] = useState({
    city: "", name: "", code: "", state: "", country: "MY",
  });
  const [msg, setMsg] = useState("");

  const load = () => {
    api.get("/admin/terminals").then(({ data }) => setTerminals(data)).catch(() => {});
  };

  useEffect(() => { load(); }, []);

  const create = async (e) => {
    e.preventDefault();
    setMsg("");
    try {
      await api.post("/admin/terminals", {
        city: form.city.trim(),
        name: form.name.trim(),
        code: form.code.trim().toUpperCase(),
        state: form.state.trim() || null,
        country: form.country,
      });
      setMsg("Terminal added.");
      setForm({ city: "", name: "", code: "", state: "", country: "MY" });
      load();
    } catch (e) {
      setMsg(e?.response?.data?.detail || "Failed");
    }
  };

  const remove = async (t) => {
    if (t.schedule_count > 0) {
      alert(`Cannot delete — ${t.schedule_count} schedule(s) reference this terminal.`);
      return;
    }
    if (!window.confirm(`Delete terminal ${t.code} (${t.name})?`)) return;
    try {
      await api.delete(`/admin/terminals/${t.id}`);
      load();
    } catch (e) {
      alert(e?.response?.data?.detail || "Delete failed");
    }
  };

  return (
    <div className="mt-6 grid grid-cols-1 lg:grid-cols-5 gap-6">
      <div className="lg:col-span-2">
        <form onSubmit={create} className="te-card p-6 space-y-4" data-testid="add-terminal-form">
          <div className="te-overline">Add terminal</div>
          <div>
            <label className="te-label">City</label>
            <input required className="te-input" value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} placeholder="Kuala Lumpur" data-testid="terminal-city" />
          </div>
          <div>
            <label className="te-label">Terminal name</label>
            <input required className="te-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="KL Sentral" data-testid="terminal-name" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="te-label">Code (2–6)</label>
              <input required className="te-input font-mono uppercase" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} placeholder="KLS" data-testid="terminal-code" />
            </div>
            <div>
              <label className="te-label">State</label>
              <input className="te-input" value={form.state} onChange={(e) => setForm({ ...form, state: e.target.value })} placeholder="WP" data-testid="terminal-state" />
            </div>
          </div>
          <div>
            <label className="te-label">Country (decides billing currency)</label>
            <div className="grid grid-cols-2 gap-[1px] bg-black/10 border border-black/15">
              {[{ c: "MY", label: "Malaysia · MYR" }, { c: "SG", label: "Singapore · SGD" }].map((o) => {
                const selected = form.country === o.c;
                return (
                  <button
                    key={o.c}
                    type="button"
                    onClick={() => setForm({ ...form, country: o.c })}
                    className={`px-3 py-2.5 text-xs font-bold uppercase tracking-wider transition ${
                      selected ? "bg-[#002FA7] text-white" : "bg-white hover:bg-zinc-50"
                    }`}
                    data-testid={`terminal-country-${o.c}`}
                  >
                    {o.label}
                  </button>
                );
              })}
            </div>
          </div>
          <button className="te-btn-primary w-full" data-testid="terminal-form-submit">Add terminal</button>
          {msg && <div className="text-xs font-bold" data-testid="terminal-msg">{msg}</div>}
        </form>
      </div>
      <div className="lg:col-span-3">
        <div className="te-overline mb-2">All terminals ({terminals.length})</div>
        <div className="space-y-2 max-h-[600px] overflow-auto pr-1">
          {terminals.map((t) => (
            <div key={t.id} className="te-card p-4 grid grid-cols-12 gap-2 items-center" data-testid={`terminal-row-${t.code}`}>
              <div className="col-span-3">
                <div className="font-black">{t.city}</div>
                <div className="text-[10px] font-mono text-zinc-500">{t.state || "—"}</div>
              </div>
              <div className="col-span-5 text-sm">{t.name}</div>
              <div className="col-span-1">
                <span className="font-mono text-[10px] font-bold bg-zinc-900 text-white px-1.5 py-0.5">{t.code}</span>
              </div>
              <div className="col-span-1">
                <span className={`text-[10px] font-mono font-bold px-1.5 py-0.5 ${t.country === "SG" ? "bg-rose-100 text-rose-700" : "bg-blue-100 text-blue-700"}`}>
                  {t.country || "MY"}
                </span>
              </div>
              <div className="col-span-1 font-mono text-[10px] text-zinc-500">{t.schedule_count || 0} sch</div>
              <div className="col-span-1 flex justify-end">
                <button
                  onClick={() => remove(t)}
                  disabled={t.schedule_count > 0}
                  title={t.schedule_count > 0 ? `Cannot delete — ${t.schedule_count} schedules` : "Delete"}
                  className="text-[10px] px-2 py-1 font-bold uppercase border border-red-200 text-red-600 hover:bg-red-50 disabled:opacity-30 disabled:cursor-not-allowed"
                  data-testid={`terminal-delete-${t.code}`}
                >
                  del
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
