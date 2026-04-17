import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../lib/api";
import { getFlow, setFlow } from "../lib/booking-store";
import { useAuth } from "../lib/auth";

function fmtPrice(v, ccy = "myr") {
  const map = { myr: "RM", sgd: "S$", usd: "$" };
  return `${map[ccy] || ccy.toUpperCase()} ${Number(v).toFixed(2)}`;
}

export default function Passengers() {
  const navigate = useNavigate();
  const flow = getFlow();
  const { user } = useAuth();

  const [passengers, setPassengers] = useState(
    (flow?.selected_seats || []).map((s) => ({
      name: "",
      category: s.category,
      seat_number: s.seat_number,
      ic_or_passport: "",
    }))
  );
  const [email, setEmail] = useState(user?.email || "");
  const [phone, setPhone] = useState(user?.phone || "");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  if (!flow?.schedule_id) {
    return <div className="p-10">Session expired. <a href="/" className="underline">Start over</a></div>;
  }

  const updateP = (i, key, val) => {
    const copy = [...passengers];
    copy[i][key] = val;
    setPassengers(copy);
  };

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    for (const p of passengers) {
      if (!p.name.trim()) return setError("All passenger names are required.");
    }
    if (!email || !phone) return setError("Contact email and phone required.");
    setLoading(true);
    try {
      const body = {
        schedule_id: flow.schedule_id,
        seat_assignments: passengers.map((p, idx) => ({ seat_number: p.seat_number, passenger_index: idx })),
        passengers: passengers.map((p) => ({ name: p.name, category: p.category, ic_or_passport: p.ic_or_passport })),
        contact_email: email,
        contact_phone: phone,
      };
      const { data: booking } = await api.post("/bookings", body);
      setFlow({ ...flow, booking });

      // Create Stripe checkout session
      const { data: checkout } = await api.post("/payments/checkout", {
        booking_id: booking.id,
        origin_url: window.location.origin,
      });
      window.location.href = checkout.url;
    } catch (e) {
      setError(e?.response?.data?.detail?.message || e?.response?.data?.detail || "Could not create booking.");
      setLoading(false);
    }
  };

  const s = flow.schedule;

  return (
    <div className="px-6 md:px-12 lg:px-20 py-10">
      <div className="te-overline mb-2">Step 03 · Passenger details</div>
      <h1 className="text-3xl md:text-4xl font-black tracking-tight mb-8">Who is travelling?</h1>

      <form onSubmit={submit} className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        <div className="lg:col-span-8 space-y-4">
          {passengers.map((p, i) => (
            <div key={i} className="te-card p-6">
              <div className="flex items-center justify-between mb-4">
                <div className="font-mono text-sm font-black">SEAT {p.seat_number}</div>
                <div className="text-[10px] font-mono uppercase border border-black/20 px-2 py-1">{p.category}</div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="te-label">Full name (as on IC/Passport)</label>
                  <input
                    required
                    className="te-input"
                    value={p.name}
                    onChange={(e) => updateP(i, "name", e.target.value)}
                    data-testid={`pax-name-${i}`}
                  />
                </div>
                <div>
                  <label className="te-label">IC / Passport (optional)</label>
                  <input
                    className="te-input"
                    value={p.ic_or_passport}
                    onChange={(e) => updateP(i, "ic_or_passport", e.target.value)}
                    data-testid={`pax-ic-${i}`}
                  />
                </div>
              </div>
            </div>
          ))}

          <div className="te-card p-6">
            <div className="te-overline mb-3">Contact (for ticket delivery)</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="te-label">Email</label>
                <input required type="email" className="te-input" value={email} onChange={(e) => setEmail(e.target.value)} data-testid="contact-email" />
              </div>
              <div>
                <label className="te-label">Phone</label>
                <input required className="te-input" value={phone} onChange={(e) => setPhone(e.target.value)} data-testid="contact-phone" />
              </div>
            </div>
            {!user && (
              <div className="mt-4 text-xs text-zinc-600">
                Booking as guest. <a className="underline font-bold" href="/login">Log in</a> to save to your account.
              </div>
            )}
          </div>
        </div>

        <div className="lg:col-span-4">
          <div className="te-card p-6 sticky top-20">
            <div className="te-overline mb-3">Trip summary</div>
            <div className="font-black text-lg">{flow.from.city} → {flow.to.city}</div>
            <div className="text-xs font-mono text-zinc-500">{s.departure_date} · {s.departure_time}</div>
            <div className="te-divider-dashed my-4" />
            <div className="space-y-2 text-sm">
              {passengers.map((p) => (
                <div key={p.seat_number} className="flex justify-between">
                  <span><span className="font-mono">{p.seat_number}</span> {p.category === "adult" ? "Adult" : "Child"}</span>
                  <span className="font-mono">{fmtPrice(p.category === "adult" ? s.adult_fare : s.adult_fare * 0.5, s.currency)}</span>
                </div>
              ))}
            </div>
            <div className="te-divider-dashed my-4" />
            <div className="flex justify-between font-black text-xl">
              <span>TOTAL</span>
              <span className="font-mono" data-testid="passengers-total">
                {fmtPrice(
                  passengers.reduce((acc, p) => acc + (p.category === "adult" ? s.adult_fare : s.adult_fare * 0.5), 0),
                  s.currency
                )}
              </span>
            </div>
            {error && <div className="mt-4 text-xs font-bold text-red-600" data-testid="passengers-error">{error}</div>}
            <button className="te-btn-accent w-full mt-5 disabled:opacity-40" disabled={loading} data-testid="pay-now-btn">
              {loading ? "Redirecting to payment…" : "Pay now →"}
            </button>
            <div className="text-[10px] font-mono text-zinc-500 mt-3 text-center">SECURE CHECKOUT · STRIPE</div>
          </div>
        </div>
      </form>
    </div>
  );
}
