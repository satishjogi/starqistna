import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../lib/api";

const CCY = { myr: "RM", sgd: "S$", usd: "$" };
const fmtPrice = (v, c = "myr") => `${CCY[c] || c.toUpperCase()} ${Number(v || 0).toFixed(0)}`;

function humaniseDelta(mins, isToday) {
  if (mins == null) return "";
  if (!isToday) {
    if (mins < 60 * 24 * 2) return `tomorrow · ${Math.round(mins / 60)}h away`;
    return `in ${Math.round(mins / (60 * 24))} days`;
  }
  if (mins < 60) return `in ${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? `in ${h}h ${m}m` : `in ${h}h`;
}

function headline(item) {
  const when = item.is_today
    ? humaniseDelta(item.minutes_until_departure, true)
    : `${item.departure_date.slice(5)} · ${item.departure_time}`;
  if (item.is_today && item.arrival_time) {
    return {
      top: `Next bus to ${item.to_city}`,
      sub: `From ${item.from_city} · departs ${when} · reach by ${item.arrival_time}`,
    };
  }
  return {
    top: `${item.from_city} → ${item.to_city}`,
    sub: item.is_today
      ? `Departs ${when} · arrives ${item.arrival_time || "—"}`
      : `Next ${when} · ${item.departure_time}`,
  };
}

export default function PopularNow() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    api
      .get("/popular/now?limit=6")
      .then(({ data }) => setItems(data.items || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const go = (it) => {
    const params = new URLSearchParams({
      from: it.from_terminal.id,
      to: it.to_terminal.id,
      date: it.departure_date,
      adults: "1",
      children: "0",
    });
    navigate(`/search?${params.toString()}`);
  };

  if (!loading && items.length === 0) return null;

  return (
    <section
      className="px-4 md:px-6 lg:px-10 py-12 border-t border-black/10 bg-zinc-50"
      data-testid="popular-now-section"
    >
      <div className="flex flex-wrap items-end justify-between gap-3 mb-6">
        <div>
          <div className="te-overline mb-2 text-[#B5121B]">⚡ Popular right now</div>
          <h2 className="text-2xl md:text-4xl font-black tracking-tight">
            Next buses leaving soon.
          </h2>
        </div>
        <div className="font-mono text-[10px] text-zinc-500 tracking-wider">
          TAP TO BOOK · LIVE DATA
        </div>
      </div>

      {loading ? (
        <div className="font-mono text-xs text-zinc-500">LOADING…</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-[1px] bg-black/10 border border-black/10">
          {items.map((it) => {
            const h = headline(it);
            const urgent =
              it.is_today &&
              it.minutes_until_departure != null &&
              it.minutes_until_departure <= 120;
            const almostFull = it.seats_available <= 10;
            return (
              <button
                key={it.schedule_id}
                type="button"
                onClick={() => go(it)}
                className="group bg-white p-5 text-left hover:bg-white hover:shadow-[inset_4px_0_0_0_#B5121B] transition-all duration-200"
                data-testid={`popular-now-${it.from_city}-${it.to_city}`.replace(/\s/g, "")}
              >
                <div className="flex items-center justify-between">
                  <span
                    className={`text-[9px] font-mono font-bold tracking-[0.2em] px-2 py-1 uppercase ${
                      urgent
                        ? "bg-[#B5121B] text-white"
                        : it.is_today
                        ? "bg-black text-white"
                        : "bg-zinc-200 text-zinc-700"
                    }`}
                  >
                    {urgent ? "LEAVES SOON" : it.is_today ? "TODAY" : "UPCOMING"}
                  </span>
                  <span className="font-mono text-[10px] text-zinc-400">
                    {it.departure_time}
                  </span>
                </div>

                <h3 className="mt-4 text-xl md:text-2xl font-black tracking-tight leading-tight">
                  {h.top}
                </h3>
                <p className="mt-1 text-xs md:text-sm text-zinc-600 leading-snug">
                  {h.sub}
                </p>

                <div className="mt-5 flex items-end justify-between">
                  <div>
                    <div className="te-overline text-[9px] text-zinc-500">FROM</div>
                    <div className="font-mono font-black text-lg">
                      {fmtPrice(it.fare, it.currency)}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="te-overline text-[9px] text-zinc-500">SEATS</div>
                    <div
                      className={`font-mono font-black text-lg ${
                        almostFull ? "text-[#B5121B]" : "text-black"
                      }`}
                    >
                      {it.seats_available}
                      {almostFull ? " left" : ""}
                    </div>
                  </div>
                  <div className="text-xs font-mono text-zinc-400 group-hover:text-black transition-colors">
                    Book →
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
