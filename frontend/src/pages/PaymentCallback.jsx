import React, { useEffect, useState, useRef } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import { QRCodeSVG } from "qrcode.react";
import api from "../lib/api";
import { clearFlow } from "../lib/booking-store";

const MAX_POLL = 20;   // 20 * 2s = 40 seconds — accommodates GrabPay / FPX settle time
const POLL_INTERVAL_MS = 2000;

export default function PaymentCallback() {
  const [params] = useSearchParams();
  const sessionId = params.get("session_id");
  const bookingId = params.get("booking_id");

  const [status, setStatus] = useState("pending"); // pending | paid | failed | timeout
  const [booking, setBooking] = useState(null);
  const [verifying, setVerifying] = useState(false);
  const attemptsRef = useRef(0);
  const aliveRef = useRef(true);

  const runPoll = async () => {
    if (!aliveRef.current) return;
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
    setTimeout(runPoll, POLL_INTERVAL_MS);
  };

  useEffect(() => {
    if (!sessionId) {
      setStatus("failed");
      return;
    }
    aliveRef.current = true;
    runPoll();
    return () => { aliveRef.current = false; };
  }, [sessionId]);

  // Manual verification: single extra API call to force a fresh Stripe check.
  const verifyNow = async () => {
    if (!sessionId) return;
    setVerifying(true);
    try {
      const { data } = await api.get(`/payments/status/${sessionId}`);
      if (data.payment_status === "paid") {
        setStatus("paid");
        setBooking(data.booking);
        clearFlow();
      } else {
        // Still not paid — restart polling for another round.
        attemptsRef.current = 0;
        setStatus("pending");
        runPoll();
      }
    } catch {
      // no-op — keep the timeout UI so user can retry / contact support
    } finally {
      setVerifying(false);
    }
  };

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
              {(() => {
                const firstCts = (booking.gohub_tickets || []).find((t) => t?.qr);
                if (firstCts) {
                  return <div className="text-xs text-emerald-700 font-bold mt-2">TBS boarding pass ready · Scan the QR at the gate.</div>;
                }
                if (booking.gohub_status === "failed") {
                  return <div className="text-xs text-amber-700 font-bold mt-2">TBS boarding pass being finalised — we&apos;ll email it separately.</div>;
                }
                return <div className="text-xs text-zinc-600 mt-2">Show your booking reference at the boarding counter.</div>;
              })()}
            </div>
            {(() => {
              const firstCts = (booking.gohub_tickets || []).find((t) => t?.qr);
              // Prefer the CTS-branded PNG (has gopass logo) if we fetched it;
              // otherwise render the raw QR string with a locally-generated QR.
              const brandedSrc = firstCts?.qr_image;
              return (
                <div className="bg-white p-3 border border-black/10 flex flex-col items-center" data-testid="booking-qr">
                  {brandedSrc ? (
                    <img src={brandedSrc} alt="TBS boarding QR" width={128} height={128} className="block" />
                  ) : (
                    <QRCodeSVG value={firstCts?.qr || booking.reference} size={128} level="M" />
                  )}
                  {firstCts && (
                    <div className="mt-1 text-[9px] font-mono uppercase text-emerald-700 tracking-wider">TBS GATE</div>
                  )}
                </div>
              );
            })()}
          </div>

          {/* Per-passenger CTS QR passes when TBS has issued them. */}
          {(booking.gohub_tickets || []).some((t) => t?.qr) && (
            <div className="mt-6 border border-emerald-200 bg-emerald-50/50 p-4" data-testid="cts-passes">
              <div className="te-overline text-[10px] text-emerald-700 mb-3">TBS Boarding Passes · scan each at the gate</div>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                {booking.gohub_tickets.filter((t) => t?.qr).map((t) => (
                  <div key={t.seat_number} className="bg-white p-2 border border-black/10 flex flex-col items-center" data-testid={`cts-pass-${t.seat_number}`}>
                    {t.qr_image ? (
                      <img src={t.qr_image} alt={`Boarding QR seat ${t.seat_number}`} width={100} height={100} className="block" />
                    ) : (
                      <QRCodeSVG value={t.qr} size={100} level="M" />
                    )}
                    <div className="mt-1 font-mono font-bold text-xs">SEAT {t.seat_number}</div>
                    {t.tickno && <div className="font-mono text-[9px] text-zinc-500">{t.tickno}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}

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
            <Link
              to={`/bookings/${booking.id}${booking.user_id ? "" : `?email=${encodeURIComponent(booking.contact_email || "")}`}`}
              className="te-btn-primary"
              data-testid="view-booking-btn"
            >
              View booking
            </Link>
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
              ? "Your bank / GrabPay may take a moment. Click Verify Payment to re-check, or your booking will finalise automatically within a minute."
              : "You were not charged. Try booking again."}
          </p>
          {status === "timeout" && sessionId && (
            <div className="mt-3 text-[10px] font-mono text-zinc-500">
              Reference: <span className="text-zinc-700">{sessionId.slice(0, 24)}…</span>
            </div>
          )}
          <div className="mt-6 flex gap-3 flex-wrap">
            {status === "timeout" && (
              <button
                onClick={verifyNow}
                disabled={verifying}
                className="te-btn-primary"
                data-testid="verify-payment-btn"
              >
                {verifying ? "Verifying…" : "Verify payment now"}
              </button>
            )}
            {bookingId && <Link to={`/bookings/${bookingId}`} className="te-btn-outline">Check booking status</Link>}
            <Link to="/" className={status === "timeout" ? "te-btn-outline" : "te-btn-primary"}>Start over</Link>
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
