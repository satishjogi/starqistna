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
    api
      .get("/search", { params: { from_terminal_id: from, to_terminal_id: to, date } })
      .then(({ data }) => setData(data))
      .catch((e) => setError(e?.response?.data?.detail || "Failed to load schedules"))
      .finally(() => setLoading(false));
  }, [from, to, date]);

  const selectSchedule = (sched) => {
    setFlow({
      schedule_id: sched.id,
      schedule: sched,
      from: data.from,
      to: data.to,
      date,
      adults,
      children,
    });
    navigate(`/seats/${sched.id}`);
  };

  return (
    <div className="px-4 md:px-6 lg:px-10 py-10 min-h-[60vh]">
      {/* Breadcrumb header */}
      <div className="mb-8">
        <Link
          to={`/?from=${from || ""}&to=${to || ""}&date=${date || ""}&adults=${adults}&children=${children}`}
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
              <div className="text-xs font-mono text-zinc-500">{data.from.code} · {data.from.name}</div>
            </div>
            <div className="text-3xl text-[#B5121B] font-black">→</div>
            <div>
              <div className="te-overline">To</div>
              <div className="text-2xl font-black">{data.to.city}</div>
              <div className="text-xs font-mono text-zinc-500">{data.to.code} · {data.to.name}</div>
            </div>
            <div className="ml-auto text-right">
              <div className="te-overline">Date / Passengers</div>
              <div className="font-mono text-sm">{date} · {adults}A {children > 0 ? `+ ${children}C` : ""}</div>
            </div>
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
                  const nextDayLink = `/search?from=${from}&to=${to}&date=${tomorrow}&adults=${adults}&children=${children}`;
                  return (
                    <>
                      <div className="font-black text-2xl">No more buses today.</div>
                      <p className="text-sm text-zinc-600 mt-2">
                        It looks like today's departures have already left. Tomorrow's schedule is ready.
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
                        <div>
                          <div className="font-mono text-xl font-black">{s.departure_time}</div>
                          <div className="text-[10px] font-mono text-zinc-500">{data.from.code}</div>
                        </div>
                        <div className="flex-1 border-t border-dashed border-black/30" />
                        <div>
                          <div className="font-mono text-xl font-black">{s.arrival_time}</div>
                          <div className="text-[10px] font-mono text-zinc-500">{data.to.code}</div>
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
              to={`/?from=${from || ""}&to=${to || ""}&date=${date || ""}&adults=${adults}&children=${children}`}
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
