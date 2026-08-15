import React, { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { setFlow } from "../lib/booking-store";
import { formatPriceWithMyr } from "../lib/price";

export default function SearchResults() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const from = params.get("from");
  const to = params.get("to");
  const fromCity = params.get("from_city");
  const toCity = params.get("to_city");
  const date = params.get("date");
  const adults = parseInt(params.get("adults") || "1");
  const children = parseInt(params.get("children") || "0");

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [fxRate, setFxRate] = useState(null);

  useEffect(() => {
    api.get("/settings").then(({ data }) => setFxRate(data.sgd_to_myr_rate)).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    const searchParams = { date };
    if (from) searchParams.from_terminal_id = from;
    else if (fromCity) searchParams.from_city = fromCity;
    if (to) searchParams.to_terminal_id = to;
    else if (toCity) searchParams.to_city = toCity;
    api
      .get("/search", { params: searchParams })
      .then(({ data }) => setData(data))
      .catch((e) => setError(e?.response?.data?.detail || "Failed to load schedules"))
      .finally(() => setLoading(false));
  }, [from, to, fromCity, toCity, date]);

  const selectSchedule = (sched) => {
    // Pass the SPECIFIC pickup + drop-off (not the city aggregate) so downstream
    // pages know which stop the passenger is actually boarding at.
    setFlow({
      schedule_id: sched.id,
      schedule: sched,
      from: sched.from_terminal || data.from,
      to: sched.to_terminal || data.to,
      date,
      adults,
      children,
    });
    navigate(`/seats/${sched.id}`);
  };

  // City-level search? Then multiple stops per side are on offer and each schedule
  // may leave from / arrive at a different terminal — surface that explicitly.
  const isCitySearch = !!(data?.from?.is_city || data?.to?.is_city);

  // Build a URL back to Home that preserves the current search mode (stop vs city).
  const homeBackUrl = (() => {
    const q = new URLSearchParams({ date: date || "", adults: String(adults), children: String(children) });
    if (from) q.set("from", from);
    else if (fromCity) q.set("from_city", fromCity);
    if (to) q.set("to", to);
    else if (toCity) q.set("to_city", toCity);
    return `/?${q.toString()}`;
  })();

  return (
    <div className="px-4 md:px-6 lg:px-10 py-10 min-h-[60vh]">
      {/* Breadcrumb header */}
      <div className="mb-8">
        <Link
          to={homeBackUrl}
          className="text-xs font-mono text-zinc-500 hover:text-black"
          data-testid="back-home"
        >
          ← MODIFY SEARCH
        </Link>
        {data?.from && data?.to && (
          <div className="mt-4 flex flex-wrap items-end gap-6">
            <div>
              <div className="te-overline">From</div>
              <div className="text-2xl font-black">{data.from.city}</div>
              <div className="text-xs font-mono text-zinc-500">
                {data.from.is_city
                  ? `${data.from.stop_count} pickup stop${data.from.stop_count === 1 ? "" : "s"}`
                  : `${data.from.code} · ${data.from.name}`}
              </div>
            </div>
            <div className="text-3xl text-[#B5121B] font-black">→</div>
            <div>
              <div className="te-overline">To</div>
              <div className="text-2xl font-black">{data.to.city}</div>
              <div className="text-xs font-mono text-zinc-500">
                {data.to.is_city
                  ? `${data.to.stop_count} drop-off point${data.to.stop_count === 1 ? "" : "s"}`
                  : `${data.to.code} · ${data.to.name}`}
              </div>
            </div>
            <div className="ml-auto text-right">
              <div className="te-overline">Date / Passengers</div>
              <div className="font-mono text-sm">{date} · {adults}A {children > 0 ? `+ ${children}C` : ""}</div>
            </div>
          </div>
        )}
        {isCitySearch && (
          <div className="mt-3 text-[11px] font-mono text-zinc-600 border-l-2 border-[#002FA7] pl-3 py-1" data-testid="city-search-hint">
            City-level search — each trip below shows the specific pickup + drop-off stop.
          </div>
        )}
      </div>

      {loading && <div className="text-sm text-zinc-500 font-mono" data-testid="search-loading">LOADING SCHEDULES…</div>}
      {error && <div className="text-sm text-red-600 font-bold" data-testid="search-error">{error}</div>}

      {data && !loading && (
        <div>
          <div className="te-overline mb-3">{data.schedules.length} Departures available</div>
          {data.schedules.length === 0 ? (
            <div className="te-card p-10 text-center" data-testid="search-empty">
              {(() => {
                // Did we land here because we filtered out today's past-time buses?
                const todayLocal = new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
                const isToday = date === todayLocal;
                const tomorrow = (() => {
                  const d = new Date(Date.now() + 8 * 60 * 60 * 1000 + 24 * 60 * 60 * 1000);
                  return d.toISOString().slice(0, 10);
                })();
                if (isToday) {
                  const dayParams = new URLSearchParams({ date: tomorrow, adults: String(adults), children: String(children) });
                  if (from) dayParams.set("from", from);
                  else if (fromCity) dayParams.set("from_city", fromCity);
                  if (to) dayParams.set("to", to);
                  else if (toCity) dayParams.set("to_city", toCity);
                  const nextDayLink = `/search?${dayParams.toString()}`;
                  return (
                    <>
                      <div className="font-black text-2xl">No more buses today.</div>
                      <p className="text-sm text-zinc-600 mt-2">
                        It looks like today&apos;s departures have already left. Tomorrow&apos;s schedule is ready.
                      </p>
                      <Link to={nextDayLink} className="te-btn-primary inline-flex mt-5" data-testid="try-tomorrow-btn">
                        See buses tomorrow ({tomorrow}) →
                      </Link>
                    </>
                  );
                }
                return (
                  <>
                    <div className="font-black text-2xl">No buses for this date.</div>
                    <p className="text-sm text-zinc-600 mt-2">Try a different date or nearby terminal.</p>
                  </>
                );
              })()}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3">
              {data.schedules.map((s) => {
                const soldOut = s.seats_available <= 0;
                const pickup = s.from_terminal || data.from;
                const dropoff = s.to_terminal || data.to;
                return (
                  <div
                    key={s.id}
                    className="te-card p-5 md:p-6 grid grid-cols-12 gap-4 items-center hover:border-black/40 transition"
                    data-testid={`schedule-row-${s.id}`}
                  >
                    <div className="col-span-12 md:col-span-3">
                      <div className="te-overline text-[10px]">Operator</div>
                      <div className="font-black">{s.bus_operator}</div>
                      <div className="text-[10px] font-mono text-zinc-500 mt-1">{s.bus_type}</div>
                    </div>
                    <div className="col-span-6 md:col-span-4">
                      <div className="flex items-center gap-3">
                        <div className="min-w-0">
                          <div className="font-mono text-xl font-black">{s.departure_time}</div>
                          <div className="text-[10px] font-mono text-zinc-500">{pickup?.code || data.from.code || ""}</div>
                        </div>
                        <div className="flex-1 border-t border-dashed border-black/30" />
                        <div className="min-w-0">
                          <div className="font-mono text-xl font-black">{s.arrival_time}</div>
                          <div className="text-[10px] font-mono text-zinc-500">{dropoff?.code || data.to.code || ""}</div>
                        </div>
                      </div>
                    </div>
                    <div className="col-span-6 md:col-span-2">
                      <div className="te-overline text-[10px]">Seats</div>
                      <div className={`font-mono font-bold ${s.seats_available < 5 ? "text-[#B5121B]" : ""}`}>
                        {s.seats_available} / {s.total_seats}
                      </div>
                    </div>
                    <div className="col-span-12 md:col-span-3 flex items-center justify-between md:justify-end gap-4">
                      <div className="text-right">
                        <div className="text-[10px] font-mono text-zinc-500">FROM</div>
                        <div className="text-2xl font-black font-mono">{formatPriceWithMyr(s.adult_fare, s.currency, fxRate)}</div>
                        {s.child_fare != null && (
                          <div className="text-[10px] font-mono text-zinc-500 mt-1">
                            Child {formatPriceWithMyr(s.child_fare, s.currency, fxRate)}
                          </div>
                        )}
                      </div>
                      <button
                        disabled={soldOut}
                        onClick={() => selectSchedule(s)}
                        className="te-btn-primary disabled:opacity-40"
                        data-testid={`select-schedule-${s.id}`}
                      >
                        {soldOut ? "Sold out" : "Select"}
                      </button>
                    </div>
                    {/* Full-width stop details — always visible so pax knows where to board & alight */}
                    {pickup && dropoff && (
                      <div className="col-span-12 grid grid-cols-1 md:grid-cols-2 gap-3 pt-3 mt-1 border-t border-dashed border-black/10" data-testid={`stops-detail-${s.id}`}>
                        <div className="flex items-start gap-2">
                          <span className="text-[10px] font-mono uppercase tracking-wider text-emerald-700 border border-emerald-700 px-1.5 py-0.5 mt-0.5 flex-shrink-0">Pickup</span>
                          <div className="min-w-0">
                            <div className="font-bold text-sm truncate">{pickup.name}</div>
                            <div className="text-[11px] font-mono text-zinc-500 truncate">
                              {pickup.code ? `${pickup.code} · ` : ""}{pickup.city}
                              {pickup.landmark_address ? ` · ${pickup.landmark_address}` : ""}
                            </div>
                          </div>
                        </div>
                        <div className="flex items-start gap-2">
                          <span className="text-[10px] font-mono uppercase tracking-wider text-[#B5121B] border border-[#B5121B] px-1.5 py-0.5 mt-0.5 flex-shrink-0">Drop-off</span>
                          <div className="min-w-0">
                            <div className="font-bold text-sm truncate">{dropoff.name}</div>
                            <div className="text-[11px] font-mono text-zinc-500 truncate">
                              {dropoff.code ? `${dropoff.code} · ` : ""}{dropoff.city}
                              {dropoff.landmark_address ? ` · ${dropoff.landmark_address}` : ""}
                            </div>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Bottom back-to-search link */}
          <div className="mt-10 pt-6 border-t border-dashed border-black/15 flex items-center justify-between flex-wrap gap-3">
            <div className="te-overline text-[10px]">
              End of {data.schedules.length} result{data.schedules.length === 1 ? "" : "s"}
            </div>
            <Link
              to={homeBackUrl}
              className="te-btn-outline flex items-center gap-2"
              data-testid="back-to-search-bottom"
            >
              <span className="font-mono text-xs">←</span> Back to Search
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
