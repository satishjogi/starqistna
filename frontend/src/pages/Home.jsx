import React, { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import PopularNow from "../components/PopularNow";

const HERO_BG = "https://images.unsplash.com/photo-1544620347-1959828a2a7d?q=80&w=2000&auto=format&fit=crop";

function TerminalSelect({ label, value, onChange, testId, exclude }) {
  const [open, setOpen] = useState(false);
  const [grouped, setGrouped] = useState([]);
  const [q, setQ] = useState("");

  useEffect(() => {
    api.get("/terminals").then(({ data }) => setGrouped(data.grouped)).catch(() => {});
  }, []);

  const filtered = useMemo(() => {
    if (!q) return grouped;
    const lower = q.toLowerCase();
    return grouped
      .map((g) => ({
        ...g,
        // City itself matches → keep the group so the "Any stop in {city}" pseudo-option
        // stays reachable when the user is typing a city name.
        cityMatch: g.city.toLowerCase().includes(lower),
        terminals: g.terminals.filter(
          (t) =>
            t.name.toLowerCase().includes(lower) ||
            t.city.toLowerCase().includes(lower) ||
            t.code.toLowerCase().includes(lower)
        ),
      }))
      .filter((g) => g.terminals.length > 0 || g.cityMatch);
  }, [q, grouped]);

  const pickCity = (city, terminals) => {
    // Store the city as a virtual "selection" — no terminal id, is_city=true.
    onChange({ id: null, is_city: true, city, name: `Any stop · ${city}`, code: city.split(" ").map((w) => w[0]).join("").toUpperCase(), stop_count: terminals.length });
    setOpen(false);
    setQ("");
  };

  return (
    <div className="relative">
      <label className="te-label">{label}</label>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left px-4 py-4 bg-white border border-black/15 hover:border-[#002FA7] transition-colors rounded-sm"
        data-testid={testId}
      >
        {value ? (
          <div>
            <div className="font-bold flex items-center gap-2">
              {value.is_city && <span aria-hidden="true">🏙</span>}
              {value.city}
            </div>
            <div className="text-xs text-zinc-500 font-mono">
              {value.is_city ? `Any stop · ${value.stop_count ?? ""} option${value.stop_count === 1 ? "" : "s"}` : `${value.code} · ${value.name}`}
            </div>
          </div>
        ) : (
          <div className="text-zinc-400">Select terminal or city</div>
        )}
      </button>
      {open && (
        <div className="absolute z-[60] top-[calc(100%+4px)] left-0 right-0 bg-white border border-black/15 shadow-xl rounded-sm max-h-96 overflow-auto">
          <div className="p-3 border-b border-black/10 sticky top-0 bg-white">
            <input
              className="te-input"
              placeholder="Search city or terminal…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              data-testid={`${testId}-search`}
              autoFocus
            />
          </div>
          {filtered.map((g) => {
            const cityDisabled = exclude?.is_city && exclude.city === g.city;
            return (
              <div key={g.city}>
                <button
                  type="button"
                  disabled={cityDisabled}
                  onClick={() => pickCity(g.city, g.terminals)}
                  className={`w-full text-left px-4 pt-3 pb-2 hover:bg-emerald-50 border-b border-black/5 ${cityDisabled ? "opacity-40 cursor-not-allowed" : ""}`}
                  data-testid={`${testId}-city-${g.city.replace(/\s+/g, "-").toLowerCase()}`}
                >
                  <div className="te-overline text-[10px]">🏙 Any stop · {g.city}</div>
                  <div className="text-[10px] font-mono text-zinc-500 mt-0.5">
                    {g.terminals.length} pickup option{g.terminals.length === 1 ? "" : "s"} available
                  </div>
                </button>
                {g.terminals.map((t) => {
                  const disabled = exclude && !exclude.is_city && exclude.id === t.id;
                  return (
                    <button
                      key={t.id}
                      type="button"
                      disabled={disabled}
                      onClick={() => { onChange(t); setOpen(false); setQ(""); }}
                      className={`w-full text-left px-4 py-3 hover:bg-zinc-50 border-b border-black/5 flex items-center justify-between ${disabled ? "opacity-40 cursor-not-allowed" : ""}`}
                      data-testid={`${testId}-opt-${t.code}`}
                    >
                      <div>
                        <div className="text-sm font-semibold">{t.name}</div>
                        <div className="text-[10px] font-mono text-zinc-500 tracking-wider">{t.code} · {t.state}</div>
                      </div>
                    </button>
                  );
                })}
              </div>
            );
          })}
          {filtered.length === 0 && <div className="p-6 text-sm text-zinc-500 text-center">No terminals or cities found</div>}
        </div>
      )}
    </div>
  );
}

export default function Home() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [from, setFrom] = useState(null);
  const [to, setTo] = useState(null);
  const today = new Date().toISOString().slice(0, 10);
  const [date, setDate] = useState(params.get("date") || today);
  const [adults, setAdults] = useState(parseInt(params.get("adults") || "1"));
  const [children, setChildren] = useState(parseInt(params.get("children") || "0"));
  const [error, setError] = useState("");

  // Prefill from/to when URL params present (terminal id OR city name).
  useEffect(() => {
    const fromId = params.get("from");
    const toId = params.get("to");
    const fromCity = params.get("from_city");
    const toCity = params.get("to_city");
    if (!fromId && !toId && !fromCity && !toCity) return;
    api.get("/terminals").then(({ data }) => {
      if (fromId) {
        const f = data.all.find((t) => t.id === fromId);
        if (f) setFrom(f);
      } else if (fromCity) {
        const stops = data.all.filter((t) => t.city === fromCity);
        setFrom({ id: null, is_city: true, city: fromCity, name: `Any stop · ${fromCity}`, code: fromCity.slice(0, 3).toUpperCase(), stop_count: stops.length });
      }
      if (toId) {
        const t = data.all.find((x) => x.id === toId);
        if (t) setTo(t);
      } else if (toCity) {
        const stops = data.all.filter((t) => t.city === toCity);
        setTo({ id: null, is_city: true, city: toCity, name: `Any stop · ${toCity}`, code: toCity.slice(0, 3).toUpperCase(), stop_count: stops.length });
      }
    }).catch(() => {});
  }, []);

  const submit = (e) => {
    e.preventDefault();
    setError("");
    if (!from || !to) return setError("Please select both departure and arrival.");
    if (!from.is_city && !to.is_city && from.id === to.id) return setError("Departure and arrival cannot be the same.");
    if (from.is_city && to.is_city && from.city === to.city) return setError("Departure and arrival cities cannot be the same.");
    if (adults + children < 1) return setError("At least one passenger is required.");
    const q = new URLSearchParams({
      date,
      adults: String(adults),
      children: String(children),
    });
    if (from.is_city) q.set("from_city", from.city);
    else q.set("from", from.id);
    if (to.is_city) q.set("to_city", to.city);
    else q.set("to", to.id);
    navigate(`/search?${q.toString()}`);
  };

  return (
    <div>
      {/* Hero */}
      <section className="relative border-b border-black/10">
        <div className="absolute inset-0 -z-10 hero-grid overflow-hidden" />
        <div className="absolute inset-0 -z-20 overflow-hidden">
          <img src={HERO_BG} alt="" className="w-full h-full object-cover opacity-10" />
        </div>
        <div className="px-4 md:px-6 lg:px-10 pt-12 pb-8">
          <div className="grid grid-cols-12 gap-4 items-end">
            <div className="col-span-12 lg:col-span-7">
              <div className="te-overline mb-4" data-testid="hero-overline">Express · Inter-city Buses</div>
              <h1 className="text-5xl sm:text-6xl lg:text-7xl font-black tracking-tighter leading-[0.9]">
                Arrive rested.<br/>
                <span className="text-[#B5121B]">First class coach.</span>
              </h1>
              <p className="mt-6 text-lg text-zinc-600 max-w-xl">
                Star Qistna runs premium massage-coach service across Malaysia & Singapore. Real seat locks. No overbooking. Pay by Card, GrabPay, or FPX.
              </p>
            </div>
            <div className="col-span-12 lg:col-span-5 hidden lg:flex items-end justify-end">
              <div className="font-mono text-[10px] text-zinc-500 tracking-wider text-right">
                <div className="mb-1">ACTIVE TERMINALS · 14</div>
                <div className="mb-1">DAILY SCHEDULES · 500+</div>
                <div>DOUBLE-BOOK PROTECTED · YES</div>
              </div>
            </div>
          </div>

          {/* Search form — dense control-board style */}
          <form onSubmit={submit} className="mt-12 bg-black p-[2px] rounded-sm" data-testid="search-form">
            <div className="grid grid-cols-12 gap-[2px]">
              <div className="col-span-12 md:col-span-4 bg-white p-5">
                <TerminalSelect label="From" value={from} onChange={setFrom} testId="from-terminal" exclude={to} />
              </div>
              <div className="col-span-12 md:col-span-4 bg-white p-5">
                <TerminalSelect label="To" value={to} onChange={setTo} testId="to-terminal" exclude={from} />
              </div>
              <div className="col-span-6 md:col-span-2 bg-white p-5">
                <label className="te-label">Date</label>
                <input
                  type="date"
                  className="te-input"
                  value={date}
                  min={today}
                  onChange={(e) => setDate(e.target.value)}
                  data-testid="search-date"
                />
              </div>
              <div className="col-span-6 md:col-span-2 bg-white p-5">
                <label className="te-label">Passengers</label>
                <div className="flex flex-col md:flex-row md:items-center gap-3 md:gap-2">
                  <div className="flex-1">
                    <div className="text-[9px] font-mono text-zinc-500">ADULT</div>
                    <div className="flex items-center justify-center gap-1 mt-1">
                      <button type="button" onClick={() => setAdults(Math.max(1, adults - 1))} className="w-7 h-7 border border-black/20 rounded-sm font-bold" data-testid="adult-minus">−</button>
                      <div className="w-6 text-center font-mono font-bold" data-testid="adult-count">{adults}</div>
                      <button type="button" onClick={() => setAdults(Math.min(10, adults + 1))} className="w-7 h-7 border border-black/20 rounded-sm font-bold" data-testid="adult-plus">+</button>
                    </div>
                  </div>
                  <div className="flex-1">
                    <div className="text-[9px] font-mono text-zinc-500">CHILD</div>
                    <div className="flex items-center justify-center gap-1 mt-1">
                      <button type="button" onClick={() => setChildren(Math.max(0, children - 1))} className="w-7 h-7 border border-black/20 rounded-sm font-bold" data-testid="child-minus">−</button>
                      <div className="w-6 text-center font-mono font-bold" data-testid="child-count">{children}</div>
                      <button type="button" onClick={() => setChildren(Math.min(10, children + 1))} className="w-7 h-7 border border-black/20 rounded-sm font-bold" data-testid="child-plus">+</button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div className="bg-white p-4 flex items-center justify-between flex-wrap gap-3">
              <div className="text-xs font-mono text-zinc-500">CONFIRMED SEAT · TRANSPARENT FARE · NO HIDDEN ADD-ONS</div>
              {error && <div className="text-xs font-bold text-red-600" data-testid="search-error">{error}</div>}
              <button type="submit" className="te-btn-accent" data-testid="search-submit-btn">
                Search Buses →
              </button>
            </div>
          </form>
        </div>
      </section>

      {/* Popular right now - live, time-aware suggestions just below search */}
      <PopularNow />

      {/* Popular routes - clickable, right under search */}
      <PopularRoutes />

      {/* Features */}
      <section className="px-4 md:px-6 lg:px-10 py-20">
        <div className="te-overline mb-3">How it works</div>
        <h2 className="text-3xl md:text-5xl font-black tracking-tight max-w-2xl">Three clicks to a confirmed seat.</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-[1px] mt-12 bg-black/10 border border-black/10">
          {[
            { n: "01", t: "Search", d: "Pick your departure terminal (with sub-terminals), destination and date." },
            { n: "02", t: "Select seats", d: "Our seat map locks your selection for 10 minutes. No double-booking possible." },
            { n: "03", t: "Pay securely", d: "Pay by card via Stripe. Tickets delivered instantly with reference code." },
          ].map((s) => (
            <div key={s.n} className="bg-white p-8">
              <div className="font-mono text-xs text-[#B5121B] tracking-wider">{s.n}</div>
              <h3 className="text-2xl font-black mt-4">{s.t}</h3>
              <p className="text-sm text-zinc-600 mt-3">{s.d}</p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

// ----- Popular Routes (clickable) -----
const POPULAR_ROUTES = [
  { from: "Kuala Lumpur", to: "Melaka",        tag: "GO TODAY",      line: "90-min weekend escape",       price: 25, size: "lg", accent: "#B5121B" },
  { from: "Kuala Lumpur", to: "Johor Bahru",   tag: "HOT SELLING",   line: "South-bound workhorse",       price: 45, size: "md", accent: "#002FA7" },
  { from: "Kuala Lumpur", to: "Singapore",     tag: "CROSS-BORDER",  line: "Beat the causeway crawl",     price: 55, size: "md", accent: "#002FA7" },
  { from: "Kuala Lumpur", to: "Penang",        tag: "FOODIE FAVE",   line: "Char kuey teow calls",         price: 49, size: "md", accent: "#B5121B" },
  { from: "Kuala Lumpur", to: "Ipoh",          tag: "QUICK ESCAPE",  line: "White coffee country",         price: 35, size: "sm", accent: "#002FA7" },
  { from: "Singapore",    to: "Kuala Lumpur",  tag: "TOP RETURN",    line: "Back to the capital",          price: 55, size: "sm", accent: "#002FA7" },
  { from: "Johor Bahru",  to: "Kuala Lumpur",  tag: "COMMUTER",      line: "Monday morning rush",          price: 45, size: "sm", accent: "#B5121B" },
  { from: "Penang",       to: "Kuala Lumpur",  tag: "NORTHBOUND",    line: "Island to city",               price: 49, size: "sm", accent: "#002FA7" },
  { from: "Kuala Lumpur", to: "Kuantan",       tag: "EAST COAST",    line: "Beach weekend bound",          price: 52, size: "sm", accent: "#B5121B" },
];

function PopularRoutes() {
  const navigate = useNavigate();
  const [cityMap, setCityMap] = useState({});
  // Use Malaysia/Singapore local date (UTC+8) — schedule departure_time is stored as local.
  const today = new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);

  useEffect(() => {
    api.get("/terminals").then(({ data }) => {
      const map = {};
      data.all.forEach((t) => { if (!map[t.city]) map[t.city] = t; });
      setCityMap(map);
    }).catch(() => {});
  }, []);

  const go = (r) => {
    const f = cityMap[r.from];
    const t = cityMap[r.to];
    if (!f || !t) return;
    const p = new URLSearchParams({ from: f.id, to: t.id, date: today, adults: "1", children: "0" });
    navigate(`/search?${p.toString()}`);
  };

  return (
    <section className="px-4 md:px-6 lg:px-10 py-16 border-t border-black/10" data-testid="popular-routes-section">
      <div className="flex flex-wrap items-end justify-between gap-3 mb-8">
        <div>
          <div className="te-overline mb-2">Popular routes</div>
          <h2 className="text-3xl md:text-5xl font-black tracking-tight">
            Where everyone&apos;s going <span className="text-[#B5121B]">today.</span>
          </h2>
        </div>
        <div className="font-mono text-[10px] text-zinc-500 tracking-wider">
          TAP A ROUTE · SEARCHES FOR {today}
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-4 gap-[1px] bg-black/10 border border-black/10">
        {POPULAR_ROUTES.map((r, i) => {
          const rowSpan = r.size === "lg" ? "md:row-span-2 md:col-span-2" : "";
          const big = r.size === "lg";
          return (
            <button
              key={i}
              type="button"
              onClick={() => go(r)}
              className={`group relative bg-white p-6 text-left hover:bg-zinc-50 transition-all duration-200 overflow-hidden ${rowSpan}`}
              data-testid={`popular-route-${r.from.replace(/\s/g, "")}-${r.to.replace(/\s/g, "")}`}
            >
              {/* diagonal colour bar */}
              <div
                className="absolute top-0 right-0 w-[6px] h-full transition-all duration-300 group-hover:w-[10px]"
                style={{ backgroundColor: r.accent }}
              />
              <div className="flex items-start justify-between">
                <span
                  className="text-[9px] font-mono font-bold tracking-[0.2em] px-2 py-1 uppercase"
                  style={{ backgroundColor: r.accent, color: "white" }}
                >
                  {r.tag}
                </span>
                <span className="font-mono text-[10px] text-zinc-400">{String(i + 1).padStart(2, "0")}</span>
              </div>

              <div className={`mt-${big ? "10" : "6"}`}>
                <div className={`font-black tracking-tighter leading-[0.95] ${big ? "text-4xl md:text-5xl" : "text-xl md:text-2xl"}`}>
                  {r.from}
                </div>
                <div
                  className={`inline-block my-2 font-mono font-black ${big ? "text-3xl" : "text-xl"}`}
                  style={{ color: r.accent }}
                >
                  ↓
                </div>
                <div className={`font-black tracking-tighter leading-[0.95] ${big ? "text-4xl md:text-5xl" : "text-xl md:text-2xl"}`}>
                  {r.to}
                </div>
              </div>

              <div className={`mt-${big ? "8" : "5"} pt-4 border-t border-dashed border-black/15 flex items-end justify-between`}>
                <div className="flex-1 pr-2">
                  <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">{r.line}</div>
                  <div className={`font-mono font-black mt-1 ${big ? "text-2xl" : "text-base"}`}>
                    FROM RM {r.price}
                  </div>
                </div>
                <div
                  className="w-9 h-9 flex items-center justify-center transition-transform duration-200 group-hover:translate-x-1 font-black"
                  style={{ backgroundColor: r.accent, color: "white" }}
                  aria-hidden
                >
                  →
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
