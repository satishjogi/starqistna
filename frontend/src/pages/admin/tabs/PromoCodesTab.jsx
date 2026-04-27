import React, { useEffect, useState } from "react";
import api from "../../../lib/api";

export default function PromoCodesTab() {
  const [promos, setPromos] = useState([]);
  const [form, setForm] = useState({
    code: "", type: "percent", value: 10, currency: "myr", max_uses: "", valid_until: "", description: "",
  });
  const [msg, setMsg] = useState("");

  const load = () => {
    api.get("/admin/promo-codes").then(({ data }) => setPromos(data)).catch(() => {});
  };

  useEffect(() => { load(); }, []);

  const create = async (e) => {
    e.preventDefault();
    setMsg("");
    try {
      const payload = {
        code: form.code.trim().toUpperCase(),
        type: form.type,
        value: parseFloat(form.value),
        currency: form.currency,
        description: form.description || undefined,
        max_uses: form.max_uses ? parseInt(form.max_uses) : undefined,
        valid_until: form.valid_until || undefined,
      };
      await api.post("/admin/promo-codes", payload);
      setMsg("Code created.");
      setForm({ code: "", type: "percent", value: 10, currency: "myr", max_uses: "", valid_until: "", description: "" });
      load();
    } catch (e) {
      setMsg(e?.response?.data?.detail || "Failed");
    }
  };

  const toggle = async (p) => {
    await api.patch(`/admin/promo-codes/${p.id}?active=${!p.active}`);
    load();
  };

  const remove = async (p) => {
    if (!window.confirm(`Delete code ${p.code}?`)) return;
    await api.delete(`/admin/promo-codes/${p.id}`);
    load();
  };

  return (
    <div className="mt-6 grid grid-cols-1 lg:grid-cols-5 gap-6">
      <div className="lg:col-span-2">
        <form onSubmit={create} className="te-card p-6 space-y-4" data-testid="add-promo-form">
          <div className="te-overline">Create promo code</div>
          <div>
            <label className="te-label">Code</label>
            <input required className="te-input font-mono uppercase" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} data-testid="promo-form-code" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="te-label">Type</label>
              <select className="te-input" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
                <option value="percent">Percent %</option>
                <option value="flat">Flat amount</option>
              </select>
            </div>
            <div>
              <label className="te-label">Value</label>
              <input type="number" min="1" step="0.01" required className="te-input" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} data-testid="promo-form-value" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="te-label">Currency</label>
              <select className="te-input" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}>
                <option value="myr">MYR</option>
                <option value="sgd">SGD</option>
                <option value="usd">USD</option>
              </select>
            </div>
            <div>
              <label className="te-label">Max uses (blank = ∞)</label>
              <input type="number" min="1" className="te-input" value={form.max_uses} onChange={(e) => setForm({ ...form, max_uses: e.target.value })} />
            </div>
          </div>
          <div>
            <label className="te-label">Valid until (optional)</label>
            <input type="date" className="te-input" value={form.valid_until} onChange={(e) => setForm({ ...form, valid_until: e.target.value })} />
          </div>
          <div>
            <label className="te-label">Description</label>
            <input className="te-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </div>
          <button className="te-btn-primary w-full" data-testid="promo-form-submit">Create code</button>
          {msg && <div className="text-xs font-bold">{msg}</div>}
        </form>
      </div>
      <div className="lg:col-span-3">
        <div className="te-overline mb-2">Active codes</div>
        <div className="space-y-2">
          {promos.length === 0 && <div className="text-sm text-zinc-500 te-card p-5">No codes yet.</div>}
          {promos.map((p) => (
            <div key={p.id} className="te-card p-4 grid grid-cols-6 gap-2 items-center" data-testid={`promo-row-${p.code}`}>
              <div className="col-span-2">
                <div className="font-mono font-black">{p.code}</div>
                <div className="text-[10px] text-zinc-500">{p.description}</div>
              </div>
              <div className="font-mono text-sm">
                {p.type === "percent" ? `${p.value}%` : `${p.currency.toUpperCase()} ${Number(p.value).toFixed(2)}`}
              </div>
              <div className="font-mono text-xs">{p.used_count || 0}{p.max_uses ? ` / ${p.max_uses}` : ""}</div>
              <div className="font-mono text-xs">{p.valid_until || "∞"}</div>
              <div className="flex gap-2 justify-end">
                <button onClick={() => toggle(p)} className={`text-[10px] px-2 py-1 font-bold uppercase border ${p.active ? "bg-emerald-100 border-emerald-300 text-emerald-700" : "bg-zinc-100 border-black/20 text-zinc-500"}`} data-testid={`promo-toggle-${p.code}`}>
                  {p.active ? "active" : "off"}
                </button>
                <button onClick={() => remove(p)} className="text-[10px] px-2 py-1 font-bold uppercase border border-red-200 text-red-600 hover:bg-red-50" data-testid={`promo-delete-${p.code}`}>
                  delete
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
