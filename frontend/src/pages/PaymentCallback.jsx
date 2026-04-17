import React, { useEffect, useState, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { clearFlow } from "../lib/booking-store";

const MAX_POLL = 8;

export default function PaymentCallback() {
  const [params] = useSearchParams();
  const sessionId = params.get("session_id");
  const bookingId = params.get("booking_id");

  const [status, setStatus] = useState("pending"); // pending | paid | failed | timeout
  const [booking, setBooking] = useState(null);
  const attemptsRef = useRef(0);

  useEffect(() => {
    if (!sessionId) {
      setStatus("failed");
      return;
    }
    let alive = true;
    const tick = async () => {
      if (!alive) return;
      if (attemptsRef.current >= MAX_POLL) {
        setStatus("timeout");
        return;
      }
      attemptsRef.current += 1;
      try {
        const { data } = await api.get(`/payments/status/${sessionId}`);
        if (data.payment_status === "paid") {
          setStatus("paid");
          setBooking(data.booking);
          clearFlow();
          return;
        }
        if (data.status === "expired") {
          setStatus("failed");
          return;
        }
      } catch {
        // keep polling
      }
      setTimeout(tick, 2000);
    };
    tick();
    return () => { alive = false; };
  }, [sessionId]);

  return (
    <div className="px-6 md:px-12 lg:px-20 py-16 max-w-3xl mx-auto">
      {status === "pending" && (
        <div className="te-card p-10 text-center" data-testid="payment-pending">
          <div className="te-overline mb-2">Processing</div>
          <h1 className="text-4xl font-black tracking-tight">Confirming your payment…</h1>
          <p className="text-zinc-600 mt-3">Hang tight. This usually takes a few seconds.</p>
          <div className="mt-6 mx-auto w-10 h-10 border-4 border-black/10 border-t-[#002FA7] rounded-full animate-spin" />
        </div>
      )}
      {status === "paid" && booking && (
        <div className="te-card p-10" data-testid="payment-success">
          <div className="te-overline mb-2 text-emerald-600">Confirmed</div>
          <h1 className="text-4xl md:text-5xl font-black tracking-tight">Seat secured.</h1>
          <p className="text-zinc-600 mt-3">Your ticket has been issued. A copy was sent to {booking.contact_email}.</p>

          <div className="mt-8 bg-[#F4F4F5] border-l-4 border-[#002FA7] p-6">
            <div className="te-overline">Booking reference</div>
            <div className="font-mono text-3xl font-black mt-1" data-testid="booking-reference">{booking.reference}</div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-6 text-sm">
            <div>
              <div className="te-overline text-[10px]">Date</div>
              <div className="font-mono font-bold">{booking.departure_date}</div>
            </div>
            <div>
              <div className="te-overline text-[10px]">Departure</div>
              <div className="font-mono font-bold">{booking.departure_time}</div>
            </div>
            <div>
              <div className="te-overline text-[10px]">Seats</div>
              <div className="font-mono font-bold">{booking.seats.join(", ")}</div>
            </div>
            <div>
              <div className="te-overline text-[10px]">Total paid</div>
              <div className="font-mono font-bold">
                {booking.pricing.currency.toUpperCase()} {booking.pricing.total.toFixed(2)}
              </div>
            </div>
          </div>

          <div className="mt-8 flex gap-3">
            <Link to={`/bookings/${booking.id}`} className="te-btn-primary" data-testid="view-booking-btn">View booking</Link>
            <Link to="/" className="te-btn-outline">Back home</Link>
          </div>
        </div>
      )}
      {(status === "failed" || status === "timeout") && (
        <div className="te-card p-10" data-testid="payment-failed">
          <div className="te-overline mb-2 text-red-600">Payment {status === "timeout" ? "pending" : "failed"}</div>
          <h1 className="text-4xl font-black tracking-tight">{status === "timeout" ? "Still processing…" : "We couldn't confirm your payment."}</h1>
          <p className="text-zinc-600 mt-3">
            {status === "timeout"
              ? "Your bank may take a few minutes. Check your booking later; if the charge doesn't settle, it auto-voids."
              : "You were not charged. Try booking again."}
          </p>
          <div className="mt-6 flex gap-3">
            {bookingId && <Link to={`/bookings/${bookingId}`} className="te-btn-outline">Check booking status</Link>}
            <Link to="/" className="te-btn-primary">Start over</Link>
          </div>
        </div>
      )}
    </div>
  );
}
