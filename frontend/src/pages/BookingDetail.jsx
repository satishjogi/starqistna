import React, { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { QRCodeSVG } from "qrcode.react";
import api from "../lib/api";
import CancelBookingButton from "../components/CancelBookingButton";

function fmtPrice(v, ccy = "myr") {
  const map = { myr: "RM", sgd: "S$", usd: "$" };
  return `${map[ccy] || ccy.toUpperCase()} ${Number(v).toFixed(2)}`;
}

export default function BookingDetail() {
  const { id } = useParams();
  const [searchParams] = useSearchParams();
  const guestEmail = searchParams.get("email") || "";
  const [b, setB] = useState(null);
  const [err, setErr] = useState("");

  // Guests pass ?email=… so the backend can verify ownership without auth.
  const url = guestEmail ? `/bookings/${id}?email=${encodeURIComponent(guestEmail)}` : `/bookings/${id}`;

  useEffect(() => {
    api.get(url).then(({ data }) => setB(data)).catch((e) => setErr(e?.response?.data?.detail || "Failed to load booking"));
  }, [url]);

  const reload = () => {
    api.get(url).then(({ data }) => setB(data)).catch(() => {});
  };

  if (err) return <div className="p-10 text-red-600">{err}</div>;
  if (!b) return <div className="p-10 font-mono text-zinc-500">LOADING…</div>;

  return (
    <div className="px-4 md:px-6 lg:px-10 py-10 max-w-4xl">
      <Link to="/dashboard" className="text-xs font-mono text-zinc-500 hover:text-black" data-testid="back-dashboard">← MY BOOKINGS</Link>

      <div className="mt-4 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="te-overline">Booking reference</div>
          <div className="font-mono text-4xl font-black" data-testid="detail-reference">{b.reference}</div>
          {b.boarded_at && (
            <div className="inline-block mt-2 text-[10px] font-mono font-bold uppercase px-2 py-1 bg-emerald-100 text-emerald-700">
              Boarded {new Date(b.boarded_at).toLocaleString()}
            </div>
          )}
        </div>
        <div className="flex items-center gap-4">
          {b.status === "confirmed" && (
            <div className="bg-white p-2 border border-black/10" data-testid="detail-qr">
              <QRCodeSVG value={b.reference} size={112} level="M" />
            </div>
          )}
          <div className={`text-xs font-mono font-black uppercase px-3 py-2 ${
            b.status === "confirmed"
              ? "bg-emerald-100 text-emerald-700"
              : b.status === "cancelled_refunded" || b.status === "cancelled_burned"
              ? "bg-red-100 text-red-700"
              : "bg-amber-100 text-amber-700"
          }`} data-testid="detail-status">
            {b.status.replace(/_/g, " ")}
          </div>
        </div>
      </div>

      <div className="te-card p-8 mt-6">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          <div>
            <div className="te-overline text-[10px]">From</div>
            <div className="text-2xl font-black">{b.from?.city}</div>
            <div className="text-xs font-mono text-zinc-500">{b.from?.code} · {b.from?.name}</div>
          </div>
          <div>
            <div className="te-overline text-[10px]">To</div>
            <div className="text-2xl font-black">{b.to?.city}</div>
            <div className="text-xs font-mono text-zinc-500">{b.to?.code} · {b.to?.name}</div>
          </div>
          <div>
            <div className="te-overline text-[10px]">Departure</div>
            <div className="font-mono font-black">{b.departure_date} · {b.departure_time}</div>
          </div>
          <div>
            <div className="te-overline text-[10px]">Operator</div>
            <div className="font-black">{b.schedule?.bus_operator} <span className="text-xs text-zinc-500 font-mono">· {b.schedule?.bus_type}</span></div>
          </div>
        </div>

        <div className="te-divider-dashed my-6" />

        <div className="te-overline text-[10px] mb-3">Passengers</div>
        <div className="space-y-2">
          {b.passengers.map((p, i) => (
            <div key={i} className="flex justify-between items-center border-b border-black/5 pb-2">
              <div>
                <div className="font-bold">{p.name}</div>
                <div className="text-[10px] font-mono uppercase text-zinc-500">{p.category} {p.ic_or_passport && `· ${p.ic_or_passport}`}</div>
              </div>
              <div className="font-mono font-black">SEAT {p.seat_number}</div>
            </div>
          ))}
        </div>

        <div className="te-divider-dashed my-6" />

        <div className="space-y-2 text-sm">
          <div className="flex justify-between"><span>Adult × {b.pricing.adults}</span><span className="font-mono">{fmtPrice(b.pricing.adults * b.pricing.adult_fare, b.pricing.currency)}</span></div>
          <div className="flex justify-between"><span>Child × {b.pricing.children}</span><span className="font-mono">{fmtPrice(b.pricing.children * b.pricing.child_fare, b.pricing.currency)}</span></div>
          {b.pricing.discount > 0 && (
            <div className="flex justify-between text-emerald-700">
              <span>Promo {b.pricing.promo?.code ? `(${b.pricing.promo.code})` : ""}</span>
              <span className="font-mono">−{fmtPrice(b.pricing.discount, b.pricing.currency)}</span>
            </div>
          )}
          <div className="flex justify-between font-black text-lg mt-2"><span>TOTAL</span><span className="font-mono">{fmtPrice(b.pricing.total, b.pricing.currency)}</span></div>
        </div>

        {b.status === "confirmed" && (
          <div className="te-divider-dashed my-6" />
        )}
        {b.status === "confirmed" && (
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3" data-testid="cancel-section">
            <div className="text-[11px] font-mono text-zinc-500 leading-relaxed">
              REFUND POLICY · CANCEL ≥24H BEFORE DEPARTURE → FULL REFUND<br/>
              CANCEL &lt;24H BEFORE DEPARTURE → TICKET BURNED · NO REFUND
            </div>
            <CancelBookingButton booking={b} onCancelled={reload} />
          </div>
        )}
        {(b.status === "cancelled_refunded" || b.status === "cancelled_burned") && (
          <div className="te-divider-dashed my-6" />
        )}
        {b.status === "cancelled_refunded" && (
          <div className="bg-emerald-50 border border-emerald-200 p-4 text-sm" data-testid="cancelled-refunded-banner">
            <div className="te-overline text-[10px] text-emerald-700 mb-1">Cancelled · Full refund issued</div>
            <div className="text-emerald-800">
              Refund of <b>{fmtPrice(b.cancellation_refund?.amount || 0, (b.cancellation_refund?.currency || "myr").toLowerCase())}</b> sent to your original payment method.
              {b.cancelled_at && <> Cancelled on {new Date(b.cancelled_at).toLocaleString()}.</>}
            </div>
          </div>
        )}
        {b.status === "cancelled_burned" && (
          <div className="bg-red-50 border border-red-200 p-4 text-sm" data-testid="cancelled-burned-banner">
            <div className="te-overline text-[10px] text-red-700 mb-1">Cancelled · No refund</div>
            <div className="text-red-800">
              Cancellation was made within 24 hours of departure, so the ticket was burned per our refund policy.
              {b.cancelled_at && <> Cancelled on {new Date(b.cancelled_at).toLocaleString()}.</>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
