import React, { useEffect, useState } from "react";
import api from "../../../lib/api";

export default function AddScheduleTab() {
  const [terminals, setTerminals] = useState([]);
  const [routes, setRoutes] = useState([]);
  const [busTypes, setBusTypes] = useState([]);
  const [form, setForm] = useState({
    route_id: "", trip_no: "",
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
    api.get("/admin/routes").then(({ data }) => setRoutes(data)).catch(() => {});
    api.get("/bus-types").then(({ data }) => setBusTypes(data)).catch(() => {});
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
      const payload = {
        ...form,
        adult_fare: parseFloat(form.adult_fare),
        child_fare: parseFloat(form.child_fare),
        total_seats: parseInt(form.total_seats, 10),
      };
      if (!payload.route_id) delete payload.route_id;
      if (!payload.trip_no) delete payload.trip_no;
      const { data } = await api.post("/admin/schedules/bulk", payload);
      setMsg(`Created ${data.created} schedule(s)${data.skipped_duplicates ? ` · ${data.skipped_duplicates} skipped (duplicates)` : ""} · billed in ${data.currency.toUpperCase()}.`);
    } catch (e) {
      setMsg(e?.response?.data?.detail || "Failed");
    }
  };

  // When a route is picked, the from/to fields DEFAULT to the route's
  // origin → destination but stay editable — admin may want to sell a specific
  // segment (e.g. KL→Melaka only). Sibling schedules for the same physical bus
  // are auto-linked via `bus_instance_id` on the backend so seats are shared.
  const selectedRoute = form.route_id ? routes.find((r) => r.id === form.route_id) : null;
  const routeOriginId = selectedRoute
    ? [...(selectedRoute.boarding_stops || [])].sort((a, b) => (a.offset_min ?? 0) - (b.offset_min ?? 0))[0]?.terminal_id
    : null;
  const routeDestinationId = selectedRoute
    ? [...(selectedRoute.alighting_stops || [])].sort((a, b) => (a.offset_min ?? 0) - (b.offset_min ?? 0)).slice(-1)[0]?.terminal_id
    : null;
  // Auto-fill from/to when a route is FIRST picked. Once set, respect any user
  // override so they can pick a specific segment.
  useEffect(() => {
    if (selectedRoute && routeOriginId && routeDestinationId) {
      setForm((prev) => ({
        ...prev,
        from_terminal_id: prev.from_terminal_id || routeOriginId,
        to_terminal_id: prev.to_terminal_id || routeDestinationId,
      }));
    }
  }, [form.route_id, routeOriginId, routeDestinationId, selectedRoute]);
  // With a route linked, narrow the pickers to the route's stops so admin
  // can't accidentally pick a terminal that isn't part of the route.
  const fromChoices = selectedRoute
    ? terminals.filter((t) => selectedRoute.boarding_stops?.some((s) => s.terminal_id === t.id))
    : terminals;
  const toChoices = selectedRoute
    ? terminals.filter((t) => selectedRoute.alighting_stops?.some((s) => s.terminal_id === t.id))
    : terminals;

  // Auto-apply the route's pairing fare when both from/to are selected on a route.
  useEffect(() => {
    if (!selectedRoute || !form.from_terminal_id || !form.to_terminal_id) return;
    const pair = selectedRoute.pairings?.find(
      (p) => p.pickup_id === form.from_terminal_id && p.dropoff_id === form.to_terminal_id,
    );
    if (pair) {
      setForm((prev) => ({
        ...prev,
        adult_fare: pair.adult_fare ?? prev.adult_fare,
        child_fare: pair.child_fare ?? prev.child_fare,
      }));
    }
  }, [form.route_id, form.from_terminal_id, form.to_terminal_id, selectedRoute]);

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
        <div className="md:col-span-2">
          <label className="te-label">Link to route <span className="text-zinc-400">(optional — auto-fills fare from pairings)</span></label>
          <select
            className="te-input"
            value={form.route_id}
            onChange={(e) => setForm({ ...form, route_id: e.target.value, from_terminal_id: "", to_terminal_id: "" })}
            data-testid="sched-route"
          >
            <option value="">— None (create unlinked) —</option>
            {routes.map((r) => (
              <option key={r.id} value={r.id}>
                {r.code} · {r.name} ({r.origin_city} → {r.destination_city})
              </option>
            ))}
          </select>
          {selectedRoute && (
            <div className="text-[10px] font-mono text-emerald-700 mt-1">
              ✓ Linked · {selectedRoute.boarding_stops?.length ?? 0} pickup(s) · {selectedRoute.alighting_stops?.length ?? 0} drop-off(s) · {selectedRoute.pairings?.length ?? 0} pairings
            </div>
          )}
        </div>
        {selectedRoute && (
          <div className="md:col-span-2 border-l-2 border-[#002FA7] pl-3 py-1 text-[11px] font-mono text-zinc-700 leading-relaxed" data-testid="route-schedule-banner">
            <b>Route linked · shared seat pool.</b> Any schedule you create on this
            route + date + time is the SAME physical bus — seats are automatically
            shared across every segment you sell (booking seat 5A on the KL→Melaka
            row instantly blocks 5A on the KL→SG or Melaka→JB row for the same bus).
            Pick From/To below to sell a specific segment; defaults are the route&apos;s
            origin → final destination.
          </div>
        )}
        <div>
          <label className="te-label">From Terminal{selectedRoute && <span className="text-emerald-600 text-[10px] ml-1">· must be a route pickup stop</span>}</label>
          <select
            className="te-input"
            required
            value={form.from_terminal_id}
            onChange={(e) => setForm({ ...form, from_terminal_id: e.target.value })}
            data-testid="sched-from"
          >
            <option value="">Select</option>
            {fromChoices.map((t) => <option key={t.id} value={t.id}>{t.city} · {t.name}</option>)}
          </select>
        </div>
        <div>
          <label className="te-label">To Terminal{selectedRoute && <span className="text-emerald-600 text-[10px] ml-1">· must be a route drop-off stop</span>}</label>
          <select
            className="te-input"
            required
            value={form.to_terminal_id}
            onChange={(e) => setForm({ ...form, to_terminal_id: e.target.value })}
            data-testid="sched-to"
          >
            <option value="">Select</option>
            {toChoices.map((t) => <option key={t.id} value={t.id}>{t.city} · {t.name}</option>)}
          </select>
        </div>
        <div className="md:col-span-2">
          <label className="te-label">Trip No <span className="text-zinc-400">(CTS, ≤10 chars, opt)</span></label>
          <input className="te-input font-mono" maxLength={10} value={form.trip_no} onChange={(e) => setForm({ ...form, trip_no: e.target.value.toUpperCase() })} placeholder="SQ001" data-testid="sched-trip-no" />
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
        <div className="md:col-span-2">
          <label className="te-label">Bus type</label>
          <select
            className="te-input"
            value={form.bus_type}
            onChange={(e) => {
              const chosenName = e.target.value;
              const bt = busTypes.find((b) => b.name === chosenName);
              setForm((prev) => ({
                ...prev,
                bus_type: chosenName,
                total_seats: bt?.seat_count ?? prev.total_seats,
              }));
            }}
            data-testid="sched-bus-type"
          >
            {busTypes.length === 0 && (
              <option value="Standard">Standard (40 seats · fallback)</option>
            )}
            {busTypes.map((bt) => (
              <option key={bt.id} value={bt.name}>
                {bt.name} · {bt.seat_count} seats · {bt.layout || "2+2"}
              </option>
            ))}
          </select>
          <div className="text-[10px] font-mono text-zinc-500 mt-1">
            Pick a bus and seat count fills in automatically. Manage bus types in the &quot;Bus Types&quot; tab.
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
        <div className="md:col-span-2">
          <label className="te-label">Total seats (auto-filled from bus type)</label>
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
            Override only if this specific trip uses a different capacity than the bus type default.
          </div>
        </div>
        <div className="md:col-span-2 text-[10px] font-mono text-zinc-500 leading-relaxed border-l-2 border-[#002FA7] pl-3 py-1">
          OPERATOR · QISTNA EXPRESS (single operator)<br/>
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
