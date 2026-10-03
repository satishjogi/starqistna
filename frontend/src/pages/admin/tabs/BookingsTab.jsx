import React, { useEffect, useState } from "react";
import api from "../../../lib/api";

function fmtPrice(v, ccy = "myr") {
  const map = { myr: "RM", sgd: "S$", usd: "$" };
  return `${map[ccy] || ccy.toUpperCase()} ${Number(v || 0).toFixed(2)}`;
}

export default function BookingsTab() {
  const [bookings, setBookings] = useState([]);
  const [selectedBookingId, setSelectedBookingId] = useState(null);
  const [selectedBooking, setSelectedBooking] = useState(null);
  const [loadingBooking, setLoadingBooking] = useState(false);

  useEffect(() => {
    api.get("/admin/bookings").then(({ data }) => setBookings(data)).catch(() => {});
  }, []);

  const openBooking = async (bookingId) => {
    setSelectedBookingId(bookingId);
    setLoadingBooking(true);
    try {
      const { data } = await api.get(`/bookings/${bookingId}`);
      setSelectedBooking(data);
    } catch (e) {
      setSelectedBooking(null);
      console.error("Failed to load booking detail", e);
    } finally {
      setLoadingBooking(false);
    }
  };

  const closeBooking = () => {
    setSelectedBookingId(null);
    setSelectedBooking(null);
    setLoadingBooking(false);
  };

  return (
    <div className="mt-6 space-y-2">
      {bookings.map((b) => (
        <div key={b.id} className="te-card p-4 grid grid-cols-6 gap-3 text-sm items-center">
          <button
            onClick={() => openBooking(b.id)}
            className="font-mono font-bold text-blue-600 hover:text-blue-800 hover:underline cursor-pointer text-left"
          >
            {b.reference}
          </button>
          <div>{b.contact_email}</div>
          <div className="font-mono">{b.departure_date} {b.departure_time}</div>
          <div>Seats: {b.seats.join(",")}</div>
          <div className="font-mono">{b.pricing.currency.toUpperCase()} {b.pricing.total.toFixed(2)}</div>
          <div className="text-[10px] uppercase font-mono font-bold">{b.status}</div>
        </div>
      ))}

      {selectedBookingId && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="w-full max-w-3xl max-h-[90vh] overflow-y-auto rounded-xl border border-black/10 bg-white shadow-2xl">
            <div className="sticky top-0 flex items-center justify-between border-b border-black/10 bg-white px-5 py-4">
              <div>
                <div className="te-overline">Booking detail</div>
                <div className="font-mono text-xl font-black">
                  {selectedBooking?.reference || "Loading..."}
                </div>
              </div>
              <button
                type="button"
                onClick={closeBooking}
                className="rounded border border-black/10 px-3 py-1.5 text-xs font-mono font-bold uppercase hover:bg-zinc-50"
              >
                Close
              </button>
            </div>

            {loadingBooking && (
              <div className="p-6 font-mono text-sm text-zinc-500">LOADING BOOKING…</div>
            )}

            {!loadingBooking && selectedBooking && (
              <div className="space-y-6 p-6">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <div className="te-overline text-[10px]">Status</div>
                    <div className="text-xs font-mono font-black uppercase px-2 py-1 bg-amber-100 text-amber-700 inline-block">
                      {selectedBooking.status?.replace(/_/g, " ")}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="te-overline text-[10px]">Contact</div>
                    <div className="font-mono text-sm">{selectedBooking.contact_email}</div>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  <div className="te-card p-4">
                    <div className="te-overline text-[10px]">Route</div>
                    <div className="text-xl font-black">{selectedBooking.from?.city} → {selectedBooking.to?.city}</div>
                    <div className="font-mono text-xs text-zinc-500 mt-1">
                      {selectedBooking.from?.name} → {selectedBooking.to?.name}
                    </div>
                  </div>
                  <div className="te-card p-4">
                    <div className="te-overline text-[10px]">Departure</div>
                    <div className="font-mono font-black">{selectedBooking.departure_date} · {selectedBooking.departure_time}</div>
                    <div className="font-mono text-xs text-zinc-500 mt-1">
                      {selectedBooking.schedule?.bus_operator} · {selectedBooking.schedule?.bus_type}
                    </div>
                  </div>
                </div>

                <div className="te-card p-4">
                  <div className="te-overline text-[10px] mb-3">Passengers</div>
                  <div className="space-y-2">
                    {(selectedBooking.passengers || []).map((p, i) => (
                      <div key={`${p.name}-${i}`} className="flex justify-between gap-3 border-b border-black/5 pb-2">
                        <div>
                          <div className="font-bold">{p.name}</div>
                          <div className="text-[10px] font-mono uppercase text-zinc-500">
                            {p.category} {p.ic_or_passport ? `· ${p.ic_or_passport}` : ""}
                          </div>
                        </div>
                        <div className="font-mono font-black">SEAT {p.seat_number}</div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="te-card p-4">
                  <div className="te-overline text-[10px] mb-3">Payment</div>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span>Subtotal</span>
                      <span className="font-mono">
                        {fmtPrice((selectedBooking.pricing?.adults || 0) * (selectedBooking.pricing?.adult_fare || 0) + (selectedBooking.pricing?.children || 0) * (selectedBooking.pricing?.child_fare || 0), selectedBooking.pricing?.currency)}
                      </span>
                    </div>
                    {(selectedBooking.pricing?.discount || 0) > 0 && (
                      <div className="flex justify-between text-emerald-700">
                        <span>Promo</span>
                        <span className="font-mono">−{fmtPrice(selectedBooking.pricing.discount, selectedBooking.pricing.currency)}</span>
                      </div>
                    )}
                    <div className="flex justify-between font-black text-base pt-2 border-t border-black/10">
                      <span>Total</span>
                      <span className="font-mono">{fmtPrice(selectedBooking.pricing?.total, selectedBooking.pricing?.currency)}</span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
