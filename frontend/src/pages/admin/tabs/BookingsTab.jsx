import React, { useEffect, useMemo, useState } from "react";
import api from "../../../lib/api";

function formatMoney(amount, currency = "myr") {
  const code = String(currency || "myr").toUpperCase();
  const symbol = { MYR: "RM", SGD: "S$", USD: "$" }[code] || code;
  const value = Number(amount);
  return Number.isFinite(value) ? symbol + " " + value.toFixed(2) : "—";
}

function StatusPill({ value }) {
  const status = String(value || "unknown").toLowerCase();
  const tone = ["confirmed", "paid", "complete", "boarded"].includes(status)
    ? "bg-emerald-50 text-emerald-700 border-emerald-200"
    : ["pending_payment", "pending", "initiated", "locked"].includes(status)
    ? "bg-amber-50 text-amber-800 border-amber-200"
    : ["cancelled", "failed", "seat_conflict", "refunded", "expired"].some((part) => status.includes(part))
    ? "bg-red-50 text-red-700 border-red-200"
    : "bg-zinc-50 text-zinc-600 border-zinc-200";
  return (
    <span className={"inline-flex border px-2 py-1 text-[10px] font-mono font-bold uppercase " + tone}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

function BookingDetail({ booking, onClose }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    api.get("/admin/bookings/" + booking.id)
      .then(({ data }) => { if (active) setDetail(data); })
      .catch((e) => {
        if (active) setError(e?.response?.data?.detail || "Unable to load complete booking details.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [booking.id]);

  const current = detail || booking;
  const from = current.from?.name || current.from_terminal_id || "—";
  const to = current.to?.name || current.to_terminal_id || "—";
  const pricing = current.pricing || {};

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 p-3 sm:p-6 flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="booking-detail-title"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <div className="bg-white w-full max-w-4xl max-h-[92vh] overflow-auto shadow-2xl">
        <div className="sticky top-0 z-10 bg-white border-b border-black/10 px-5 py-4 flex items-start justify-between gap-4">
          <div>
            <div className="te-overline">Customer operations</div>
            <h2 id="booking-detail-title" className="text-2xl font-black tracking-tight">
              {current.reference || "Booking details"}
            </h2>
            <div className="text-xs text-zinc-500 mt-1">
              Created {current.created_at ? new Date(current.created_at).toLocaleString() : "—"}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="border border-black/15 px-3 py-2 text-xs font-bold uppercase hover:bg-zinc-50"
            aria-label="Close booking details"
          >
            Close ✕
          </button>
        </div>

        {loading && <div className="p-5 text-sm text-zinc-500">Loading booking and payment details…</div>}
        {error && <div className="m-5 border border-red-200 bg-red-50 p-3 text-sm text-red-700" role="alert">{error}</div>}

        <div className="p-5 space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="border border-black/10 p-4">
              <div className="te-overline mb-2">Booking status</div>
              <StatusPill value={current.status} />
              <div className="text-[10px] uppercase text-zinc-400 mt-3 mb-1">Payment status</div>
              <StatusPill value={current.payment_status} />
            </div>
            <div className="border border-black/10 p-4">
              <div className="te-overline mb-2">Customer</div>
              <div className="font-bold break-all">{current.contact_email || "—"}</div>
              <div className="text-sm text-zinc-600 mt-1">{current.contact_phone || "No phone recorded"}</div>
            </div>
            <div className="border border-black/10 p-4">
              <div className="te-overline mb-2">Booking total</div>
              <div className="text-2xl font-black tabular-nums">{formatMoney(pricing.total, pricing.currency)}</div>
              <div className="text-xs text-zinc-500 mt-1">
                {pricing.adults ?? "—"} adult(s) · {pricing.children ?? "—"} child(ren)
              </div>
              {pricing.promo?.code && <div className="text-xs text-emerald-700 mt-2">Promo: {pricing.promo.code}</div>}
            </div>
          </div>

          <section>
            <div className="te-overline mb-2">Journey</div>
            <div className="border border-black/10 p-4 grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] gap-3 items-center">
              <div>
                <div className="text-[10px] uppercase text-zinc-400">From</div>
                <div className="font-bold">{from}</div>
                {current.from?.city && <div className="text-xs text-zinc-500">{current.from.city}</div>}
              </div>
              <div className="text-xl text-[#B5121B]">→</div>
              <div>
                <div className="text-[10px] uppercase text-zinc-400">To</div>
                <div className="font-bold">{to}</div>
                {current.to?.city && <div className="text-xs text-zinc-500">{current.to.city}</div>}
              </div>
            </div>
            <div className="text-xs font-mono text-zinc-500 mt-2">
              Departure: {current.departure_date || current.schedule?.departure_date || "—"} · {current.departure_time || current.schedule?.departure_time || "—"}
              {current.schedule?.bus_operator ? " · " + current.schedule.bus_operator : ""}
            </div>
          </section>

          <section>
            <div className="te-overline mb-2">Passengers and seats</div>
            <div className="overflow-x-auto border border-black/10">
              <table className="w-full text-sm">
                <thead className="bg-zinc-50 text-[10px] uppercase text-zinc-500">
                  <tr>
                    <th className="text-left p-3">Passenger</th>
                    <th className="text-left p-3">Category</th>
                    <th className="text-left p-3">Seat</th>
                    <th className="text-left p-3">Travel document</th>
                  </tr>
                </thead>
                <tbody>
                  {(current.passengers || []).map((passenger, index) => (
                    <tr key={(passenger.seat_number || index) + "-" + index} className="border-t border-black/5">
                      <td className="p-3 font-bold">{passenger.name || "—"}</td>
                      <td className="p-3 capitalize">{passenger.category || "—"}</td>
                      <td className="p-3 font-mono">{passenger.seat_number || (current.seats || [])[index] || "—"}</td>
                      <td className="p-3 font-mono text-xs">{passenger.ic_or_passport || "—"}</td>
                    </tr>
                  ))}
                  {!(current.passengers || []).length && (
                    <tr><td colSpan="4" className="p-4 text-zinc-500">No passenger details recorded.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section>
            <div className="te-overline mb-2">Fare breakdown</div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
              {[
                ["Adults", pricing.adults],
                ["Children", pricing.children],
                ["Subtotal", pricing.subtotal],
                ["Discount", pricing.discount],
              ].map(([label, value]) => (
                <div key={label} className="border border-black/10 p-3">
                  <div className="text-[10px] uppercase text-zinc-400">{label}</div>
                  <div className="font-mono font-bold mt-1">
                    {value == null ? "—" : ["Subtotal", "Discount"].includes(label)
                      ? formatMoney(value, pricing.currency)
                      : value}
                  </div>
                </div>
              ))}
            </div>
            {pricing.promo?.code && <div className="text-xs text-zinc-500 mt-2">Promo code: {pricing.promo.code}</div>}
          </section>

          <section>
            <div className="te-overline mb-2">Payment history</div>
            <div className="space-y-2">
              {(current.payment_transactions || []).map((txn, index) => (
                <div key={txn.session_id || index} className="border border-black/10 p-3 grid grid-cols-1 md:grid-cols-5 gap-2 text-xs">
                  <div>
                    <div className="text-[10px] uppercase text-zinc-400">Created</div>
                    <div>{txn.created_at ? new Date(txn.created_at).toLocaleString() : "—"}</div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase text-zinc-400">Payment</div>
                    <StatusPill value={txn.payment_status || txn.status} />
                  </div>
                  <div>
                    <div className="text-[10px] uppercase text-zinc-400">Amount</div>
                    <div className="font-mono font-bold">{formatMoney(txn.amount, txn.currency)}</div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase text-zinc-400">Finalization</div>
                    <div>{txn.finalization_status || "—"}</div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase text-zinc-400">Review flags</div>
                    <div>{txn.needs_ops_action ? "Needs operations" : txn.needs_refund_review ? "Refund review" : txn.duplicate_payment ? "Duplicate payment" : "—"}</div>
                  </div>
                </div>
              ))}
              {!(current.payment_transactions || []).length && (
                <div className="border border-dashed border-black/15 p-4 text-sm text-zinc-500">No payment attempts are recorded for this booking.</div>
              )}
            </div>
          </section>

          {(current.gohub_status || current.gohub_tickets?.length || current.needs_ops_action || current.seat_conflict_seats?.length) && (
            <section>
              <div className="te-overline mb-2">Ticketing and operations</div>
              <div className="border border-black/10 p-4 space-y-2 text-sm">
                <div>CTS / ticket status: <b>{current.gohub_status || "not recorded"}</b></div>
                {current.gohub_tickets?.length > 0 && <div>Issued tickets: {current.gohub_tickets.length}</div>}
                {current.needs_ops_action && <div className="text-red-700 font-bold">Needs operations review</div>}
                {current.seat_conflict_seats?.length > 0 && <div className="text-red-700">Seat conflict: {current.seat_conflict_seats.join(", ")}</div>}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

export default function BookingsTab() {
  const [bookings, setBookings] = useState([]);
  const [terminals, setTerminals] = useState([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [paymentStatus, setPaymentStatus] = useState("all");
  const [departureFrom, setDepartureFrom] = useState("");
  const [departureTo, setDepartureTo] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [bookingResponse, terminalResponse] = await Promise.all([
        api.get("/admin/bookings"),
        api.get("/admin/terminals"),
      ]);
      setBookings(Array.isArray(bookingResponse.data) ? bookingResponse.data : []);
      setTerminals(Array.isArray(terminalResponse.data) ? terminalResponse.data : []);
    } catch (e) {
      setError(e?.response?.data?.detail || "Could not load bookings.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const terminalById = useMemo(
    () => Object.fromEntries(terminals.map((terminal) => [terminal.id, terminal])),
    [terminals],
  );
  const visibleBookings = useMemo(() => {
    const search = query.trim().toLowerCase();
    return bookings.filter((booking) => {
      const from = terminalById[booking.from_terminal_id]?.name || "";
      const to = terminalById[booking.to_terminal_id]?.name || "";
      const passengers = (booking.passengers || []).map((p) => p.name || "").join(" ");
      const haystack = [
        booking.reference, booking.contact_email, booking.contact_phone, passengers, from, to,
        ...(booking.seats || []), booking.schedule_id,
      ].join(" ").toLowerCase();
      if (search && !haystack.includes(search)) return false;
      if (status !== "all" && String(booking.status || "").toLowerCase() !== status) return false;
      if (paymentStatus !== "all" && String(booking.payment_status || "").toLowerCase() !== paymentStatus) return false;
      if (departureFrom && (!booking.departure_date || booking.departure_date < departureFrom)) return false;
      if (departureTo && (!booking.departure_date || booking.departure_date > departureTo)) return false;
      return true;
    });
  }, [bookings, departureFrom, departureTo, paymentStatus, query, status, terminalById]);

  return (
    <div className="mt-6 space-y-5" data-testid="bookings-tab">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="te-overline">Customer operations</div>
          <h2 className="text-2xl font-black tracking-tight">Bookings</h2>
          <p className="text-sm text-zinc-500 mt-1">Search customers and references, inspect passengers, journey, payment attempts and ticketing status.</p>
        </div>
        <button type="button" onClick={load} disabled={loading} className="te-btn-primary disabled:opacity-50" data-testid="booking-refresh">
          {loading ? "Refreshing…" : "Refresh bookings"}
        </button>
      </div>

      <div className="te-card p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-3">
        <div className="lg:col-span-2">
          <label className="te-label" htmlFor="booking-search">Search</label>
          <input id="booking-search" className="te-input" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Reference, email, phone, passenger…" data-testid="booking-search" />
        </div>
        <div>
          <label className="te-label" htmlFor="booking-status">Booking status</label>
          <select id="booking-status" className="te-input" value={status} onChange={(e) => setStatus(e.target.value)} data-testid="booking-status-filter">
            <option value="all">All statuses</option>
            {["pending_payment", "confirmed", "cancelled_refunded", "cancelled_burned", "seat_conflict"].map((value) => (
              <option key={value} value={value}>{value.replace(/_/g, " ")}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="te-label" htmlFor="booking-payment-status">Payment</label>
          <select id="booking-payment-status" className="te-input" value={paymentStatus} onChange={(e) => setPaymentStatus(e.target.value)} data-testid="booking-payment-filter">
            <option value="all">All payment states</option>
            {["pending", "paid", "failed", "refunded", "initiated", "expired", "canceled"].map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="te-label" htmlFor="booking-date-from">Departure from</label>
          <input id="booking-date-from" type="date" className="te-input" value={departureFrom} onChange={(e) => setDepartureFrom(e.target.value)} />
        </div>
        <div>
          <label className="te-label" htmlFor="booking-date-to">Departure to</label>
          <input id="booking-date-to" type="date" className="te-input" value={departureTo} onChange={(e) => setDepartureTo(e.target.value)} />
        </div>
      </div>

      {error && <div className="border border-red-200 bg-red-50 text-red-700 p-4 text-sm" role="alert">{error}</div>}
      <div className="flex justify-between text-xs text-zinc-500 font-mono">
        <span>{visibleBookings.length} shown · {bookings.length} loaded (latest 500)</span>
        <button type="button" onClick={() => {
          setQuery("");
          setStatus("all");
          setPaymentStatus("all");
          setDepartureFrom("");
          setDepartureTo("");
        }} className="underline">Clear filters</button>
      </div>

      <div className="space-y-2">
        {loading && !bookings.length && <div className="te-card p-6 text-sm text-zinc-500">Loading bookings…</div>}
        {!loading && !error && visibleBookings.length === 0 && <div className="te-card p-8 text-center text-sm text-zinc-500">No bookings match these filters.</div>}
        {visibleBookings.map((booking) => (
          <button
            type="button"
            key={booking.id}
            onClick={() => setSelected(booking)}
            className="te-card w-full text-left p-4 grid grid-cols-1 md:grid-cols-12 gap-3 items-center hover:bg-zinc-50 focus:outline-none focus:ring-2 focus:ring-[#002FA7]"
            data-testid={"booking-row-" + (booking.reference || booking.id)}
          >
            <div className="md:col-span-2">
              <div className="font-mono font-bold">{booking.reference || "—"}</div>
              <div className="text-[10px] text-zinc-400 mt-1">{booking.created_at ? new Date(booking.created_at).toLocaleDateString() : ""}</div>
            </div>
            <div className="md:col-span-3 min-w-0">
              <div className="font-bold truncate">{booking.contact_email || "—"}</div>
              <div className="text-xs text-zinc-500 truncate">{(booking.passengers || []).map((p) => p.name).filter(Boolean).join(", ") || "Passenger details unavailable"}</div>
              <div className="text-xs text-zinc-400">{booking.contact_phone || ""}</div>
            </div>
            <div className="md:col-span-3">
              <div className="text-sm font-bold">{terminalById[booking.from_terminal_id]?.city || terminalById[booking.from_terminal_id]?.name || "—"} → {terminalById[booking.to_terminal_id]?.city || terminalById[booking.to_terminal_id]?.name || "—"}</div>
              <div className="text-xs font-mono text-zinc-500 mt-1">{booking.departure_date || "—"} · {booking.departure_time || "—"}</div>
            </div>
            <div className="md:col-span-1 text-xs font-mono">Seats<br />{(booking.seats || []).join(", ") || "—"}</div>
            <div className="md:col-span-2">
              <div className="font-mono font-bold">{formatMoney(booking.pricing?.total, booking.pricing?.currency)}</div>
              <div className="mt-1"><StatusPill value={booking.status} /></div>
              <div className="mt-1"><StatusPill value={booking.payment_status} /></div>
            </div>
            <div className="md:col-span-1 text-right text-xs font-bold uppercase text-[#002FA7]">Details ↗</div>
          </button>
        ))}
      </div>
      {selected && <BookingDetail booking={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
