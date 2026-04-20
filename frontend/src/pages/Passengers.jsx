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

  // Promo code state
  const [promoInput, setPromoInput] = useState("");
  const [promo, setPromo] = useState(null); // {code,type,value,discount_amount}
  const [promoError, setPromoError] = useState("");
  const [promoLoading, setPromoLoading] = useState(false);

  // Gateway selection — currency-aware, all via Stripe
  const scheduleCurrency = (flow?.schedule?.currency || "myr").toLowerCase();
  const gatewayOptions = [
    { id: "card", name: "Credit / Debit Card", provider: "Stripe", methods: "Visa · Mastercard · Amex", available: true },
    { id: "grabpay", name: "GrabPay", provider: "Stripe", methods: "GrabPay wallet", available: scheduleCurrency === "myr" || scheduleCurrency === "sgd" },
    ...(scheduleCurrency === "myr"
      ? [{ id: "fpx", name: "FPX Online Banking", provider: "Stripe", methods: "Maybank · CIMB · PBB · RHB · all MY banks", available: true }]
      : []),
  ];
  const [gateway, setGateway] = useState("card");

  if (!flow?.schedule_id) {
    return <div className="p-10">Session expired. <a href="/" className="underline">Start over</a></div>;
  }

  const updateP = (i, key, val) => {
    const copy = [...passengers];
    copy[i][key] = val;
    setPassengers(copy);
  };

  const applyPromo = async () => {
    setPromoError("");
    if (!promoInput.trim()) return;
    setPromoLoading(true);
    try {
      const adults = passengers.filter((p) => p.category === "adult").length;
      const children = passengers.filter((p) => p.category === "child").length;
      const { data } = await api.post("/promo/validate", {
        code: promoInput.trim(),
        schedule_id: flow.schedule_id,
        passenger_count: passengers.length,
        adults,
        children,
      });
      setPromo(data.promo);
    } catch (e) {
      setPromo(null);
      setPromoError(e?.response?.data?.detail || "Invalid promo code");
    } finally {
      setPromoLoading(false);
    }
  };

  const removePromo = () => { setPromo(null); setPromoInput(""); setPromoError(""); };

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
        promo_code: promo?.code || undefined,
      };
      const { data: booking } = await api.post("/bookings", body);
      setFlow({ ...flow, booking });

      // Create Stripe checkout session
      const { data: checkout } = await api.post("/payments/checkout", {
        booking_id: booking.id,
        origin_url: window.location.origin,
        gateway,
      });
      window.location.href = checkout.url;
    } catch (e) {
      setError(e?.response?.data?.detail?.message || e?.response?.data?.detail || "Could not create booking.");
      setLoading(false);
    }
  };

  const s = flow.schedule;

  return (
    <div className="px-4 md:px-6 lg:px-10 py-10">
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

          <div className="te-card p-6">
            <div className="te-overline mb-3">Promo code</div>
            {!promo ? (
              <div className="flex gap-2">
                <input
                  className="te-input flex-1 font-mono uppercase"
                  placeholder="Enter code e.g. WELCOME10"
                  value={promoInput}
                  onChange={(e) => setPromoInput(e.target.value.toUpperCase())}
                  data-testid="promo-input"
                />
                <button
                  type="button"
                  onClick={applyPromo}
                  disabled={promoLoading || !promoInput.trim()}
                  className="te-btn-outline disabled:opacity-40"
                  data-testid="promo-apply-btn"
                >
                  {promoLoading ? "Checking…" : "Apply"}
                </button>
              </div>
            ) : (
              <div className="flex items-center justify-between border border-emerald-300 bg-emerald-50 px-4 py-3 rounded-sm" data-testid="promo-applied">
                <div>
                  <div className="font-mono font-black text-sm">{promo.code}</div>
                  <div className="text-[10px] font-mono text-emerald-700">
                    {promo.type === "percent" ? `${promo.value}% off` : `Flat ${promo.value} off`} · −{fmtPrice(promo.discount_amount, s.currency)}
                  </div>
                </div>
                <button type="button" onClick={removePromo} className="text-xs font-bold text-emerald-700 underline" data-testid="promo-remove-btn">Remove</button>
              </div>
            )}
            {promoError && <div className="text-xs font-bold text-red-600 mt-2" data-testid="promo-error">{promoError}</div>}
            <div className="text-[10px] font-mono text-zinc-500 mt-2">TRY: WELCOME10 · RAYA5</div>
          </div>

          <div className="te-card p-6">
            <div className="flex items-center justify-between mb-3">
              <div className="te-overline">Payment method</div>
              <div className="text-[10px] font-mono font-bold bg-black text-white px-2 py-1 tracking-wider" data-testid="currency-badge">
                BILLED IN {scheduleCurrency.toUpperCase()}
              </div>
            </div>
            {scheduleCurrency === "sgd" && (
              <div className="text-xs text-zinc-600 mb-3">
                Singapore-boarding tickets are billed in Singapore Dollars (SGD).
              </div>
            )}
            <div className="space-y-2">
              {gatewayOptions.map((g) => {
                const selected = gateway === g.id && g.available;
                return (
                  <button
                    key={g.id}
                    type="button"
                    disabled={!g.available}
                    onClick={() => g.available && setGateway(g.id)}
                    className={`w-full text-left p-4 border transition-all ${
                      selected
                        ? "border-[#B5121B] bg-[#B5121B]/5"
                        : g.available
                        ? "border-black/15 hover:border-black/40"
                        : "border-black/10 opacity-50 cursor-not-allowed"
                    }`}
                    data-testid={`gateway-${g.id}`}
                  >
                    <div className="flex items-center gap-3">
                      <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0 ${
                        selected ? "border-[#B5121B]" : "border-black/30"
                      }`}>
                        {selected && <div className="w-2 h-2 rounded-full bg-[#B5121B]" />}
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <div className="font-bold">{g.name}</div>
                          <div className="text-[10px] font-mono text-zinc-500 tracking-wider">via {g.provider}</div>
                          {g.note && (
                            <span className="text-[9px] font-mono font-bold px-2 py-0.5 bg-amber-100 text-amber-700 tracking-wider uppercase">
                              {g.note}
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] font-mono text-zinc-500 mt-1">{g.methods}</div>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
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
            {(() => {
              const subtotal = passengers.reduce((acc, p) => acc + (p.category === "adult" ? s.adult_fare : s.adult_fare * 0.5), 0);
              const discount = promo?.discount_amount || 0;
              const total = Math.max(0, subtotal - discount);
              return (
                <>
                  <div className="flex justify-between text-sm"><span>Subtotal</span><span className="font-mono">{fmtPrice(subtotal, s.currency)}</span></div>
                  {discount > 0 && (
                    <div className="flex justify-between text-sm text-emerald-700 mt-1" data-testid="summary-discount-row">
                      <span>Promo ({promo.code})</span>
                      <span className="font-mono">−{fmtPrice(discount, s.currency)}</span>
                    </div>
                  )}
                  <div className="flex justify-between font-black text-xl mt-3">
                    <span>TOTAL</span>
                    <span className="font-mono" data-testid="passengers-total">{fmtPrice(total, s.currency)}</span>
                  </div>
                </>
              );
            })()}
            {error && <div className="mt-4 text-xs font-bold text-red-600" data-testid="passengers-error">{error}</div>}
            <button className="te-btn-accent w-full mt-5 disabled:opacity-40" disabled={loading} data-testid="pay-now-btn">
              {loading ? "Redirecting to payment…" : `Pay with ${(gatewayOptions.find((g) => g.id === gateway)?.name) || "Card"} →`}
            </button>
            <div className="text-[10px] font-mono text-zinc-500 mt-3 text-center">SECURE CHECKOUT · STRIPE</div>
          </div>
        </div>
      </form>
    </div>
  );
}
