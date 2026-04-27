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
  const [payments, setPayments] = useState({ summary: null, items: [] });
  const [paymentsFilter, setPaymentsFilter] = useState("all");
  const [paymentsLoading, setPaymentsLoading] = useState(false);
  const [auditLogs, setAuditLogs] = useState([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditFilter, setAuditFilter] = useState({ resource: "all", action: "all", actor_email: "" });
  const [tab, setTab] = useState("bookings");
  const [form, setForm] = useState({
    from_terminal_id: "", to_terminal_id: "",
    start_date: "", end_date: "",
    days_of_week: [0, 1, 2, 3, 4, 5, 6],
    departure_time: "08:00", arrival_time: "12:00",
    bus_type: "Standard", adult_fare: 50, child_fare: 25, total_seats: 40,
  });
  const [promoForm, setPromoForm] = useState({
    code: "", type: "percent", value: 10, currency: "myr", max_uses: "", valid_until: "", description: "",
  });
  const [terminalForm, setTerminalForm] = useState({
    city: "", name: "", code: "", state: "", country: "MY",
  });
  const [msg, setMsg] = useState("");
  const [promoMsg, setPromoMsg] = useState("");
  const [terminalMsg, setTerminalMsg] = useState("");
  const [admins, setAdmins] = useState({ admins: [], pending_invites: [] });
  const [adminInviteForm, setAdminInviteForm] = useState({ email: "", full_name: "", role: "admin" });
  const [adminMsg, setAdminMsg] = useState("");
  const isSuperAdmin = user?.role === "super_admin";
  const [feedback, setFeedback] = useState({ summary: null, items: [] });
  const [feedbackFilter, setFeedbackFilter] = useState({ status: "all", category: "all" });
  const [fxRate, setFxRate] = useState(3.5);
  const [fxRateInput, setFxRateInput] = useState("3.5");
  const [fxMsg, setFxMsg] = useState("");

  const loadSettings = () =>
    api.get("/settings").then(({ data }) => {
      setFxRate(data.sgd_to_myr_rate);
      setFxRateInput(String(data.sgd_to_myr_rate));
    }).catch(() => {});

  const saveFxRate = async () => {
    setFxMsg("");
    const rate = parseFloat(fxRateInput);
    if (!rate || rate <= 0) { setFxMsg("Enter a valid rate."); return; }
    try {
      await api.patch("/admin/settings", { sgd_to_myr_rate: rate });
      setFxRate(rate);
      setFxMsg(`Saved · 1 SGD ≈ MYR ${rate.toFixed(2)}`);
    } catch (e) {
      setFxMsg(e?.response?.data?.detail || "Could not save");
    }
  };

  const loadAdmins = () =>
    api.get("/admin/admins").then(({ data }) => setAdmins(data)).catch(() => {});

  const loadFeedback = (filter = feedbackFilter) => {
    const params = new URLSearchParams();
    if (filter.status && filter.status !== "all") params.set("status_filter", filter.status);
    if (filter.category && filter.category !== "all") params.set("category", filter.category);
    api.get(`/admin/feedback?${params.toString()}`)
      .then(({ data }) => setFeedback(data))
      .catch(() => {});
  };

  useEffect(() => {
    if (!loading && (!user || !user.is_admin)) navigate("/");
  }, [user, loading, navigate]);

  const loadAll = () => {
    api.get("/admin/stats").then(({ data }) => setStats(data)).catch(() => {});
    api.get("/admin/bookings").then(({ data }) => setBookings(data)).catch(() => {});
    api.get("/admin/schedules").then(({ data }) => setSchedules(data)).catch(() => {});
    api.get("/admin/terminals").then(({ data }) => setTerminals(data)).catch(() => {});
    api.get("/admin/promo-codes").then(({ data }) => setPromos(data)).catch(() => {});
  };

  const loadPayments = (filter = paymentsFilter) => {
    setPaymentsLoading(true);
    const q = filter && filter !== "all" ? `?status_filter=${filter}` : "";
    api
      .get(`/admin/payments${q}`)
      .then(({ data }) => setPayments(data))
      .catch(() => {})
      .finally(() => setPaymentsLoading(false));
  };

  const loadAuditLogs = (filter = auditFilter) => {
    setAuditLoading(true);
    const params = new URLSearchParams();
    if (filter.resource && filter.resource !== "all") params.set("resource", filter.resource);
    if (filter.action && filter.action !== "all") params.set("action", filter.action);
    if (filter.actor_email) params.set("actor_email", filter.actor_email);
    params.set("limit", "300");
    api
      .get(`/admin/audit-logs?${params.toString()}`)
      .then(({ data }) => setAuditLogs(data.items || []))
      .catch(() => setAuditLogs([]))
      .finally(() => setAuditLoading(false));
  };

  useEffect(() => {
    if (user?.is_admin && tab === "audit-log") loadAuditLogs(auditFilter);
    if (user?.is_admin && tab === "admins") loadAdmins();
    if (user?.is_admin && tab === "feedback") loadFeedback(feedbackFilter);
    if (user?.is_admin && tab === "add-schedule") loadSettings();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, auditFilter.resource, auditFilter.action, feedbackFilter.status, feedbackFilter.category, user]);

  useEffect(() => {
    if (user?.is_admin && tab === "payments") loadPayments(paymentsFilter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, paymentsFilter, user]);

  useEffect(() => {
    if (user?.is_admin) loadAll();
  }, [user]);

  const createSched = async (e) => {
    e.preventDefault();
    setMsg("");
    if (!form.start_date || !form.end_date) {
      setMsg("Start date and end date are both required.");
      return;
    }
    if (form.end_date < form.start_date) {
      setMsg("End date must be on or after start date.");
      return;
    }
    if (form.days_of_week.length === 0) {
      setMsg("Pick at least one day of the week.");
      return;
    }
    try {
      const { data } = await api.post("/admin/schedules/bulk", {
        ...form,
        adult_fare: parseFloat(form.adult_fare),
        child_fare: parseFloat(form.child_fare),
        total_seats: parseInt(form.total_seats, 10),
      });
      setMsg(`Created ${data.created} schedule(s)${data.skipped_duplicates ? ` · ${data.skipped_duplicates} skipped (duplicates)` : ""} · billed in ${data.currency.toUpperCase()}.`);
      loadAll();
    } catch (e) {
      setMsg(e?.response?.data?.detail || "Failed");
    }
  };

  const toggleDay = (d) => {
    const set = new Set(form.days_of_week);
    if (set.has(d)) set.delete(d); else set.add(d);
    setForm({ ...form, days_of_week: [...set].sort() });
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

  const createTerminal = async (e) => {
    e.preventDefault();
    setTerminalMsg("");
    try {
      await api.post("/admin/terminals", {
        city: terminalForm.city.trim(),
        name: terminalForm.name.trim(),
        code: terminalForm.code.trim().toUpperCase(),
        state: terminalForm.state.trim() || null,
        country: terminalForm.country,
      });
      setTerminalMsg("Terminal added.");
      setTerminalForm({ city: "", name: "", code: "", state: "", country: "MY" });
      loadAll();
    } catch (e) {
      setTerminalMsg(e?.response?.data?.detail || "Failed");
    }
  };

  const deleteTerminal = async (t) => {
    if (t.schedule_count > 0) {
      alert(`Cannot delete — ${t.schedule_count} schedule(s) reference this terminal.`);
      return;
    }
    if (!window.confirm(`Delete terminal ${t.code} (${t.name})?`)) return;
    try {
      await api.delete(`/admin/terminals/${t.id}`);
      loadAll();
    } catch (e) {
      alert(e?.response?.data?.detail || "Delete failed");
    }
  };

  if (!user?.is_admin) return null;

  return (
    <div className="px-4 md:px-6 lg:px-10 py-10">
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
        {["bookings", "payments", "schedules", "add-schedule", "terminals", "promo-codes", "feedback", "audit-log", "admins"].map((t) => (
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

      {tab === "payments" && (
        <div className="mt-6" data-testid="admin-payments-panel">
          {/* Summary cards */}
          <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-5">
            <SummaryCard label="Total" value={payments.summary?.total ?? 0} />
            <SummaryCard label="Paid" value={payments.summary?.paid ?? 0} accent="emerald" />
            <SummaryCard label="Initiated" value={payments.summary?.initiated ?? 0} accent="zinc" />
            <SummaryCard label="Failed" value={payments.summary?.failed ?? 0} accent="red" />
            <SummaryCard label="Gross MYR" value={`RM ${(payments.summary?.gross_myr ?? 0).toFixed(2)}`} />
            <SummaryCard label="Gross SGD" value={`S$ ${(payments.summary?.gross_sgd ?? 0).toFixed(2)}`} />
          </div>

          {/* Filters */}
          <div className="flex gap-2 flex-wrap mb-4">
            {["all", "paid", "initiated", "failed", "refunded"].map((f) => (
              <button
                key={f}
                onClick={() => setPaymentsFilter(f)}
                className={`px-3 py-1.5 text-[10px] font-mono font-bold uppercase border ${
                  paymentsFilter === f
                    ? "bg-black text-white border-black"
                    : "border-black/20 text-zinc-600 hover:border-black"
                }`}
                data-testid={`payments-filter-${f}`}
              >
                {f}
              </button>
            ))}
            <button
              onClick={() => loadPayments(paymentsFilter)}
              className="px-3 py-1.5 text-[10px] font-mono font-bold uppercase border border-black/20 text-zinc-600 hover:border-black ml-auto"
              data-testid="payments-refresh"
            >
              ↻ Refresh
            </button>
          </div>

          {/* Table */}
          <div className="te-card overflow-hidden">
            <div className="hidden md:grid grid-cols-12 gap-3 px-4 py-3 bg-zinc-50 border-b border-black/10 text-[10px] font-mono font-bold uppercase text-zinc-500">
              <div className="col-span-2">Created</div>
              <div className="col-span-2">Booking</div>
              <div className="col-span-3">Customer</div>
              <div className="col-span-2">Amount</div>
              <div className="col-span-2">Session</div>
              <div className="col-span-1 text-right">Status</div>
            </div>

            {paymentsLoading && (
              <div className="p-6 font-mono text-xs text-zinc-500" data-testid="payments-loading">LOADING…</div>
            )}
            {!paymentsLoading && (payments.items?.length ?? 0) === 0 && (
              <div className="p-6 font-mono text-xs text-zinc-500" data-testid="payments-empty">
                NO TRANSACTIONS
              </div>
            )}
            {!paymentsLoading &&
              payments.items?.map((p) => {
                const created = (p.created_at || "").slice(0, 19).replace("T", " ");
                const ccy = (p.currency || "myr").toUpperCase();
                const amt = Number(p.amount || 0).toFixed(2);
                const ps = (p.payment_status || "initiated").toLowerCase();
                const badgeCls =
                  ps === "paid"
                    ? "bg-emerald-100 text-emerald-700"
                    : ps === "failed"
                    ? "bg-red-100 text-red-700"
                    : ps === "refunded" || ps === "canceled" || ps === "expired"
                    ? "bg-amber-100 text-amber-700"
                    : "bg-zinc-100 text-zinc-600";
                const bookingRef =
                  p?.metadata?.booking_reference || p.booking_reference || "—";
                const stripeUrl = p.session_id
                  ? `https://dashboard.stripe.com/test/payments?query=${encodeURIComponent(p.session_id)}`
                  : null;
                return (
                  <div
                    key={p.session_id || p.id}
                    className="grid grid-cols-1 md:grid-cols-12 gap-3 px-4 py-3 border-b border-black/5 last:border-b-0 text-sm items-center"
                    data-testid={`payment-row-${p.session_id || p.id}`}
                  >
                    <div className="md:col-span-2 font-mono text-xs text-zinc-600">{created}</div>
                    <div className="md:col-span-2 font-mono font-bold">{bookingRef}</div>
                    <div className="md:col-span-3 truncate" title={p.user_email}>
                      {p.user_email || "—"}
                    </div>
                    <div className="md:col-span-2 font-mono font-bold">
                      {ccy} {amt}
                    </div>
                    <div className="md:col-span-2 font-mono text-[10px] text-zinc-500 truncate">
                      {p.session_id ? (
                        stripeUrl ? (
                          <a
                            href={stripeUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="underline hover:text-black"
                            title="Open in Stripe Dashboard"
                          >
                            {p.session_id.slice(0, 18)}…
                          </a>
                        ) : (
                          p.session_id.slice(0, 18) + "…"
                        )
                      ) : (
                        "—"
                      )}
                    </div>
                    <div className="md:col-span-1 md:text-right">
                      <span className={`text-[10px] font-mono font-bold uppercase px-2 py-1 ${badgeCls}`}>
                        {ps}
                      </span>
                    </div>
                  </div>
                );
              })}
          </div>
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
        <>
          <div className="te-card p-5 mt-6 max-w-4xl" data-testid="fx-rate-panel">
            <div className="te-overline mb-3">Currency display · SGD → MYR approx</div>
            <div className="flex flex-col md:flex-row md:items-end gap-3">
              <div className="flex-1">
                <label className="te-label">1 SGD = MYR</label>
                <input
                  type="number"
                  step="0.01"
                  min="0.01"
                  className="te-input"
                  value={fxRateInput}
                  onChange={(e) => setFxRateInput(e.target.value)}
                  data-testid="fx-rate-input"
                />
                <div className="text-[10px] font-mono text-zinc-500 mt-1">
                  Used for the bracketed MYR approx shown on Singapore-priced trips during search & checkout. Update whenever the rate drifts.
                </div>
              </div>
              <button type="button" onClick={saveFxRate} className="te-btn-primary" data-testid="fx-rate-save">
                Save rate
              </button>
            </div>
            {fxMsg && <div className="text-xs font-bold mt-2" data-testid="fx-rate-msg">{fxMsg}</div>}
          </div>
          <form onSubmit={createSched} className="te-card p-6 mt-4 grid grid-cols-1 md:grid-cols-2 gap-4 max-w-4xl" data-testid="add-sched-form">
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
          <div>
            <label className="te-label">First trip date</label>
            <input type="date" className="te-input" required value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} data-testid="sched-start-date" />
          </div>
          <div>
            <label className="te-label">Last trip date (inclusive) <span className="text-[#B5121B]">*</span></label>
            <input type="date" className="te-input" required value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} data-testid="sched-end-date" />
          </div>
          <div className="md:col-span-2">
            <label className="te-label">Runs on (days of week)</label>
            <div className="grid grid-cols-7 gap-[1px] bg-black/10 border border-black/15" data-testid="days-of-week-picker">
              {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day, i) => {
                const active = form.days_of_week.includes(i);
                return (
                  <button
                    key={day}
                    type="button"
                    onClick={() => toggleDay(i)}
                    className={`px-2 py-2.5 text-xs font-bold uppercase tracking-wider transition ${active ? "bg-[#002FA7] text-white" : "bg-white hover:bg-zinc-50 text-zinc-500"}`}
                    data-testid={`dow-${day.toLowerCase()}`}
                  >
                    {day}
                  </button>
                );
              })}
            </div>
            <div className="flex gap-3 mt-2 text-[10px] font-mono">
              <button type="button" className="underline text-zinc-600" onClick={() => setForm({ ...form, days_of_week: [0, 1, 2, 3, 4, 5, 6] })}>All days</button>
              <button type="button" className="underline text-zinc-600" onClick={() => setForm({ ...form, days_of_week: [0, 1, 2, 3, 4] })}>Weekdays</button>
              <button type="button" className="underline text-zinc-600" onClick={() => setForm({ ...form, days_of_week: [5, 6] })}>Weekends</button>
            </div>
          </div>
          <div>
            <label className="te-label">Coach class</label>
            <div className="grid grid-cols-3 gap-[1px] bg-black/10 border border-black/15" data-testid="coach-class-picker">
              {["VIP 27", "Executive", "Standard"].map((bt) => {
                const selected = form.bus_type === bt;
                return (
                  <button
                    key={bt}
                    type="button"
                    onClick={() => setForm({ ...form, bus_type: bt })}
                    className={`px-3 py-2.5 text-xs font-bold uppercase tracking-wider transition ${selected ? "bg-[#B5121B] text-white" : "bg-white hover:bg-zinc-50"}`}
                    data-testid={`coach-class-${bt.replace(" ", "-").toLowerCase()}`}
                  >
                    {bt}
                  </button>
                );
              })}
            </div>
          </div>
          <div>
            <label className="te-label">Adult fare</label>
            <input type="number" step="0.01" className="te-input" value={form.adult_fare} onChange={(e) => setForm({ ...form, adult_fare: e.target.value })} data-testid="add-sched-adult-fare" />
          </div>
          <div>
            <label className="te-label">Child fare</label>
            <input type="number" step="0.01" className="te-input" value={form.child_fare} onChange={(e) => setForm({ ...form, child_fare: e.target.value })} data-testid="add-sched-child-fare" />
          </div>
          <div>
            <label className="te-label">Departure time</label>
            <input type="time" className="te-input" value={form.departure_time} onChange={(e) => setForm({ ...form, departure_time: e.target.value })} />
          </div>
          <div>
            <label className="te-label">Arrival time</label>
            <input type="time" className="te-input" value={form.arrival_time} onChange={(e) => setForm({ ...form, arrival_time: e.target.value })} />
          </div>
          <div>
            <label className="te-label">Total seats (capacity)</label>
            <input
              type="number"
              min="12"
              max="60"
              className="te-input"
              value={form.total_seats}
              onChange={(e) => setForm({ ...form, total_seats: e.target.value })}
              data-testid="add-sched-total-seats"
            />
            <div className="text-[10px] font-mono text-zinc-500 mt-1">
              VIP → 2+1 layout (~27 seats) · Standard/Executive → 2+2 layout (~40 seats)
            </div>
          </div>
          <div className="md:col-span-2 text-[10px] font-mono text-zinc-500 leading-relaxed border-l-2 border-[#002FA7] pl-3 py-1">
            OPERATOR · STAR QISTNA (single operator)<br/>
            CURRENCY · AUTO-DERIVED FROM ORIGIN TERMINAL COUNTRY (SG → SGD, MY → MYR)<br/>
            LAYOUT · BUS TYPE DETERMINES 2+1 (VIP) OR 2+2 (STANDARD/EXECUTIVE)<br/>
            BULK MODE · CREATES ONE SCHEDULE PER SELECTED WEEKDAY BETWEEN START & END DATES · MAX 180 DAYS
          </div>
          <div className="md:col-span-2 flex items-center gap-4">
            <button className="te-btn-primary" data-testid="add-sched-submit">Generate schedules</button>
            {msg && <div className="text-xs font-bold" data-testid="sched-msg">{msg}</div>}
          </div>
        </form>
        </>
      )}

      {tab === "terminals" && (
        <div className="mt-6 grid grid-cols-1 lg:grid-cols-5 gap-6">
          <div className="lg:col-span-2">
            <form onSubmit={createTerminal} className="te-card p-6 space-y-4" data-testid="add-terminal-form">
              <div className="te-overline">Add terminal</div>
              <div>
                <label className="te-label">City</label>
                <input required className="te-input" value={terminalForm.city} onChange={(e) => setTerminalForm({ ...terminalForm, city: e.target.value })} placeholder="Kuala Lumpur" data-testid="terminal-city" />
              </div>
              <div>
                <label className="te-label">Terminal name</label>
                <input required className="te-input" value={terminalForm.name} onChange={(e) => setTerminalForm({ ...terminalForm, name: e.target.value })} placeholder="KL Sentral" data-testid="terminal-name" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="te-label">Code (2–6)</label>
                  <input required className="te-input font-mono uppercase" value={terminalForm.code} onChange={(e) => setTerminalForm({ ...terminalForm, code: e.target.value.toUpperCase() })} placeholder="KLS" data-testid="terminal-code" />
                </div>
                <div>
                  <label className="te-label">State</label>
                  <input className="te-input" value={terminalForm.state} onChange={(e) => setTerminalForm({ ...terminalForm, state: e.target.value })} placeholder="WP" data-testid="terminal-state" />
                </div>
              </div>
              <div>
                <label className="te-label">Country (decides billing currency)</label>
                <div className="grid grid-cols-2 gap-[1px] bg-black/10 border border-black/15">
                  {[{ c: "MY", label: "Malaysia · MYR" }, { c: "SG", label: "Singapore · SGD" }].map((o) => {
                    const selected = terminalForm.country === o.c;
                    return (
                      <button
                        key={o.c}
                        type="button"
                        onClick={() => setTerminalForm({ ...terminalForm, country: o.c })}
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
              {terminalMsg && <div className="text-xs font-bold" data-testid="terminal-msg">{terminalMsg}</div>}
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
                      onClick={() => deleteTerminal(t)}
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

      {tab === "audit-log" && (
        <div className="mt-6 space-y-4" data-testid="audit-log-section">
          <div className="te-card p-4">
            <div className="te-overline mb-3">Filters</div>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
              <div>
                <label className="te-label">Resource</label>
                <select
                  className="te-input"
                  value={auditFilter.resource}
                  onChange={(e) => setAuditFilter({ ...auditFilter, resource: e.target.value })}
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
                  value={auditFilter.action}
                  onChange={(e) => setAuditFilter({ ...auditFilter, action: e.target.value })}
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
                  value={auditFilter.actor_email}
                  onChange={(e) => setAuditFilter({ ...auditFilter, actor_email: e.target.value })}
                  data-testid="audit-filter-actor"
                />
              </div>
              <div className="flex items-end">
                <button
                  type="button"
                  onClick={() => loadAuditLogs(auditFilter)}
                  className="te-btn-outline w-full"
                  data-testid="audit-refresh-btn"
                >
                  {auditLoading ? "Loading…" : "Refresh"}
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
            {auditLogs.length === 0 && (
              <div className="p-6 text-sm text-zinc-500" data-testid="audit-empty">
                {auditLoading ? "Loading audit trail…" : "No audit entries match these filters yet."}
              </div>
            )}
            {auditLogs.map((row) => (
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
      )}

      {tab === "feedback" && (
        <div className="mt-6 space-y-4" data-testid="feedback-admin-section">
          {feedback.summary && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-[1px] bg-black/10 border border-black/10">
              <div className="bg-white p-4"><div className="te-overline text-[9px]">Total</div><div className="text-2xl font-black font-mono">{feedback.summary.total}</div></div>
              <div className="bg-white p-4"><div className="te-overline text-[9px] text-[#B5121B]">New</div><div className="text-2xl font-black font-mono text-[#B5121B]">{feedback.summary.new}</div></div>
              <div className="bg-white p-4"><div className="te-overline text-[9px] text-amber-700">In progress</div><div className="text-2xl font-black font-mono text-amber-700">{feedback.summary.in_progress}</div></div>
              <div className="bg-white p-4"><div className="te-overline text-[9px] text-emerald-700">Resolved</div><div className="text-2xl font-black font-mono text-emerald-700">{feedback.summary.resolved}</div></div>
            </div>
          )}

          <div className="te-card p-4">
            <div className="te-overline mb-3">Filters</div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <select className="te-input" value={feedbackFilter.status} onChange={(e) => setFeedbackFilter({ ...feedbackFilter, status: e.target.value })} data-testid="feedback-filter-status">
                <option value="all">All statuses</option>
                <option value="new">New</option>
                <option value="in_progress">In progress</option>
                <option value="resolved">Resolved</option>
              </select>
              <select className="te-input" value={feedbackFilter.category} onChange={(e) => setFeedbackFilter({ ...feedbackFilter, category: e.target.value })} data-testid="feedback-filter-category">
                <option value="all">All categories</option>
                <option value="general">General</option>
                <option value="booking_issue">Booking issue</option>
                <option value="complaint">Complaint</option>
                <option value="suggestion">Suggestion</option>
                <option value="praise">Praise</option>
              </select>
              <button type="button" onClick={() => loadFeedback(feedbackFilter)} className="te-btn-outline" data-testid="feedback-refresh-btn">Refresh</button>
            </div>
          </div>

          <div className="space-y-3">
            {feedback.items.length === 0 && (
              <div className="te-card p-6 text-sm text-zinc-500" data-testid="feedback-empty">
                No feedback matches these filters yet.
              </div>
            )}
            {feedback.items.map((f) => {
              const catLabel = { general: "General", booking_issue: "Booking issue", complaint: "Complaint", suggestion: "Suggestion", praise: "Praise" }[f.category] || f.category;
              const statusColor = f.status === "new" ? "bg-[#B5121B] text-white" : f.status === "in_progress" ? "bg-amber-500 text-white" : "bg-emerald-600 text-white";
              return (
                <div key={f.id} className="te-card p-5 space-y-2" data-testid={`feedback-row-${f.id}`}>
                  <div className="flex flex-wrap items-center gap-3 justify-between">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono font-black text-sm">{f.reference}</span>
                      <span className="inline-block px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider bg-zinc-200">{catLabel}</span>
                      <span className={`inline-block px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${statusColor}`}>{f.status.replace("_", " ")}</span>
                      {f.rating && <span className="text-amber-500 text-sm">{"★".repeat(f.rating)}{"☆".repeat(5 - f.rating)}</span>}
                    </div>
                    <div className="font-mono text-[10px] text-zinc-500">{new Date(f.created_at).toLocaleString()}</div>
                  </div>
                  <div className="text-xs font-mono text-zinc-600">
                    <a href={`mailto:${f.email}`} className="text-[#002FA7] font-bold">{f.email}</a>
                    {f.name && <span> · {f.name}</span>}
                    {f.booking_reference && <span> · Booking: <b>{f.booking_reference}</b></span>}
                  </div>
                  <div className="text-sm text-zinc-800 whitespace-pre-wrap border-l-2 border-[#002FA7] pl-3 py-1">{f.message}</div>
                  <div className="flex gap-2 flex-wrap pt-2">
                    {f.status !== "in_progress" && (
                      <button type="button" onClick={async () => {
                        await api.patch(`/admin/feedback/${f.id}`, { status: "in_progress" });
                        loadFeedback(feedbackFilter);
                      }} className="text-[10px] font-mono font-bold text-amber-700 hover:underline uppercase tracking-wider" data-testid={`feedback-mark-progress-${f.id}`}>
                        Mark in progress
                      </button>
                    )}
                    {f.status !== "resolved" && (
                      <button type="button" onClick={async () => {
                        await api.patch(`/admin/feedback/${f.id}`, { status: "resolved" });
                        loadFeedback(feedbackFilter);
                      }} className="text-[10px] font-mono font-bold text-emerald-700 hover:underline uppercase tracking-wider" data-testid={`feedback-mark-resolved-${f.id}`}>
                        Mark resolved
                      </button>
                    )}
                    {f.status === "resolved" && (
                      <button type="button" onClick={async () => {
                        await api.patch(`/admin/feedback/${f.id}`, { status: "new" });
                        loadFeedback(feedbackFilter);
                      }} className="text-[10px] font-mono font-bold text-zinc-700 hover:underline uppercase tracking-wider" data-testid={`feedback-reopen-${f.id}`}>
                        Reopen
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {tab === "admins" && (
        <div className="mt-6 space-y-6" data-testid="admins-section">
          {/* Invite form — super-admin only */}
          {isSuperAdmin ? (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                setAdminMsg("");
                try {
                  await api.post("/admin/admins/invite", adminInviteForm);
                  setAdminMsg(`Invite sent to ${adminInviteForm.email}. They have 7 days to accept.`);
                  setAdminInviteForm({ email: "", full_name: "", role: "admin" });
                  loadAdmins();
                } catch (err) {
                  setAdminMsg(`Error: ${err?.response?.data?.detail || "could not send invite"}`);
                }
              }}
              className="te-card p-6 space-y-4"
              data-testid="admin-invite-form"
            >
              <div className="te-overline">Invite a new admin</div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div>
                  <label className="te-label">Full name</label>
                  <input
                    required
                    className="te-input"
                    value={adminInviteForm.full_name}
                    onChange={(e) => setAdminInviteForm({ ...adminInviteForm, full_name: e.target.value })}
                    data-testid="admin-invite-name"
                  />
                </div>
                <div>
                  <label className="te-label">Email</label>
                  <input
                    type="email"
                    required
                    className="te-input"
                    value={adminInviteForm.email}
                    onChange={(e) => setAdminInviteForm({ ...adminInviteForm, email: e.target.value })}
                    data-testid="admin-invite-email"
                  />
                </div>
                <div>
                  <label className="te-label">Role</label>
                  <select
                    className="te-input"
                    value={adminInviteForm.role}
                    onChange={(e) => setAdminInviteForm({ ...adminInviteForm, role: e.target.value })}
                    data-testid="admin-invite-role"
                  >
                    <option value="admin">Admin</option>
                    <option value="super_admin">Super-admin</option>
                  </select>
                </div>
              </div>
              <div className="flex items-center justify-between">
                <div className="text-[10px] font-mono text-zinc-500 leading-relaxed">
                  INVITE SENT VIA EMAIL · EXPIRES IN 7 DAYS · RECIPIENT SETS OWN PASSWORD
                </div>
                <button className="te-btn-primary" data-testid="admin-invite-submit">
                  Send invite
                </button>
              </div>
              {adminMsg && <div className="text-xs font-bold" data-testid="admin-invite-msg">{adminMsg}</div>}
            </form>
          ) : (
            <div className="te-card p-4 text-xs text-zinc-600" data-testid="admin-non-super-notice">
              Only super-admins can invite or manage other admins. You can still view the list below.
            </div>
          )}

          {/* Pending invites */}
          {admins.pending_invites?.length > 0 && (
            <div className="te-card p-0 overflow-hidden">
              <div className="px-4 py-3 bg-amber-50 border-b border-amber-200 text-[10px] font-mono uppercase tracking-wider text-amber-700">
                Pending invites ({admins.pending_invites.length})
              </div>
              {admins.pending_invites.map((inv) => (
                <div key={inv.id} className="flex items-center justify-between px-4 py-3 border-t border-black/5 text-xs">
                  <div>
                    <div className="font-bold">{inv.full_name} <span className="text-zinc-500">· {inv.email}</span></div>
                    <div className="font-mono text-[10px] text-zinc-500">
                      Role: {inv.role} · Invited by {inv.invited_by_email} · Expires {new Date(inv.expires_at).toLocaleDateString()}
                    </div>
                  </div>
                  {isSuperAdmin && (
                    <button
                      type="button"
                      onClick={async () => {
                        if (!window.confirm(`Cancel invite for ${inv.email}?`)) return;
                        try {
                          await api.delete(`/admin/admins/invites/${inv.id}`);
                          loadAdmins();
                        } catch (err) {
                          alert(err?.response?.data?.detail || "Could not cancel");
                        }
                      }}
                      className="text-[10px] font-mono font-bold text-red-600 hover:text-red-800 uppercase tracking-wider"
                      data-testid={`admin-cancel-invite-${inv.id}`}
                    >
                      Cancel
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Admin roster */}
          <div className="te-card p-0 overflow-hidden">
            <div className="grid grid-cols-12 gap-2 px-4 py-3 bg-black text-white text-[10px] font-mono uppercase tracking-wider">
              <div className="col-span-3">Name</div>
              <div className="col-span-3">Email</div>
              <div className="col-span-2">Role</div>
              <div className="col-span-2">Last login</div>
              <div className="col-span-2 text-right">Actions</div>
            </div>
            {admins.admins?.length === 0 && (
              <div className="p-6 text-sm text-zinc-500" data-testid="admin-empty">
                No admins yet.
              </div>
            )}
            {admins.admins?.map((a) => {
              const isSelf = a.id === user?.id;
              const isSuper = a.role === "super_admin";
              return (
                <div
                  key={a.id}
                  className={`grid grid-cols-12 gap-2 px-4 py-3 border-t border-black/5 text-xs items-center ${a.is_active === false ? "opacity-50" : ""}`}
                  data-testid={`admin-row-${a.id}`}
                >
                  <div className="col-span-3 font-bold truncate">
                    {a.full_name || "—"}
                    {isSelf && <span className="ml-2 text-[9px] font-mono text-zinc-400">YOU</span>}
                  </div>
                  <div className="col-span-3 font-mono text-[11px] truncate">{a.email}</div>
                  <div className="col-span-2">
                    <span className={`inline-block px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${isSuper ? "bg-[#002FA7] text-white" : "bg-zinc-200 text-zinc-800"}`}>
                      {isSuper ? "Super" : "Admin"}
                    </span>
                    {a.is_active === false && (
                      <span className="ml-1 inline-block px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider bg-zinc-900 text-white">
                        Off
                      </span>
                    )}
                  </div>
                  <div className="col-span-2 font-mono text-[10px] text-zinc-500">
                    {a.last_login_at ? new Date(a.last_login_at).toLocaleString() : "Never"}
                  </div>
                  <div className="col-span-2 text-right flex gap-2 justify-end">
                    {isSuperAdmin && !isSelf && (
                      <>
                        <button
                          type="button"
                          onClick={async () => {
                            const newRole = isSuper ? "admin" : "super_admin";
                            if (!window.confirm(`Change ${a.email} to ${newRole}?`)) return;
                            try {
                              await api.patch(`/admin/admins/${a.id}/role`, { role: newRole });
                              loadAdmins();
                            } catch (err) { alert(err?.response?.data?.detail || "failed"); }
                          }}
                          className="text-[10px] font-mono font-bold uppercase tracking-wider text-[#002FA7] hover:underline"
                          data-testid={`admin-toggle-role-${a.id}`}
                        >
                          {isSuper ? "Demote" : "Promote"}
                        </button>
                        <button
                          type="button"
                          onClick={async () => {
                            const next = a.is_active === false;
                            if (!window.confirm(`${next ? "Reactivate" : "Deactivate"} ${a.email}?`)) return;
                            try {
                              await api.patch(`/admin/admins/${a.id}/active`, { is_active: next });
                              loadAdmins();
                            } catch (err) { alert(err?.response?.data?.detail || "failed"); }
                          }}
                          className="text-[10px] font-mono font-bold uppercase tracking-wider text-zinc-700 hover:underline"
                          data-testid={`admin-toggle-active-${a.id}`}
                        >
                          {a.is_active === false ? "Reactivate" : "Deactivate"}
                        </button>
                        <button
                          type="button"
                          onClick={async () => {
                            if (!window.confirm(`Revoke admin access for ${a.email}? They'll still be able to log in as a normal user.`)) return;
                            try {
                              await api.delete(`/admin/admins/${a.id}`);
                              loadAdmins();
                            } catch (err) { alert(err?.response?.data?.detail || "failed"); }
                          }}
                          className="text-[10px] font-mono font-bold uppercase tracking-wider text-red-600 hover:underline"
                          data-testid={`admin-revoke-${a.id}`}
                        >
                          Revoke
                        </button>
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function SummaryCard({ label, value, accent }) {
  const accentClass =
    accent === "emerald"
      ? "text-emerald-700"
      : accent === "red"
      ? "text-red-700"
      : accent === "zinc"
      ? "text-zinc-600"
      : "text-black";
  return (
    <div className="te-card p-4">
      <div className="te-overline text-[9px] text-zinc-500">{label}</div>
      <div className={`text-2xl font-black font-mono mt-1 ${accentClass}`}>{value}</div>
    </div>
  );
}
