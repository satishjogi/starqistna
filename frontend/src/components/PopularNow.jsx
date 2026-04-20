import React, { useEffect, useMemo, useState } from "react";
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

// Compute live seconds-until-departure from the absolute departure datetime string.
function secondsUntil(dateStr, timeStr, now) {
  try {
    const dep = new Date(`${dateStr}T${timeStr}:00Z`);
    return Math.max(0, Math.floor((dep.getTime() - now) / 1000));
  } catch {
    return null;
  }
}

function pad(n) { return String(n).padStart(2, "0"); }

function LiveCountdown({ secondsLeft, urgent }) {
  const mm = Math.floor(secondsLeft / 60);
  const ss = secondsLeft % 60;
  const hh = Math.floor(mm / 60);
  const mmMod = mm % 60;
  const display = hh > 0 ? `${hh}h ${pad(mmMod)}m ${pad(ss)}s` : `${pad(mmMod)}:${pad(ss)}`;
  return (
    <span
      className={`font-mono font-black tabular-nums ${urgent ? "text-[#B5121B] pulse-urgent" : "text-black"}`}
      data-testid="popular-now-countdown"
    >
      {display}
    </span>
  );
}

export default function PopularNow() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(() => Date.now());
  const navigate = useNavigate();

  const load = () => {
    api
      .get("/popular/now?limit=6")
      .then(({ data }) => setItems(data.items || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    // Silent auto-refresh every 2 minutes
    const refresh = setInterval(load, 120_000);
    // Tick every second for live countdown
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => { clearInterval(refresh); clearInterval(tick); };
  }, []);

  const enriched = useMemo(
    () =>
      items.map((it) => ({
        ...it,
        _seconds_left: secondsUntil(it.departure_date, it.departure_time, now),
      })),
    [items, now]
  );

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
      className="px-4 md:px-6 lg:px-10 pt-6 pb-8 border-t border-black/10 bg-zinc-50"
      data-testid="popular-now-section"
    >
      <div className="flex flex-wrap items-end justify-between gap-3 mb-5">
        <div>
          <div className="te-overline mb-1 text-[#B5121B]">⚡ Popular right now</div>
          <h2 className="text-2xl md:text-3xl font-black tracking-tight">
            Next buses leaving soon.
          </h2>
        </div>
        <div className="font-mono text-[10px] text-zinc-500 tracking-wider">
          TAP TO BOOK · AUTO-REFRESH · LIVE COUNTDOWN
        </div>
      </div>

      {loading ? (
        <div className="font-mono text-xs text-zinc-500">LOADING…</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-[1px] bg-black/10 border border-black/10">
          {enriched.map((it) => {
            const h = headline(it);
            const secs = it._seconds_left;
            // Urgent: leaving within 60 min AND today — pulsing red
            const urgent = it.is_today && secs != null && secs <= 60 * 60 && secs > 0;
            const imminent = it.is_today && secs != null && secs <= 10 * 60 && secs > 0;
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
                        ? "bg-[#B5121B] text-white pulse-urgent"
                        : it.is_today
                        ? "bg-black text-white"
                        : "bg-zinc-200 text-zinc-700"
                    }`}
                  >
                    {imminent ? "BOARDING NOW" : urgent ? "LEAVES SOON" : it.is_today ? "TODAY" : "UPCOMING"}
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

                {/* Live countdown row — only on 'today' cards */}
                {it.is_today && secs != null && (
                  <div className="mt-3 flex items-baseline gap-2 text-xs">
                    <span className="te-overline text-[9px] text-zinc-500">DEPARTS IN</span>
                    <LiveCountdown secondsLeft={secs} urgent={urgent} />
                  </div>
                )}

                {/* Seats scarcity flash — only when 1..3 seats remain */}
                {it.seats_available > 0 && it.seats_available <= 3 && (
                  <div
                    className="mt-2 inline-flex items-center gap-1 text-[10px] font-mono font-bold tracking-wider uppercase px-2 py-1 bg-[#B5121B] text-white pulse-urgent"
                    data-testid="popular-now-scarcity"
                  >
                    🔥 Only {it.seats_available} seat{it.seats_available === 1 ? "" : "s"} left
                  </div>
                )}

                <div className="mt-4 flex items-end justify-between">
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
