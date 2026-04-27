import React, { useEffect, useState } from "react";
import api from "../../../lib/api";

export default function AddScheduleTab() {
  const [terminals, setTerminals] = useState([]);
  const [form, setForm] = useState({
    from_terminal_id: "", to_terminal_id: "",
    start_date: "", end_date: "",
    days_of_week: [0, 1, 2, 3, 4, 5, 6],
    departure_time: "08:00", arrival_time: "12:00",
    bus_type: "Standard", adult_fare: 50, child_fare: 25, total_seats: 40,
  });
  const [msg, setMsg] = useState("");
  const [fxRate, setFxRate] = useState(3.5);
  const [fxRateInput, setFxRateInput] = useState("3.5");
  const [fxMsg, setFxMsg] = useState("");

  useEffect(() => {
    api.get("/admin/terminals").then(({ data }) => setTerminals(data)).catch(() => {});
    api.get("/settings").then(({ data }) => {
      setFxRate(data.sgd_to_myr_rate);
      setFxRateInput(String(data.sgd_to_myr_rate));
    }).catch(() => {});
  }, []);

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

  const toggleDay = (d) => {
    const set = new Set(form.days_of_week);
    if (set.has(d)) set.delete(d); else set.add(d);
    setForm({ ...form, days_of_week: [...set].sort() });
  };

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
    } catch (e) {
      setMsg(e?.response?.data?.detail || "Failed");
    }
  };

  // fxRate is read in the saveFxRate success message; suppress unused var lint
  void fxRate;

  return (
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
  );
}
