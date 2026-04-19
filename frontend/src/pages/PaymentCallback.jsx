import React, { useEffect, useState, useRef } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import { QRCodeSVG } from "qrcode.react";
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
    <div className="px-4 md:px-6 lg:px-10 py-16 max-w-3xl mx-auto">
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

          <div className="mt-8 bg-[#F4F4F5] border-l-4 border-[#002FA7] p-6 flex flex-col sm:flex-row sm:items-center gap-6">
            <div className="flex-1">
              <div className="te-overline">Booking reference</div>
              <div className="font-mono text-3xl font-black mt-1" data-testid="booking-reference">{booking.reference}</div>
              <div className="text-xs text-zinc-600 mt-2">Scan the QR at the boarding gate.</div>
            </div>
            <div className="bg-white p-3 border border-black/10" data-testid="booking-qr">
              <QRCodeSVG value={booking.reference} size={128} level="M" />
            </div>
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

          {/* Return-trip upsell */}
          <ReturnUpsell booking={booking} />

          <div className="mt-8 flex gap-3 flex-wrap">
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


function ReturnUpsell({ booking }) {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [avail, setAvail] = useState(null);
  const today = new Date().toISOString().slice(0, 10);
  const minReturn = booking.departure_date > today ? booking.departure_date : today;
  const [returnDate, setReturnDate] = useState(minReturn);

  const findReturn = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/search", {
        params: {
          from_terminal_id: booking.to_terminal_id,
          to_terminal_id: booking.from_terminal_id,
          date: returnDate,
        },
      });
      setAvail(data);
    } finally {
      setLoading(false);
    }
  };

  const bookReturn = () => {
    // Reuse the same passenger split as the outbound booking
    const adults = booking.pricing.adults;
    const children = booking.pricing.children;
    const p = new URLSearchParams({
      from: booking.to_terminal_id,
      to: booking.from_terminal_id,
      date: returnDate,
      adults: String(adults),
      children: String(children),
    });
    navigate(`/search?${p.toString()}`);
  };

  return (
    <div className="mt-10 border-2 border-dashed border-[#002FA7]/30 p-6" data-testid="return-upsell">
      <div className="te-overline text-[#002FA7]">Heading back?</div>
      <div className="flex flex-wrap items-center gap-3 mt-3">
        <h2 className="text-2xl font-black tracking-tight flex-1">
          Book your return trip — {booking.to?.city || "destination"} → {booking.from?.city || "origin"}
        </h2>
      </div>
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <div>
          <label className="te-label">Return date</label>
          <input
            type="date"
            className="te-input"
            min={minReturn}
            value={returnDate}
            onChange={(e) => setReturnDate(e.target.value)}
            data-testid="return-date"
          />
        </div>
        <button onClick={findReturn} disabled={loading} className="te-btn-outline" data-testid="find-return-btn">
          {loading ? "Searching…" : "Find buses"}
        </button>
        {avail && (
          <div className="text-xs font-mono text-zinc-600">
            {avail.schedules.length} departures available
          </div>
        )}
        <button onClick={bookReturn} className="te-btn-accent ml-auto" data-testid="book-return-btn">
          Book return →
        </button>
      </div>
    </div>
  );
}
