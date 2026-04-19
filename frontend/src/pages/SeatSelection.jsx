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
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        {/* Seat map */}
        <div className="lg:col-span-8">
          <div className="te-card p-6 md:p-10">
            <div className="flex justify-center mb-6">
              <div className="w-20 h-10 border-2 border-black/30 rounded-t-full flex items-center justify-center text-[10px] font-mono tracking-wider text-zinc-500">DRIVER</div>
            </div>
            <div className="flex flex-col items-center gap-2">
              {data.layout.map((row, rIdx) => (
                <div key={rIdx} className="flex items-center gap-2">
                  <div className="w-5 text-[10px] font-mono text-zinc-400 text-right">{rIdx + 1}</div>
                  {row.map((cell, i) => {
                    if (cell.aisle) return <div key={i} className="seat-aisle" />;
                    const isSelected = selected.find((s) => s.seat_number === cell.seat_number);
                    const cls = isSelected
                      ? "seat-selected"
                      : cell.status === "available"
                      ? "seat-available"
                      : "seat-booked";
                    return (
                      <button
                        key={i}
                        type="button"
                        className={`seat ${cls}`}
                        onClick={() => toggleSeat(cell.seat_number, cell.status)}
                        data-testid={`seat-btn-${cell.seat_number}`}
                      >
                        {cell.seat_number}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>

            <div className="mt-8 flex flex-wrap gap-6 justify-center text-xs font-mono">
              <div className="flex items-center gap-2"><div className="seat seat-available !w-5 !h-5" /> Available</div>
              <div className="flex items-center gap-2"><div className="seat seat-selected !w-5 !h-5" /> Selected</div>
              <div className="flex items-center gap-2"><div className="seat seat-booked !w-5 !h-5" /> Booked</div>
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
