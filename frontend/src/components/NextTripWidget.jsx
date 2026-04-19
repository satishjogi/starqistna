import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { QRCodeSVG } from "qrcode.react";

function pad(n) {
  return String(n).padStart(2, "0");
}

function useCountdown(targetIso) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const diff = Math.max(0, new Date(targetIso).getTime() - now);
  const days = Math.floor(diff / 86400000);
  const hours = Math.floor((diff % 86400000) / 3600000);
  const minutes = Math.floor((diff % 3600000) / 60000);
  const seconds = Math.floor((diff % 60000) / 1000);
  return { days, hours, minutes, seconds, total: diff };
}

// Generate a minimal .ics file for the trip and trigger a download
function downloadIcs(b) {
  const dt = `${b.departure_date.replace(/-/g, "")}T${b.departure_time.replace(":", "")}00`;
  // Assume trip lasts 4 hours (generic) — calendar block is informational
  const endDate = new Date(`${b.departure_date}T${b.departure_time}:00`);
  endDate.setHours(endDate.getHours() + 4);
  const end =
    endDate.getFullYear() +
    pad(endDate.getMonth() + 1) +
    pad(endDate.getDate()) +
    "T" +
    pad(endDate.getHours()) +
    pad(endDate.getMinutes()) +
    "00";

  const summary = `Star Qistna · ${b.from?.city} → ${b.to?.city}`;
  const desc = [
    `Reference: ${b.reference}`,
    `Seats: ${b.seats.join(", ")}`,
    `Passengers: ${b.passengers?.length || b.seats.length}`,
    `From: ${b.from?.name} (${b.from?.code})`,
    `To: ${b.to?.name} (${b.to?.code})`,
  ].join("\\n");

  const ics = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Star Qistna//Booking//EN",
    "BEGIN:VEVENT",
    `UID:${b.reference}@starqistna.com`,
    `DTSTAMP:${dt}`,
    `DTSTART:${dt}`,
    `DTEND:${end}`,
    `SUMMARY:${summary}`,
    `DESCRIPTION:${desc}`,
    `LOCATION:${b.from?.name || ""}`,
    "END:VEVENT",
    "END:VCALENDAR",
  ].join("\r\n");

  const blob = new Blob([ics], { type: "text/calendar" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `StarQistna-${b.reference}.ics`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export default function NextTripWidget({ bookings }) {
  const next = useMemo(() => {
    const now = Date.now();
    const upcoming = (bookings || [])
      .filter((b) => b.status === "confirmed" && b.departure_date && b.departure_time)
      .map((b) => ({ ...b, _when: new Date(`${b.departure_date}T${b.departure_time}:00`).getTime() }))
      .filter((b) => b._when >= now - 60_000) // include currently-departing as "now"
      .sort((a, b) => a._when - b._when);
    return upcoming[0] || null;
  }, [bookings]);

  const targetIso = next ? `${next.departure_date}T${next.departure_time}:00` : null;
  const { days, hours, minutes, seconds, total } = useCountdown(targetIso || new Date().toISOString());

  if (!next) return null;

  const isDeparting = total <= 0;
  const boardingSoon = total > 0 && total <= 60 * 60 * 1000; // within 1h

  return (
    <div
      className="mt-8 relative overflow-hidden p-6 md:p-8 bg-black text-white border border-black/10 rounded-sm"
      data-testid="next-trip-widget"
    >
      {/* Decorative accent bar */}
      <div className="absolute left-0 top-0 bottom-0 w-1 bg-[var(--brand-red,#E30613)]" />

      <div className="flex items-start justify-between flex-wrap gap-4">
        <div>
          <div className="te-overline text-[10px] text-zinc-400">
            {isDeparting ? "Departing now" : boardingSoon ? "Boarding soon" : "Your next trip"}
          </div>
          <div className="mt-1 text-2xl md:text-3xl font-black tracking-tight">
            {next.from?.city} <span className="text-zinc-500">→</span> {next.to?.city}
          </div>
          <div className="font-mono text-xs text-zinc-400 mt-1">
            {next.from?.code} → {next.to?.code} · REF {next.reference}
          </div>
        </div>

        <div className="bg-white text-black p-2 rounded-sm" data-testid="next-trip-qr">
          <QRCodeSVG value={next.reference} size={72} level="M" />
        </div>
      </div>

      {/* Countdown */}
      <div className="mt-6 grid grid-cols-4 gap-2 md:gap-4 max-w-lg">
        {[
          { label: "DAYS", value: days },
          { label: "HOURS", value: hours },
          { label: "MINS", value: minutes },
          { label: "SECS", value: seconds },
        ].map((u) => (
          <div key={u.label} className="bg-white/5 border border-white/10 p-2 md:p-3 text-center">
            <div className="font-mono font-black text-2xl md:text-4xl tabular-nums" data-testid={`next-trip-${u.label.toLowerCase()}`}>
              {pad(u.value)}
            </div>
            <div className="text-[9px] font-mono text-zinc-400 mt-1">{u.label}</div>
          </div>
        ))}
      </div>

      {/* Trip meta */}
      <div className="mt-6 grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div>
          <div className="te-overline text-[9px] text-zinc-400">Departs</div>
          <div className="font-mono font-bold">{next.departure_date}</div>
          <div className="font-mono text-xs text-zinc-400">{next.departure_time}</div>
        </div>
        <div>
          <div className="te-overline text-[9px] text-zinc-400">Seats</div>
          <div className="font-mono font-bold">{next.seats.join(", ")}</div>
        </div>
        <div>
          <div className="te-overline text-[9px] text-zinc-400">Passengers</div>
          <div className="font-mono font-bold">{next.passengers?.length || next.seats.length}</div>
        </div>
        <div>
          <div className="te-overline text-[9px] text-zinc-400">Operator</div>
          <div className="font-mono font-bold truncate">{next.operator || "Star Qistna"}</div>
        </div>
      </div>

      {/* Actions */}
      <div className="mt-6 flex flex-wrap gap-3">
        <Link
          to={`/bookings/${next.id}`}
          className="inline-flex items-center gap-2 bg-white text-black font-bold px-4 py-2 hover:bg-zinc-200 transition-colors"
          data-testid="next-trip-view-ticket"
        >
          View ticket →
        </Link>
        <button
          type="button"
          onClick={() => downloadIcs(next)}
          className="inline-flex items-center gap-2 border border-white/30 text-white font-bold px-4 py-2 hover:bg-white/10 transition-colors"
          data-testid="next-trip-add-calendar"
        >
          Add to calendar
        </button>
      </div>
    </div>
  );
}
