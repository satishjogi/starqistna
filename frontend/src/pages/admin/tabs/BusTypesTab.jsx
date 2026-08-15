import React, { useEffect, useState } from "react";
import api from "../../../lib/api";

const EMPTY_FORM = { name: "", seat_count: 40, image_url: "", description: "" };

export default function BusTypesTab() {
  const [types, setTypes] = useState([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState(null);
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const load = () => {
    api.get("/admin/bus-types").then(({ data }) => setTypes(data)).catch(() => {});
  };
  useEffect(() => { load(); }, []);

  const startEdit = (bt) => {
    setEditingId(bt.id);
    setForm({
      name: bt.name || "",
      seat_count: bt.seat_count ?? 40,
      image_url: bt.image_url || "",
      description: bt.description || "",
    });
    setMsg("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const cancelEdit = () => { setEditingId(null); setForm(EMPTY_FORM); setMsg(""); };

  const save = async (e) => {
    e.preventDefault();
    setMsg("");
    if (!form.name.trim()) { setMsg("Name is required."); return; }
    const seat = parseInt(form.seat_count, 10);
    if (!seat || seat < 12 || seat > 60) { setMsg("Seat count must be between 12 and 60."); return; }
    const payload = {
      name: form.name.trim(),
      seat_count: seat,
      image_url: form.image_url.trim() || null,
      description: form.description.trim() || null,
    };
    setLoading(true);
    try {
      if (editingId) {
        await api.patch(`/admin/bus-types/${editingId}`, payload);
        setMsg(`Updated · ${payload.name}`);
      } else {
        await api.post("/admin/bus-types", payload);
        setMsg(`Created · ${payload.name}`);
      }
      setForm(EMPTY_FORM);
      setEditingId(null);
      load();
    } catch (err) {
      setMsg(err?.response?.data?.detail || "Failed");
    } finally {
      setLoading(false);
    }
  };

  const remove = async (bt) => {
    if (!window.confirm(`Delete bus type "${bt.name}"?`)) return;
    try {
      await api.delete(`/admin/bus-types/${bt.id}`);
      setMsg(`Deleted · ${bt.name}`);
      if (editingId === bt.id) cancelEdit();
      load();
    } catch (err) {
      setMsg(err?.response?.data?.detail || "Failed");
    }
  };

  return (
    <div className="mt-6 max-w-5xl" data-testid="bus-types-tab">
      <form onSubmit={save} className="te-card p-6 grid grid-cols-1 md:grid-cols-2 gap-4" data-testid="bus-type-form">
        <div className="md:col-span-2 flex items-baseline justify-between">
          <div>
            <div className="te-overline mb-1">
              {editingId ? "Edit bus type" : "New bus type"}
            </div>
            <div className="text-xs text-zinc-500 font-mono">
              Reusable coach presets — the schedule form auto-fills seat count when picked.
            </div>
          </div>
          {editingId && (
            <button type="button" onClick={cancelEdit} className="text-xs font-mono font-bold uppercase text-zinc-500 hover:underline" data-testid="bt-cancel-edit">
              Cancel edit
            </button>
          )}
        </div>
        <div>
          <label className="te-label">Name <span className="text-[#B5121B]">*</span></label>
          <input
            className="te-input"
            required
            maxLength={60}
            placeholder="e.g. VIP Sleeper"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            data-testid="bt-name"
          />
          <div className="text-[10px] font-mono text-zinc-500 mt-1">
            Names containing &quot;VIP&quot; default to 2+1 layout. Everything else → 2+2.
          </div>
        </div>
        <div>
          <label className="te-label">Seat count <span className="text-[#B5121B]">*</span></label>
          <input
            type="number"
            min="12"
            max="60"
            className="te-input"
            required
            value={form.seat_count}
            onChange={(e) => setForm({ ...form, seat_count: e.target.value })}
            data-testid="bt-seat-count"
          />
        </div>
        <div>
          <label className="te-label">Image URL <span className="text-zinc-400">(optional)</span></label>
          <input
            className="te-input"
            maxLength={500}
            placeholder="https://…"
            value={form.image_url}
            onChange={(e) => setForm({ ...form, image_url: e.target.value })}
            data-testid="bt-image"
          />
        </div>
        <div>
          <label className="te-label">Short description <span className="text-zinc-400">(optional)</span></label>
          <input
            className="te-input"
            maxLength={200}
            placeholder="Reclining seats · USB · WiFi"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            data-testid="bt-description"
          />
        </div>
        <div className="md:col-span-2 flex items-center gap-4">
          <button className="te-btn-primary" disabled={loading} data-testid="bt-submit">
            {loading ? "Saving…" : editingId ? "Save changes" : "Create bus type"}
          </button>
          {msg && <div className="text-xs font-bold" data-testid="bt-msg">{msg}</div>}
        </div>
      </form>

      <div className="mt-8">
        <div className="te-overline mb-3">All bus types · {types.length}</div>
        {types.length === 0 && (
          <div className="te-card p-6 text-sm text-zinc-500 font-mono" data-testid="bt-empty">
            No bus types yet. Create one above.
          </div>
        )}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3" data-testid="bt-list">
          {types.map((bt) => (
            <div key={bt.id} className="te-card p-4 flex gap-4" data-testid={`bt-row-${bt.id}`}>
              {bt.image_url ? (
                <img src={bt.image_url} alt={bt.name} className="w-24 h-24 object-cover border border-black/10 flex-shrink-0" />
              ) : (
                <div className="w-24 h-24 border border-black/10 bg-zinc-50 flex items-center justify-center text-[10px] font-mono text-zinc-400 flex-shrink-0">
                  NO IMAGE
                </div>
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <div className="font-black text-lg truncate">{bt.name}</div>
                  <span className="text-[10px] font-mono uppercase tracking-wider px-2 py-0.5 bg-black text-white">
                    {bt.layout || "2+2"}
                  </span>
                </div>
                <div className="text-xs font-mono text-zinc-500 mt-0.5">
                  {bt.seat_count} seats
                </div>
                {bt.description && (
                  <div className="text-xs text-zinc-600 mt-1 line-clamp-2">{bt.description}</div>
                )}
                <div className="flex gap-3 mt-2 text-[10px] font-mono">
                  <button onClick={() => startEdit(bt)} className="underline text-zinc-700 hover:text-black" data-testid={`bt-edit-${bt.id}`}>
                    Edit
                  </button>
                  <button onClick={() => remove(bt)} className="underline text-red-600 hover:text-red-800" data-testid={`bt-delete-${bt.id}`}>
                    Delete
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
