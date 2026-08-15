import React, { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import api from "../lib/api";
import { getFlow, setFlow } from "../lib/booking-store";

function fmtPrice(v, ccy = "myr") {
  const map = { myr: "RM", sgd: "S$", usd: "$" };
  return `${map[ccy] || ccy.toUpperCase()} ${Number(v).toFixed(2)}`;
}

export default function SeatSelection() {
  const { scheduleId } = useParams();
  const navigate = useNavigate();
  const flow = getFlow();

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState([]); // [{seat_number, category}]
  const [error, setError] = useState("");
  const [locking, setLocking] = useState(false);

  useEffect(() => {
    api
      .get(`/schedules/${scheduleId}`)
      .then(({ data }) => setData(data))
      .catch((e) => setError(e?.response?.data?.detail || "Failed to load"))
      .finally(() => setLoading(false));
    const t = setInterval(() => {
      api.get(`/schedules/${scheduleId}`).then(({ data }) => setData(data)).catch(() => {});
    }, 15000);
    return () => clearInterval(t);
  }, [scheduleId]);

  const wanted = (flow?.adults || 1) + (flow?.children || 0);

  const toggleSeat = (sn, currentStatus) => {
    if (currentStatus === "booked" || currentStatus === "locked") return;
    setError("");
    const existing = selected.find((s) => s.seat_number === sn);
    if (existing) {
      setSelected(selected.filter((s) => s.seat_number !== sn));
      return;
    }
    if (selected.length >= wanted) {
      setError(`You can only pick ${wanted} seat(s) for this booking.`);
      return;
    }
    // Default category: first N = adult, rest = child
    const adultsSoFar = selected.filter((s) => s.category === "adult").length;
    const category = adultsSoFar < (flow?.adults || 1) ? "adult" : "child";
    setSelected([...selected, { seat_number: sn, category }]);
  };

  const setCategory = (sn, cat) => {
    setSelected(selected.map((s) => (s.seat_number === sn ? { ...s, category: cat } : s)));
  };

  const pricing = useMemo(() => {
    if (!data) return null;
    const adultFare = data.schedule.adult_fare;
    const childFare = +(adultFare * 0.5).toFixed(2);
    const adults = selected.filter((s) => s.category === "adult").length;
    const children = selected.filter((s) => s.category === "child").length;
    const total = +(adults * adultFare + children * childFare).toFixed(2);
    return { adultFare, childFare, adults, children, total, currency: data.schedule.currency };
  }, [data, selected]);

  const proceed = async () => {
    setError("");
    if (selected.length !== wanted) {
      return setError(`Select ${wanted} seat(s) before continuing.`);
    }
    // validate category split matches requested adult/child count
    const a = selected.filter((s) => s.category === "adult").length;
    const c = selected.filter((s) => s.category === "child").length;
    if (a !== (flow?.adults || 1) || c !== (flow?.children || 0)) {
      return setError(`Adjust categories: expected ${flow.adults} Adult and ${flow.children} Child.`);
    }

    setLocking(true);
    try {
      const { data: lock } = await api.post("/seats/lock", {
        schedule_id: scheduleId,
        seat_numbers: selected.map((s) => s.seat_number),
      });
      setFlow({
        ...flow,
        selected_seats: selected,
        lock_token: lock.lock_token,
        lock_expires_at: lock.expires_at,
      });
      navigate("/passengers");
    } catch (e) {
      const detail = e?.response?.data?.detail;
      if (detail?.failed_seats) {
        setError(`Seats taken: ${detail.failed_seats.join(", ")}. Please re-select.`);
        // refresh layout
        const { data: fresh } = await api.get(`/schedules/${scheduleId}`);
        setData(fresh);
        setSelected(selected.filter((s) => !detail.failed_seats.includes(s.seat_number)));
      } else {
        setError(typeof detail === "string" ? detail : "Could not lock seats. Try again.");
      }
    } finally {
      setLocking(false);
    }
  };

  if (loading) return <div className="p-10 font-mono text-zinc-500">LOADING…</div>;
  if (!data) return <div className="p-10 text-red-600">{error}</div>;

  return (
    <div className="px-4 md:px-6 lg:px-10 py-10">
      <div className="te-overline mb-2">Step 02 · Select your seats</div>
      <div className="flex flex-wrap items-end gap-4 mb-6">
        <div>
          <h1 className="text-3xl md:text-4xl font-black tracking-tight">
            {data.from.city} <span className="text-[#B5121B]">→</span> {data.to.city}
          </h1>
          <div className="font-mono text-xs text-zinc-500 mt-1">
            {data.schedule.departure_date} · {data.schedule.departure_time} → {data.schedule.arrival_time} · {data.schedule.bus_operator}
          </div>
          {/* Specific pickup + drop-off stops — critical when the city has multiple stations */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mt-3 max-w-2xl" data-testid="seat-selection-stops">
            <div className="flex items-start gap-2 text-xs">
              <span className="text-[10px] font-mono uppercase tracking-wider text-emerald-700 border border-emerald-700 px-1.5 py-0.5 mt-0.5 flex-shrink-0">Pickup</span>
              <div className="min-w-0">
                <div className="font-bold truncate">{data.from.name}</div>
                {data.from.code && <span className="font-mono text-[10px] text-zinc-500">{data.from.code}</span>}
              </div>
            </div>
            <div className="flex items-start gap-2 text-xs">
              <span className="text-[10px] font-mono uppercase tracking-wider text-[#B5121B] border border-[#B5121B] px-1.5 py-0.5 mt-0.5 flex-shrink-0">Drop-off</span>
              <div className="min-w-0">
                <div className="font-bold truncate">{data.to.name}</div>
                {data.to.code && <span className="font-mono text-[10px] text-zinc-500">{data.to.code}</span>}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        {/* Seat map */}
        <div className="lg:col-span-8">
          <div className="te-card p-6 md:p-10">
            <div className="flex items-center justify-between mb-4 text-[10px] font-mono uppercase tracking-wider text-zinc-500">
              <span>Front of bus</span>
              <span>{data.schedule.bus_type} · {data.layout_config === "2+1" ? "2+1 layout" : "2+2 layout"}</span>
            </div>
            <div className="bus-shell" data-testid="bus-shell">
              {/* Wheel arches at front & rear axles */}
              <span className="bus-wheel wheel-fl" aria-hidden="true" />
              <span className="bus-wheel wheel-fr" aria-hidden="true" />
              <span className="bus-wheel wheel-rl" aria-hidden="true" />
              <span className="bus-wheel wheel-rr" aria-hidden="true" />

              {/* Cabin: right-hand drive — entry on LEFT, driver seat + steering on RIGHT */}
              <div className="bus-cabin">
                <div className="bus-door" aria-label="Passenger entry" />
                <div className="bus-driver-seat" aria-label="Driver">
                  <svg className="bus-steering" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="9" />
                    <circle cx="12" cy="12" r="2" fill="currentColor" />
                    <path d="M12 3 L12 10" />
                    <path d="M3 12 L10 12" />
                    <path d="M14 12 L21 12" />
                    <path d="M5.6 18.4 L10 14" />
                    <path d="M14 14 L18.4 18.4" />
                  </svg>
                </div>
                <div className="bus-cabin-label">Front · Driver</div>
              </div>

              {/* Seat rows */}
              <div className="bus-rows">
                {data.layout.map((row, rIdx) => (
                  <div key={rIdx} className="bus-row">
                    <div className="bus-row-number">{rIdx + 1}</div>
                    {row.map((cell, i) => {
                      if (cell.aisle) return <div key={i} className="seat-aisle" />;
                      if (cell.empty) return <div key={i} className="seat-empty" />;
                      const selectedSeat = selected.find((s) => s.seat_number === cell.seat_number);
                      const isSelected = !!selectedSeat;
                      const isChild = selectedSeat?.category === "child";
                      const cls = isSelected
                        ? `seat-selected ${isChild ? "seat-child" : ""}`
                        : cell.status === "available"
                        ? "seat-available"
                        : "seat-booked";
                      return (
                        <button
                          key={i}
                          type="button"
                          className={`seat ${cls}`}
                          onClick={() => toggleSeat(cell.seat_number, cell.status)}
                          title={
                            cell.status === "booked" || cell.status === "locked"
                              ? `Seat ${cell.seat_number} — unavailable`
                              : isSelected
                              ? `Seat ${cell.seat_number} — ${selectedSeat.category} (click to deselect)`
                              : `Seat ${cell.seat_number} — available`
                          }
                          data-testid={`seat-btn-${cell.seat_number}`}
                        >
                          {cell.seat_number}
                        </button>
                      );
                    })}
                    <div className="bus-row-number">{rIdx + 1}</div>
                  </div>
                ))}
              </div>
              <div className="bus-rear-label">Rear</div>
            </div>

            <div className="mt-8 flex flex-wrap gap-5 justify-center text-xs font-mono">
              <div className="flex items-center gap-2"><div className="seat seat-available !w-5 !h-6" /> Available</div>
              <div className="flex items-center gap-2"><div className="seat seat-selected !w-5 !h-6" /> Adult</div>
              <div className="flex items-center gap-2"><div className="seat seat-selected seat-child !w-5 !h-6" /> Child</div>
              <div className="flex items-center gap-2"><div className="seat seat-booked !w-5 !h-6" /> Booked</div>
            </div>
          </div>
        </div>

        {/* Sidebar summary */}
        <div className="lg:col-span-4">
          <div className="te-card p-6 sticky top-20">
            <div className="te-overline mb-3">Your selection</div>
            {selected.length === 0 ? (
              <div className="text-sm text-zinc-500">Tap seats on the map to begin.</div>
            ) : (
              <div className="space-y-3">
                {selected.map((s) => (
                  <div key={s.seat_number} className="flex items-center justify-between border-b border-dashed border-black/15 pb-3">
                    <div>
                      <div className="font-mono font-black">{s.seat_number}</div>
                      <div className="text-[10px] font-mono text-zinc-500 uppercase">{s.category}</div>
                    </div>
                    <div className="flex border border-black/15">
                      {["adult", "child"].map((c) => (
                        <button
                          key={c}
                          onClick={() => setCategory(s.seat_number, c)}
                          className={`text-[10px] px-2 py-1 font-bold uppercase ${s.category === c ? "bg-black text-white" : ""}`}
                          data-testid={`cat-${s.seat_number}-${c}`}
                        >
                          {c}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {pricing && (
              <div className="mt-6 space-y-2 text-sm">
                <div className="flex justify-between"><span>Adult × {pricing.adults}</span><span className="font-mono">{fmtPrice(pricing.adults * pricing.adultFare, pricing.currency)}</span></div>
                <div className="flex justify-between"><span>Child × {pricing.children}</span><span className="font-mono">{fmtPrice(pricing.children * pricing.childFare, pricing.currency)}</span></div>
                <div className="te-divider-dashed my-3" />
                <div className="flex justify-between font-black text-lg"><span>TOTAL</span><span className="font-mono" data-testid="seat-total">{fmtPrice(pricing.total, pricing.currency)}</span></div>
              </div>
            )}

            {error && <div className="mt-4 text-xs font-bold text-red-600" data-testid="seat-error">{error}</div>}

            <button
              onClick={proceed}
              disabled={locking || selected.length !== wanted}
              className="te-btn-accent w-full mt-5 disabled:opacity-40"
              data-testid="seats-continue-btn"
            >
              {locking ? "Locking seats…" : `Continue with ${selected.length}/${wanted}`}
            </button>
            <div className="text-[10px] font-mono text-zinc-500 mt-2 text-center">SEATS LOCK FOR 10 MINUTES</div>
          </div>
        </div>
      </div>
    </div>
  );
}
