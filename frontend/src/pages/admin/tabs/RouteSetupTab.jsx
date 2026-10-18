import React, { useEffect, useMemo, useState } from "react";
import api from "../../../lib/api";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DEFAULT_FARE = 55;

export default function RouteSetupTab() {
  const [stops, setStops] = useState([]);
  const [busTypes, setBusTypes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [createdRoute, setCreatedRoute] = useState(null);
  const [stopTimes, setStopTimes] = useState({});
  const [busInstanceKey, setBusInstanceKey] = useState("");
  const [form, setForm] = useState({
    code: "", name: "", origin_city: "", destination_city: "",
    pickupIds: [], dropoffIds: [], fare: String(DEFAULT_FARE), childFare: "25",
    start_date: "", end_date: "", days_of_week: [0, 1, 2, 3, 4, 5, 6],
    departure_time: "08:00", arrival_time: "12:00", trip_no: "",
    bus_type: "Standard", total_seats: 40,
  });

  useEffect(() => {
    Promise.all([
      api.get("/admin/terminals").then(({ data }) => setStops(data)),
      api.get("/bus-types").then(({ data }) => setBusTypes(data)),
    ]).catch((e) => setError(e?.response?.data?.detail || "Could not load stops or bus types."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const selected = busTypes.find((b) => b.name === form.bus_type);
    if (selected) setForm((prev) => ({ ...prev, total_seats: selected.seat_count }));
  }, [form.bus_type, busTypes]);

  const stopById = useMemo(() => Object.fromEntries(stops.map((s) => [s.id, s])), [stops]);
  const pickups = stops.filter((s) => s.is_pickup !== false);
  const dropoffs = stops.filter((s) => s.is_dropoff !== false);
  const validPairCount = form.pickupIds.reduce((n, id) => n + form.dropoffIds.filter((dropId) => dropId !== id).length, 0);
  const selectedStopIds = [...new Set([...form.pickupIds, ...form.dropoffIds])];
  const toMinutes = (value) => {
    if (!/^\\d{2}:\\d{2}$/.test(value || "")) return NaN;
    const [hours, minutes] = value.split(":").map(Number);
    return hours * 60 + minutes;
  };
  const offsetForStop = (id) => {
    const base = toMinutes(form.departure_time), stop = toMinutes(stopTimes[id]);
    if (!Number.isFinite(base) || !Number.isFinite(stop)) return NaN;
    return (stop - base + 1440) % 1440;
  };
  const orderedSelectedStops = selectedStopIds.map((id) => ({ id, offset: offsetForStop(id) })).sort((a, b) => a.offset - b.offset);
  const timeValidation = (() => {
    if (!form.pickupIds.length || !form.dropoffIds.length) return "";
    if (selectedStopIds.some((id) => !Number.isFinite(offsetForStop(id)))) return "Enter a planned time for every selected stop.";
    if (orderedSelectedStops.some((stop, i) => i > 0 && stop.offset === orderedSelectedStops[i - 1].offset)) return "Each selected stop needs a different time so journey order is unambiguous.";
    return "";
  })();
  const toggleId = (field, id) => {
    setForm((prev) => ({ ...prev, [field]: prev[field].includes(id) ? prev[field].filter((v) => v !== id) : [...prev[field], id] }));
    setStopTimes((prev) => ({ ...prev, [id]: prev[id] || form.departure_time }));
  };
  const changeDepartureTime = (value) => {
    setForm((prev) => ({ ...prev, departure_time: value }));
    setStopTimes((prev) => {
      const next = { ...prev };
      const firstPickup = form.pickupIds[0];
      if (firstPickup && (next[firstPickup] === form.departure_time || !next[firstPickup])) next[firstPickup] = value;
      return next;
    });
  };
  const toggleDay = (day) => setForm((prev) => ({
    ...prev, days_of_week: prev.days_of_week.includes(day)
      ? prev.days_of_week.filter((d) => d !== day)
      : [...prev.days_of_week, day].sort(),
  }));

  const createRouteAndSchedules = async (event) => {
    event.preventDefault();
    setError("");
    setMessage("");
    if (!form.code.trim() || !form.name.trim() || !form.origin_city.trim() || !form.destination_city.trim()) {
      setError("Enter a route code, name, origin city and destination city.");
      return;
    }
    if (!form.pickupIds.length || !form.dropoffIds.length) {
      setError("Choose at least one pickup stop and one drop-off stop.");
      return;
    }
    if (!form.start_date || !form.end_date || form.end_date < form.start_date) {
      setError("Choose a valid first and last trip date.");
      return;
    }
    if (!form.days_of_week.length) {
      setError("Select at least one operating day.");
      return;
    }
    if (timeValidation) {
      setError(timeValidation);
      return;
    }
    const adultFare = Number(form.fare);
    const childFare = Number(form.childFare);
    if (!Number.isFinite(adultFare) || adultFare < 0 || !Number.isFinite(childFare) || childFare < 0) {
      setError("Enter valid adult and child fares.");
      return;
    }

    setSaving(true);
    let route = createdRoute;
    const scheduleBusKey = busInstanceKey || `route-setup-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    try {
      if (!route) {
        const boarding_stops = form.pickupIds.map((terminal_id) => ({ terminal_id, offset_min: offsetForStop(terminal_id) }));
        const alighting_stops = form.dropoffIds.map((terminal_id) => ({ terminal_id, offset_min: offsetForStop(terminal_id) }));
        const pairings = form.pickupIds.flatMap((pickup_id) =>
          form.dropoffIds.filter((dropoff_id) => dropoff_id !== pickup_id).map((dropoff_id) => ({
            pickup_id, dropoff_id, adult_fare: adultFare, child_fare: childFare,
            senior_fare: adultFare, oku_fare: adultFare,
            currency: stopById[pickup_id]?.country === "SG" ? "sgd" : "myr",
            cts_route_code: "",
          }))
        );
        if (!pairings.length) throw new Error("Choose at least one valid pickup-to-drop-off combination using different stops.");
        const response = await api.post("/admin/routes", {
          code: form.code.trim().toUpperCase(),
          name: form.name.trim(),
          origin_city: form.origin_city.trim(),
          destination_city: form.destination_city.trim(),
          direction: form.code.trim().toUpperCase(),
          is_active: true, boarding_stops, alighting_stops, pairings,
        });
        route = response.data;
        setCreatedRoute(route);
        setBusInstanceKey(scheduleBusKey);
        setMessage("Route saved. Generating schedules for each configured pickup/drop-off segment…");
      }

      const pairs = route.pairings || [];
      if (!pairs.length) throw new Error("Route was saved but has no fare pairings. Configure pairings in Routes before generating schedules.");
      let created = 0;
      let skipped = 0;
      const errors = [];
      for (const pair of pairs) {
        try {
          const payload = {
            route_id: route.id,
            trip_no: form.trip_no.trim().toUpperCase() || undefined,
            from_terminal_id: pair.pickup_id,
            to_terminal_id: pair.dropoff_id,
            start_date: form.start_date,
            end_date: form.end_date,
            days_of_week: form.days_of_week,
            departure_time: stopTimes[pair.pickup_id] || form.departure_time,
            arrival_time: stopTimes[pair.dropoff_id] || form.arrival_time,
            bus_instance_key: scheduleBusKey,
            bus_type: form.bus_type,
            adult_fare: Number(pair.adult_fare ?? adultFare),
            child_fare: Number(pair.child_fare ?? childFare),
            total_seats: Number(form.total_seats),
          };
          const { data } = await api.post("/admin/schedules/bulk", payload);
          created += Number(data.created || 0);
          skipped += Number(data.skipped_duplicates || 0);
        } catch (e) {
          errors.push(`${stopById[pair.pickup_id]?.city || pair.pickup_id} → ${stopById[pair.dropoff_id]?.city || pair.dropoff_id}: ${e?.response?.data?.detail || e.message || "failed"}`);
        }
      }
      const summary = `Route ${route.code} saved. Created ${created} schedule(s) across ${pairs.length} segment(s)${skipped ? `; ${skipped} duplicate schedule(s) skipped` : ""}.`;
      setMessage(errors.length ? `${summary} Some segments failed: ${errors.join(" · ")}. You can retry schedule generation without creating the route again.` : `${summary} All segments share one physical-bus seat pool, with seats blocked only on overlapping journey legs.`);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || "Route setup failed.");
    } finally {
      setSaving(false);
    }
  };

  const reset = () => {
    setCreatedRoute(null);
    setStopTimes({});
    setBusInstanceKey("");
    setMessage("");
    setError("");
    setForm({
      code: "", name: "", origin_city: "", destination_city: "",
      pickupIds: [], dropoffIds: [], fare: String(DEFAULT_FARE), childFare: "25",
      start_date: "", end_date: "", days_of_week: [0, 1, 2, 3, 4, 5, 6],
      departure_time: "08:00", arrival_time: "12:00", trip_no: "",
      bus_type: busTypes[0]?.name || "Standard", total_seats: busTypes[0]?.seat_count || 40,
    });
  };

  if (loading) return <div className="mt-6 text-sm text-zinc-500">Loading stops and bus types…</div>;

  return (
    <div className="mt-6 max-w-6xl space-y-5" data-testid="route-setup-tab">
      <div className="te-card p-5 md:p-7">
        <div className="te-overline">Staff workflow · one page</div>
        <h2 className="text-2xl font-black mt-1">Create route + schedules</h2>
        <p className="text-sm text-zinc-600 mt-2 max-w-3xl">
          Set up the route, select stops, choose fares and service dates, then generate all sellable pickup-to-drop-off segments in one submission. Existing Routes and Add Schedule tabs remain available for edits and special cases.
        </p>
        {createdRoute && <div className="mt-4 p-3 bg-emerald-50 border border-emerald-200 text-sm" data-testid="route-setup-created">Route <b>{createdRoute.code}</b> has been saved. Retrying this form will reuse it instead of creating a duplicate route.</div>}
      </div>

      <form onSubmit={createRouteAndSchedules} className="space-y-5">
        <section className="te-card p-5 md:p-7">
          <h3 className="font-black text-lg mb-4">1. Route details</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <label><span className="te-label">Route code</span><input className="te-input uppercase" required value={form.code} disabled={!!createdRoute} placeholder="KL-SG" onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} /></label>
            <label className="lg:col-span-2"><span className="te-label">Route name</span><input className="te-input" required value={form.name} disabled={!!createdRoute} placeholder="Kuala Lumpur to Singapore" onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
            <label><span className="te-label">Trip number (optional)</span><input className="te-input uppercase" maxLength={10} value={form.trip_no} placeholder="SQ001" onChange={(e) => setForm({ ...form, trip_no: e.target.value.toUpperCase() })} /></label>
            <label><span className="te-label">Origin city</span><input className="te-input" required value={form.origin_city} disabled={!!createdRoute} placeholder="Kuala Lumpur" onChange={(e) => setForm({ ...form, origin_city: e.target.value })} /></label>
            <label><span className="te-label">Destination city</span><input className="te-input" required value={form.destination_city} disabled={!!createdRoute} placeholder="Singapore" onChange={(e) => setForm({ ...form, destination_city: e.target.value })} /></label>
          </div>
        </section>

        <section className="te-card p-5 md:p-7">
          <h3 className="font-black text-lg mb-1">2. Stops along the journey</h3>
          <p className="text-xs text-zinc-500 mb-4">Select the pickup and drop-off points served by this route. Planned times below determine stop order and each offset from the first departure.</p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            <div>
              <div className="te-overline mb-2">Pickup stops ({form.pickupIds.length})</div>
              <div className="max-h-80 overflow-auto border border-black/10 rounded p-2 space-y-1">
                {pickups.map((s) => <label key={s.id} className="flex items-start gap-2 p-2 hover:bg-zinc-50 text-sm"><input type="checkbox" className="mt-1" checked={form.pickupIds.includes(s.id)} disabled={!!createdRoute} onChange={() => toggleId("pickupIds", s.id)} /><span><b>{s.city}</b><span className="block text-xs text-zinc-500">{s.name}{s.code ? ` · ${s.code}` : ""}</span></span></label>)}
                {!pickups.length && <p className="text-xs text-zinc-500 p-2">No pickup-enabled stops. Add them in Stops first.</p>}
              </div>
            </div>
            <div>
              <div className="te-overline mb-2">Drop-off stops ({form.dropoffIds.length})</div>
              <div className="max-h-80 overflow-auto border border-black/10 rounded p-2 space-y-1">
                {dropoffs.map((s) => <label key={s.id} className="flex items-start gap-2 p-2 hover:bg-zinc-50 text-sm"><input type="checkbox" className="mt-1" checked={form.dropoffIds.includes(s.id)} disabled={!!createdRoute} onChange={() => toggleId("dropoffIds", s.id)} /><span><b>{s.city}</b><span className="block text-xs text-zinc-500">{s.name}{s.code ? ` · ${s.code}` : ""}</span></span></label>)}
                {!dropoffs.length && <p className="text-xs text-zinc-500 p-2">No drop-off-enabled stops. Add them in Stops first.</p>}
              </div>
            </div>
          </div>
          <div className="mt-3 text-xs text-zinc-600">{validPairCount} pickup/drop-off combinations will be configured. Each selected combination becomes searchable inventory.</div>
          <div className="mt-6 border-t border-black/10 pt-5">
            <h4 className="font-bold mb-1">Planned stop times</h4>
            <p className="text-xs text-zinc-500 mb-3">Enter the local time at every selected stop. Offsets are calculated from the first departure; times after midnight are supported.</p>
            {!selectedStopIds.length && <p className="text-sm text-zinc-500">Select stops above to enter their times.</p>}
            <div className="space-y-2">
              {orderedSelectedStops.map(({ id, offset }) => {
                const stop = stopById[id];
                const roles = [form.pickupIds.includes(id) ? "Pickup" : "", form.dropoffIds.includes(id) ? "Drop-off" : ""].filter(Boolean).join(" / ");
                return <div key={id} className="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_140px_110px] gap-3 items-center border border-black/10 rounded p-3">
                  <div><b className="text-sm">{stop?.city || id}</b><span className="block text-xs text-zinc-500">{stop?.name || ""} · {roles}</span></div>
                  <label><span className="te-label">Planned time</span><input type="time" required className="te-input" disabled={!!createdRoute} value={stopTimes[id] || ""} onChange={(e) => setStopTimes((prev) => ({ ...prev, [id]: e.target.value }))} /></label>
                  <div className="text-xs text-zinc-600 sm:text-right"><span className="block text-zinc-400">Offset from start</span>{Number.isFinite(offset) ? `${Math.floor(offset / 60)}h ${offset % 60}m` : "Enter time"}</div>
                </div>;
              })}
            </div>
          </div>
        </section>

        <section className="te-card p-5 md:p-7">
          <h3 className="font-black text-lg mb-4">3. Fares and schedule</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <label><span className="te-label">Default adult fare</span><input type="number" min="0" step="0.01" required className="te-input" value={form.fare} onChange={(e) => setForm({ ...form, fare: e.target.value })} /></label>
            <label><span className="te-label">Default child fare</span><input type="number" min="0" step="0.01" required className="te-input" value={form.childFare} onChange={(e) => setForm({ ...form, childFare: e.target.value })} /></label>
            <label><span className="te-label">First trip date</span><input type="date" required className="te-input" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} /></label>
            <label><span className="te-label">Last trip date (inclusive)</span><input type="date" required min={form.start_date || undefined} className="te-input" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} /></label>
            <label><span className="te-label">First departure time</span><input type="time" required className="te-input" value={form.departure_time} onChange={(e) => changeDepartureTime(e.target.value)} /><span className="block text-xs text-zinc-500 mt-1">Baseline for stop offsets; keep it equal to the first stop time.</span></label>
            <label><span className="te-label">Final arrival (fallback)</span><input type="time" required className="te-input" value={form.arrival_time} onChange={(e) => setForm({ ...form, arrival_time: e.target.value })} /><span className="block text-xs text-zinc-500 mt-1">Segment arrivals use the selected drop-off stop time.</span></label>
            <label><span className="te-label">Bus type</span><select className="te-input" value={form.bus_type} onChange={(e) => setForm({ ...form, bus_type: e.target.value })}>{busTypes.map((b) => <option key={b.id} value={b.name}>{b.name} · {b.seat_count} seats</option>)}{!busTypes.length && <option value="Standard">Standard</option>}</select></label>
            <label><span className="te-label">Seat capacity</span><input type="number" min="12" max="60" required className="te-input" value={form.total_seats} onChange={(e) => setForm({ ...form, total_seats: e.target.value })} /></label>
          </div>
          <div className="mt-5">
            <div className="te-label mb-2">Operating days</div>
            <div className="grid grid-cols-7 gap-1">{DAYS.map((day, i) => <button key={day} type="button" onClick={() => toggleDay(i)} className={`py-2 text-xs font-bold ${form.days_of_week.includes(i) ? "bg-[#002FA7] text-white" : "bg-zinc-100 text-zinc-600"}`}>{day}</button>)}</div>
            <div className="flex gap-4 mt-2 text-xs"><button type="button" className="underline" onClick={() => setForm({ ...form, days_of_week: [0,1,2,3,4,5,6] })}>Every day</button><button type="button" className="underline" onClick={() => setForm({ ...form, days_of_week: [0,1,2,3,4] })}>Weekdays</button><button type="button" className="underline" onClick={() => setForm({ ...form, days_of_week: [5,6] })}>Weekends</button></div>
          </div>
        </section>

        {error && <div className="border border-red-200 bg-red-50 text-red-700 p-3 text-sm" role="alert">{error}</div>}
        {message && <div className="border border-blue-200 bg-blue-50 text-blue-900 p-3 text-sm" role="status" data-testid="route-setup-message">{message}</div>}
        <div className="flex flex-wrap gap-3">
          <button type="submit" disabled={saving} className="te-btn-primary disabled:opacity-50" data-testid="route-setup-submit">{saving ? "Creating route and schedules…" : createdRoute ? "Retry schedule generation" : "Create route + generate all schedules"}</button>
          <button type="button" onClick={reset} disabled={saving} className="px-4 py-2 border border-black/20 text-sm font-bold disabled:opacity-50">Start another route</button>
        </div>
      </form>
    </div>
  );
}
