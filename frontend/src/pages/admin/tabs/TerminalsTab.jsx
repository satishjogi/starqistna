import React, { useEffect, useState } from "react";
import api from "../../../lib/api";

// Stop = a pickup or dropoff point (KL Sentral, TBS, Nu Sentral, Bugis, etc.).
// Historically called "Terminal" in the DB — kept the collection name for
// backward compat, but presented as "Stops" to the operator.
const EMPTY_FORM = {
  city: "", name: "", code: "", state: "", country: "MY",
  landmark_address: "", lat: "", lng: "", cts_code: "",
  is_pickup: true, is_dropoff: true,
};

function toPayload(form) {
  return {
    city: form.city.trim(),
    name: form.name.trim(),
    code: form.code.trim().toUpperCase(),
    state: form.state.trim() || null,
    country: form.country,
    landmark_address: form.landmark_address.trim() || null,
    lat: form.lat === "" ? null : Number(form.lat),
    lng: form.lng === "" ? null : Number(form.lng),
    cts_code: form.cts_code.trim() || null,
    is_pickup: !!form.is_pickup,
    is_dropoff: !!form.is_dropoff,
  };
}

export default function TerminalsTab() {
  const [terminals, setTerminals] = useState([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [msg, setMsg] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState(EMPTY_FORM);
  const [filter, setFilter] = useState("");

  const load = () => {
    api.get("/admin/terminals").then(({ data }) => setTerminals(data)).catch(() => {});
  };

  useEffect(() => { load(); }, []);

  const create = async (e) => {
    e.preventDefault();
    setMsg("");
    try {
      await api.post("/admin/terminals", toPayload(form));
      setMsg("Stop added.");
      setForm(EMPTY_FORM);
      load();
    } catch (err) {
      setMsg(err?.response?.data?.detail || "Failed");
    }
  };

  const startEdit = (t) => {
    setEditingId(t.id);
    setEditForm({
      city: t.city || "",
      name: t.name || "",
      code: t.code || "",
      state: t.state || "",
      country: t.country || "MY",
      landmark_address: t.landmark_address || "",
      lat: t.lat ?? "",
      lng: t.lng ?? "",
      cts_code: t.cts_code || "",
      is_pickup: t.is_pickup !== false,
      is_dropoff: t.is_dropoff !== false,
    });
  };

  const saveEdit = async () => {
    try {
      await api.patch(`/admin/terminals/${editingId}`, toPayload(editForm));
      setEditingId(null);
      load();
    } catch (err) {
      alert(err?.response?.data?.detail || "Save failed");
    }
  };

  const remove = async (t) => {
    if (t.schedule_count > 0) {
      alert(`Cannot delete — ${t.schedule_count} schedule(s) reference this stop.`);
      return;
    }
    if (!window.confirm(`Delete stop ${t.code} (${t.name})?`)) return;
    try {
      await api.delete(`/admin/terminals/${t.id}`);
      load();
    } catch (err) {
      alert(err?.response?.data?.detail || "Delete failed");
    }
  };

  const rows = filter.trim()
    ? terminals.filter((t) => {
      const q = filter.trim().toLowerCase();
      return (
        t.city?.toLowerCase().includes(q) ||
        t.name?.toLowerCase().includes(q) ||
        t.code?.toLowerCase().includes(q)
      );
    })
    : terminals;

  return (
    <div className="mt-6 grid grid-cols-1 lg:grid-cols-5 gap-6" data-testid="stops-tab">
      <div className="lg:col-span-2">
        <form onSubmit={create} className="te-card p-6 space-y-4" data-testid="add-stop-form">
          <div className="te-overline">Add pickup / drop-off stop</div>
          <StopFormFields form={form} setForm={setForm} prefix="stop" />
          <button className="te-btn-primary w-full" data-testid="stop-form-submit">Add stop</button>
          {msg && <div className="text-xs font-bold" data-testid="stop-msg">{msg}</div>}
        </form>
      </div>

      <div className="lg:col-span-3">
        <div className="flex items-baseline gap-3 mb-2">
          <div className="te-overline">All stops ({rows.length}/{terminals.length})</div>
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Search by city, name, code…"
            className="te-input flex-1 !py-1 text-xs"
            data-testid="stop-filter"
          />
        </div>
        <div className="space-y-2 max-h-[720px] overflow-auto pr-1">
          {rows.map((t) => (
            <div key={t.id} data-testid={`stop-row-${t.code}`}>
              <div className="te-card p-4 grid grid-cols-12 gap-2 items-center">
                <div className="col-span-3">
                  <div className="font-black text-sm">{t.city}</div>
                  <div className="text-[10px] font-mono text-zinc-500">
                    {t.state || "—"}{t.cts_code ? ` · CTS:${t.cts_code}` : ""}
                  </div>
                </div>
                <div className="col-span-4 text-sm">
                  {t.name}
                  {t.landmark_address && (
                    <div className="text-[10px] text-zinc-500 truncate">{t.landmark_address}</div>
                  )}
                </div>
                <div className="col-span-1">
                  <span className="font-mono text-[10px] font-bold bg-zinc-900 text-white px-1.5 py-0.5">{t.code}</span>
                </div>
                <div className="col-span-1">
                  <span className={`text-[10px] font-mono font-bold px-1.5 py-0.5 ${t.country === "SG" ? "bg-rose-100 text-rose-700" : "bg-blue-100 text-blue-700"}`}>
                    {t.country || "MY"}
                  </span>
                </div>
                <div className="col-span-1 flex gap-1">
                  {t.is_pickup !== false && <span className="text-[9px] font-mono font-bold bg-emerald-100 text-emerald-700 px-1 py-0.5">P</span>}
                  {t.is_dropoff !== false && <span className="text-[9px] font-mono font-bold bg-amber-100 text-amber-700 px-1 py-0.5">D</span>}
                </div>
                <div className="col-span-2 flex justify-end gap-1">
                  <button
                    onClick={() => (editingId === t.id ? setEditingId(null) : startEdit(t))}
                    className="text-[10px] px-2 py-1 font-bold uppercase border border-black/15 hover:bg-zinc-50"
                    data-testid={`stop-edit-${t.code}`}
                  >
                    {editingId === t.id ? "close" : "edit"}
                  </button>
                  <button
                    onClick={() => remove(t)}
                    disabled={t.schedule_count > 0}
                    title={t.schedule_count > 0 ? `Cannot delete — ${t.schedule_count} schedules` : "Delete"}
                    className="text-[10px] px-2 py-1 font-bold uppercase border border-red-200 text-red-600 hover:bg-red-50 disabled:opacity-30 disabled:cursor-not-allowed"
                    data-testid={`stop-delete-${t.code}`}
                  >
                    del
                  </button>
                </div>
              </div>
              {editingId === t.id && (
                <div className="te-card p-4 border-t-0 space-y-4 bg-zinc-50" data-testid={`stop-edit-form-${t.code}`}>
                  <StopFormFields form={editForm} setForm={setEditForm} prefix={`stop-edit-${t.code}`} />
                  <div className="flex justify-end gap-2">
                    <button
                      onClick={() => setEditingId(null)}
                      className="text-[10px] px-3 py-2 font-bold uppercase border border-black/15 hover:bg-white"
                    >
                      cancel
                    </button>
                    <button
                      onClick={saveEdit}
                      className="text-[10px] px-3 py-2 font-bold uppercase bg-[#002FA7] text-white hover:bg-black"
                      data-testid={`stop-edit-save-${t.code}`}
                    >
                      save
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}


// Shared form body — used in both "Add" and inline "Edit" forms.
function StopFormFields({ form, setForm, prefix }) {
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  return (
    <>
      <div>
        <label className="te-label">City</label>
        <input required className="te-input" value={form.city} onChange={set("city")} placeholder="Kuala Lumpur" data-testid={`${prefix}-city`} />
      </div>
      <div>
        <label className="te-label">Stop name</label>
        <input required className="te-input" value={form.name} onChange={set("name")} placeholder="KL Sentral" data-testid={`${prefix}-name`} />
      </div>
      <div>
        <label className="te-label">Landmark / address <span className="text-zinc-400">(optional)</span></label>
        <input className="te-input" value={form.landmark_address} onChange={set("landmark_address")} placeholder="Level B1, Nu Sentral Mall, next to Starbucks" data-testid={`${prefix}-landmark`} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="te-label">Code (2–8)</label>
          <input required className="te-input font-mono uppercase" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} placeholder="KLS" data-testid={`${prefix}-code`} />
        </div>
        <div>
          <label className="te-label">State <span className="text-zinc-400">(opt)</span></label>
          <input className="te-input" value={form.state} onChange={set("state")} placeholder="WP" data-testid={`${prefix}-state`} />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="te-label">Latitude <span className="text-zinc-400">(opt)</span></label>
          <input className="te-input font-mono" value={form.lat} onChange={set("lat")} placeholder="3.1345" inputMode="decimal" data-testid={`${prefix}-lat`} />
        </div>
        <div>
          <label className="te-label">Longitude <span className="text-zinc-400">(opt)</span></label>
          <input className="te-input font-mono" value={form.lng} onChange={set("lng")} placeholder="101.6841" inputMode="decimal" data-testid={`${prefix}-lng`} />
        </div>
      </div>
      <div>
        <label className="te-label">CTS / GoHub counter code <span className="text-zinc-400">(only for TBS-linked stops)</span></label>
        <input className="te-input font-mono uppercase" value={form.cts_code} onChange={(e) => setForm({ ...form, cts_code: e.target.value.toUpperCase() })} placeholder="TBS01" data-testid={`${prefix}-cts`} />
      </div>
      <div>
        <label className="te-label">Usage</label>
        <div className="grid grid-cols-2 gap-[1px] bg-black/10 border border-black/15">
          <button
            type="button"
            onClick={() => setForm({ ...form, is_pickup: !form.is_pickup })}
            className={`px-3 py-2.5 text-xs font-bold uppercase tracking-wider transition ${form.is_pickup ? "bg-emerald-600 text-white" : "bg-white hover:bg-zinc-50"}`}
            data-testid={`${prefix}-toggle-pickup`}
          >
            {form.is_pickup ? "✓ Pickup" : "Not a pickup"}
          </button>
          <button
            type="button"
            onClick={() => setForm({ ...form, is_dropoff: !form.is_dropoff })}
            className={`px-3 py-2.5 text-xs font-bold uppercase tracking-wider transition ${form.is_dropoff ? "bg-amber-500 text-white" : "bg-white hover:bg-zinc-50"}`}
            data-testid={`${prefix}-toggle-dropoff`}
          >
            {form.is_dropoff ? "✓ Drop-off" : "Not a drop-off"}
          </button>
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
                className={`px-3 py-2.5 text-xs font-bold uppercase tracking-wider transition ${selected ? "bg-[#002FA7] text-white" : "bg-white hover:bg-zinc-50"}`}
                data-testid={`${prefix}-country-${o.c}`}
              >
                {o.label}
              </button>
            );
          })}
        </div>
      </div>
    </>
  );
}
