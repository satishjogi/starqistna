import React from "react";
import { Link, useSearchParams } from "react-router-dom";

export default function PaymentCancel() {
  const [p] = useSearchParams();
  const bookingId = p.get("booking_id");
  return (
    <div className="px-4 md:px-6 lg:px-10 py-16 max-w-3xl mx-auto">
      <div className="te-card p-10" data-testid="payment-cancel">
        <div className="te-overline mb-2 text-amber-600">Cancelled</div>
        <h1 className="text-4xl font-black tracking-tight">Payment cancelled.</h1>
        <p className="text-zinc-600 mt-3">Your seat lock will expire in a few minutes if you don't complete payment.</p>
        <div className="mt-6 flex gap-3">
          {bookingId && <Link to={`/bookings/${bookingId}`} className="te-btn-outline">View booking</Link>}
          <Link to="/" className="te-btn-primary">Search again</Link>
        </div>
      </div>
    </div>
  );
}
