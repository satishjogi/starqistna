import React, { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import NextTripWidget from "../components/NextTripWidget";
import LastLoginBanner from "../components/LastLoginBanner";
import SetPasswordCard from "../components/SetPasswordCard";

function fmtPrice(v, ccy = "myr") {
  const map = { myr: "RM", sgd: "S$", usd: "$" };
  return `${map[ccy] || ccy.toUpperCase()} ${Number(v).toFixed(2)}`;
}

const isCancelled = (b) =>
  b.status === "cancelled_refunded" || b.status === "cancelled_burned";

export default function Dashboard() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [bookings, setBookings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("upcoming");

  useEffect(() => {
    if (!authLoading && !user) navigate("/login");
  }, [user, authLoading, navigate]);

  useEffect(() => {
    if (!user) return;
    api.get("/bookings/me").then(({ data }) => setBookings(data)).finally(() => setLoading(false));
  }, [user]);

  const { upcoming, past, cancelled } = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    const live = bookings.filter((b) => !isCancelled(b));
    return {
      upcoming: live.filter((b) => b.departure_date >= today),
      past: live.filter((b) => b.departure_date < today),
      cancelled: bookings.filter(isCancelled),
    };
  }, [bookings]);

  const visible = { upcoming, past, cancelled }[filter] || [];

  const emptyCopy = {
    upcoming: "No upcoming trips.",
    past: "No past trips yet.",
    cancelled: "No cancelled bookings.",
  }[filter];

  if (!user) return null;

  return (
    <div className="px-4 md:px-6 lg:px-10 py-10">
      <div className="te-overline mb-2">Passenger dashboard</div>
      <h1 className="text-4xl md:text-5xl font-black tracking-tight">Hi, {(user.full_name || user.email || "there").split(" ")[0]}.</h1>
      <p className="text-zinc-600 mt-2">{user.email}</p>

      <LastLoginBanner user={user} />

      <SetPasswordCard />

      <NextTripWidget bookings={bookings} />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-10">
        <div className="te-card p-6">
          <div className="te-overline text-[10px]">Upcoming</div>
          <div className="text-4xl font-black font-mono mt-1" data-testid="stat-upcoming">{upcoming.length}</div>
        </div>
        <div className="te-card p-6">
          <div className="te-overline text-[10px]">Past</div>
          <div className="text-4xl font-black font-mono mt-1" data-testid="stat-past">{past.length}</div>
        </div>
        <div className="te-card p-6">
          <div className="te-overline text-[10px] text-[#B5121B]">Cancelled</div>
          <div className="text-4xl font-black font-mono mt-1 text-[#B5121B]" data-testid="stat-cancelled">{cancelled.length}</div>
        </div>
        <div className="te-card p-6 flex items-center justify-between">
          <div>
            <div className="te-overline text-[10px]">Book another</div>
            <div className="font-black text-base leading-tight">Plan a trip</div>
          </div>
          <Link to="/" className="te-btn-accent" data-testid="new-booking-btn">New</Link>
        </div>
      </div>

      <div className="mt-12">
        <div className="flex gap-1 border-b border-black/10 flex-wrap" data-testid="bookings-tabs">
          {[
            { id: "upcoming", label: "Upcoming", count: upcoming.length },
            { id: "past", label: "Past", count: past.length },
            { id: "cancelled", label: "Cancelled", count: cancelled.length },
          ].map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setFilter(t.id)}
              className={`px-4 py-2 text-xs font-bold uppercase tracking-wider flex items-center gap-2 ${
                filter === t.id ? "bg-black text-white" : "text-zinc-500 hover:text-black"
              }`}
              data-testid={`bookings-tab-${t.id}`}
            >
              <span>{t.label}</span>
              <span className={`text-[10px] font-mono px-1.5 py-0.5 ${
                filter === t.id ? "bg-white/20" : "bg-zinc-100"
              }`}>
                {t.count}
              </span>
            </button>
          ))}
        </div>

        <div className="mt-6" data-testid={`bookings-list-${filter}`}>
          {loading ? (
            <div className="font-mono text-zinc-500">LOADING…</div>
          ) : visible.length === 0 ? (
            <div className="te-card p-6 text-sm text-zinc-600" data-testid={`bookings-empty-${filter}`}>{emptyCopy}</div>
          ) : (
            <div className="space-y-3">{visible.map((b) => <BookingCard key={b.id} b={b} />)}</div>
          )}
        </div>
      </div>
    </div>
  );

  function BookingCard({ b }) {
    return (
      <Link to={`/bookings/${b.id}`} className="te-card p-5 grid grid-cols-12 gap-3 items-center hover:border-black/40 transition" data-testid={`booking-card-${b.reference}`}>
        <div className="col-span-12 md:col-span-3">
          <div className="te-overline text-[10px]">Reference</div>
          <div className="font-mono font-black">{b.reference}</div>
        </div>
        <div className="col-span-6 md:col-span-4">
          <div className="font-black">{b.from?.city} → {b.to?.city}</div>
          <div className="text-[11px] font-mono text-zinc-500">{b.from?.code} → {b.to?.code}</div>
        </div>
        <div className="col-span-6 md:col-span-2">
          <div className="font-mono text-sm font-bold">{b.departure_date}</div>
          <div className="font-mono text-[11px] text-zinc-500">{b.departure_time}</div>
        </div>
        <div className="col-span-6 md:col-span-2">
          <div className="te-overline text-[10px]">Seats</div>
          <div className="font-mono font-bold">{b.seats.join(", ")}</div>
        </div>
        <div className="col-span-6 md:col-span-1 text-right">
          <div className={`text-[10px] font-mono font-bold uppercase px-2 py-1 inline-block ${
            b.status === "confirmed"
              ? "bg-emerald-100 text-emerald-700"
              : isCancelled(b)
              ? "bg-red-100 text-red-700"
              : "bg-zinc-100 text-zinc-600"
          }`}>
            {b.status.replace(/_/g, " ")}
          </div>
          <div className="font-mono text-sm font-black mt-1">{fmtPrice(b.pricing.total, b.pricing.currency)}</div>
        </div>
      </Link>
    );
  }
}
