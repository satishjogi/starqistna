import React, { useEffect, useState } from "react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { useNavigate } from "react-router-dom";

export default function Admin() {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  const [stats, setStats] = useState(null);
  const [bookings, setBookings] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [terminals, setTerminals] = useState([]);
  const [promos, setPromos] = useState([]);
  const [tab, setTab] = useState("bookings");
  const [form, setForm] = useState({
    from_terminal_id: "", to_terminal_id: "", departure_date: "", departure_time: "08:00", arrival_time: "12:00",
    bus_operator: "Transnasional", bus_type: "Standard", adult_fare: 50, rows: 10, currency: "myr",
  });
  const [promoForm, setPromoForm] = useState({
    code: "", type: "percent", value: 10, currency: "myr", max_uses: "", valid_until: "", description: "",
  });
  const [msg, setMsg] = useState("");
  const [promoMsg, setPromoMsg] = useState("");

  useEffect(() => {
    if (!loading && (!user || !user.is_admin)) navigate("/");
  }, [user, loading, navigate]);

  const loadAll = () => {
    api.get("/admin/stats").then(({ data }) => setStats(data)).catch(() => {});
    api.get("/admin/bookings").then(({ data }) => setBookings(data)).catch(() => {});
    api.get("/admin/schedules").then(({ data }) => setSchedules(data)).catch(() => {});
    api.get("/terminals").then(({ data }) => setTerminals(data.all)).catch(() => {});
    api.get("/admin/promo-codes").then(({ data }) => setPromos(data)).catch(() => {});
  };

  useEffect(() => {
    if (user?.is_admin) loadAll();
  }, [user]);

  const createSched = async (e) => {
    e.preventDefault();
    setMsg("");
    try {
      await api.post("/admin/schedules", { ...form, adult_fare: parseFloat(form.adult_fare), rows: parseInt(form.rows) });
      setMsg("Schedule added.");
      loadAll();
    } catch (e) {
      setMsg(e?.response?.data?.detail || "Failed");
    }
  };

  const createPromo = async (e) => {
    e.preventDefault();
    setPromoMsg("");
    try {
      const payload = {
        code: promoForm.code.trim().toUpperCase(),
        type: promoForm.type,
        value: parseFloat(promoForm.value),
        currency: promoForm.currency,
        description: promoForm.description || undefined,
        max_uses: promoForm.max_uses ? parseInt(promoForm.max_uses) : undefined,
        valid_until: promoForm.valid_until || undefined,
      };
      await api.post("/admin/promo-codes", payload);
      setPromoMsg("Code created.");
      setPromoForm({ code: "", type: "percent", value: 10, currency: "myr", max_uses: "", valid_until: "", description: "" });
      loadAll();
    } catch (e) {
      setPromoMsg(e?.response?.data?.detail || "Failed");
    }
  };

  const togglePromo = async (p) => {
    await api.patch(`/admin/promo-codes/${p.id}?active=${!p.active}`);
    loadAll();
  };

  const deletePromo = async (p) => {
    if (!window.confirm(`Delete code ${p.code}?`)) return;
    await api.delete(`/admin/promo-codes/${p.id}`);
    loadAll();
  };

  if (!user?.is_admin) return null;

  return (
    <div className="px-6 md:px-12 lg:px-20 py-10">
      <div className="te-overline mb-2">Control · Admin</div>
      <h1 className="text-4xl font-black tracking-tight">Operations</h1>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-8">
        {stats && [
          ["Users", stats.users],
          ["Bookings", stats.bookings],
          ["Confirmed", stats.confirmed_bookings],
          ["Terminals", stats.terminals],
          ["Schedules", stats.schedules],
        ].map(([k, v]) => (
          <div key={k} className="te-card p-5">
            <div className="te-overline text-[10px]">{k}</div>
            <div className="font-mono text-3xl font-black">{v}</div>
          </div>
        ))}
      </div>

      <div className="mt-10 flex gap-1 border-b border-black/10 flex-wrap">
        {["bookings", "schedules", "add-schedule", "promo-codes"].map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-xs font-bold uppercase tracking-wider ${tab === t ? "bg-black text-white" : "text-zinc-500"}`}
            data-testid={`admin-tab-${t}`}
          >
            {t.replace(/-/g, " ")}
          </button>
        ))}
      </div>

      {tab === "bookings" && (
        <div className="mt-6 space-y-2">
          {bookings.map((b) => (
            <div key={b.id} className="te-card p-4 grid grid-cols-6 gap-3 text-sm items-center">
              <div className="font-mono font-bold">{b.reference}</div>
              <div>{b.contact_email}</div>
              <div className="font-mono">{b.departure_date} {b.departure_time}</div>
              <div>Seats: {b.seats.join(",")}</div>
              <div className="font-mono">{b.pricing.currency.toUpperCase()} {b.pricing.total.toFixed(2)}</div>
              <div className="text-[10px] uppercase font-mono font-bold">{b.status}</div>
            </div>
          ))}
        </div>
      )}

      {tab === "schedules" && (
        <div className="mt-6 space-y-2 max-h-[600px] overflow-auto">
          {schedules.map((s) => (
            <div key={s.id} className="te-card p-4 grid grid-cols-6 gap-3 text-sm items-center">
              <div className="font-mono">{s.departure_date}</div>
              <div className="font-mono">{s.departure_time} → {s.arrival_time}</div>
              <div>{s.bus_operator}</div>
              <div className="text-xs">{s.bus_type}</div>
              <div className="font-mono">{s.currency.toUpperCase()} {s.adult_fare.toFixed(2)}</div>
              <div className="font-mono">{s.total_seats} seats</div>
            </div>
          ))}
        </div>
      )}

      {tab === "add-schedule" && (
        <form onSubmit={createSched} className="te-card p-6 mt-6 grid grid-cols-1 md:grid-cols-2 gap-4 max-w-3xl" data-testid="add-sched-form">
          <div>
            <label className="te-label">From Terminal</label>
            <select className="te-input" required value={form.from_terminal_id} onChange={(e) => setForm({ ...form, from_terminal_id: e.target.value })}>
              <option value="">Select</option>
              {terminals.map((t) => <option key={t.id} value={t.id}>{t.city} · {t.name}</option>)}
            </select>
          </div>
          <div>
            <label className="te-label">To Terminal</label>
            <select className="te-input" required value={form.to_terminal_id} onChange={(e) => setForm({ ...form, to_terminal_id: e.target.value })}>
              <option value="">Select</option>
              {terminals.map((t) => <option key={t.id} value={t.id}>{t.city} · {t.name}</option>)}
            </select>
          </div>
          <div><label className="te-label">Date</label><input type="date" className="te-input" required value={form.departure_date} onChange={(e) => setForm({ ...form, departure_date: e.target.value })} /></div>
          <div><label className="te-label">Operator</label><input className="te-input" value={form.bus_operator} onChange={(e) => setForm({ ...form, bus_operator: e.target.value })} /></div>
          <div><label className="te-label">Departure</label><input type="time" className="te-input" value={form.departure_time} onChange={(e) => setForm({ ...form, departure_time: e.target.value })} /></div>
          <div><label className="te-label">Arrival</label><input type="time" className="te-input" value={form.arrival_time} onChange={(e) => setForm({ ...form, arrival_time: e.target.value })} /></div>
          <div><label className="te-label">Adult fare</label><input type="number" step="0.01" className="te-input" value={form.adult_fare} onChange={(e) => setForm({ ...form, adult_fare: e.target.value })} /></div>
          <div><label className="te-label">Rows (seats = rows × 4)</label><input type="number" className="te-input" value={form.rows} onChange={(e) => setForm({ ...form, rows: e.target.value })} /></div>
          <div>
            <label className="te-label">Currency</label>
            <select className="te-input" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}>
              <option value="myr">MYR</option>
              <option value="sgd">SGD</option>
              <option value="usd">USD</option>
            </select>
          </div>
          <div className="md:col-span-2 flex items-center gap-4">
            <button className="te-btn-primary" data-testid="add-sched-submit">Add schedule</button>
            {msg && <div className="text-xs font-bold">{msg}</div>}
          </div>
        </form>
      )}

      {tab === "promo-codes" && (
        <div className="mt-6 grid grid-cols-1 lg:grid-cols-5 gap-6">
          <div className="lg:col-span-2">
            <form onSubmit={createPromo} className="te-card p-6 space-y-4" data-testid="add-promo-form">
              <div className="te-overline">Create promo code</div>
              <div>
                <label className="te-label">Code</label>
                <input required className="te-input font-mono uppercase" value={promoForm.code} onChange={(e) => setPromoForm({ ...promoForm, code: e.target.value.toUpperCase() })} data-testid="promo-form-code" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="te-label">Type</label>
                  <select className="te-input" value={promoForm.type} onChange={(e) => setPromoForm({ ...promoForm, type: e.target.value })}>
                    <option value="percent">Percent %</option>
                    <option value="flat">Flat amount</option>
                  </select>
                </div>
                <div>
                  <label className="te-label">Value</label>
                  <input type="number" min="1" step="0.01" required className="te-input" value={promoForm.value} onChange={(e) => setPromoForm({ ...promoForm, value: e.target.value })} data-testid="promo-form-value" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="te-label">Currency</label>
                  <select className="te-input" value={promoForm.currency} onChange={(e) => setPromoForm({ ...promoForm, currency: e.target.value })}>
                    <option value="myr">MYR</option>
                    <option value="sgd">SGD</option>
                    <option value="usd">USD</option>
                  </select>
                </div>
                <div>
                  <label className="te-label">Max uses (blank = ∞)</label>
                  <input type="number" min="1" className="te-input" value={promoForm.max_uses} onChange={(e) => setPromoForm({ ...promoForm, max_uses: e.target.value })} />
                </div>
              </div>
              <div>
                <label className="te-label">Valid until (optional)</label>
                <input type="date" className="te-input" value={promoForm.valid_until} onChange={(e) => setPromoForm({ ...promoForm, valid_until: e.target.value })} />
              </div>
              <div>
                <label className="te-label">Description</label>
                <input className="te-input" value={promoForm.description} onChange={(e) => setPromoForm({ ...promoForm, description: e.target.value })} />
              </div>
              <button className="te-btn-primary w-full" data-testid="promo-form-submit">Create code</button>
              {promoMsg && <div className="text-xs font-bold">{promoMsg}</div>}
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
                    <button onClick={() => togglePromo(p)} className={`text-[10px] px-2 py-1 font-bold uppercase border ${p.active ? "bg-emerald-100 border-emerald-300 text-emerald-700" : "bg-zinc-100 border-black/20 text-zinc-500"}`} data-testid={`promo-toggle-${p.code}`}>
                      {p.active ? "active" : "off"}
                    </button>
                    <button onClick={() => deletePromo(p)} className="text-[10px] px-2 py-1 font-bold uppercase border border-red-200 text-red-600 hover:bg-red-50" data-testid={`promo-delete-${p.code}`}>
                      delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
