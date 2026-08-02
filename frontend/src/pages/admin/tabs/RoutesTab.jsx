import React, { useEffect, useMemo, useState } from "react";
import api from "../../../lib/api";

// Route = a city-to-city trip template with N pickup stops, M dropoff stops,
// and a pairing matrix of prices. One physical bus runs the trip; customers
// choose which pickup + dropoff pair fits them.
const DEFAULT_FARE = 55;
const CURRENCY_OPTIONS = [
  { c: "myr", label: "MYR (RM)" },
  { c: "sgd", label: "SGD (S$)" },
];

const EMPTY_FORM = {
  code: "", name: "", origin_city: "", destination_city: "",
  direction: "", is_active: true,
  boarding_stops: [],   // [{terminal_id, offset_min}]
  alighting_stops: [],  // [{terminal_id, eta_offset_min}]
  pairings: [],         // [{pickup_id, dropoff_id, adult_fare, child_fare, senior_fare, oku_fare, currency, cts_route_code}]
};

function newPairing(pickup_id, dropoff_id, currency = "myr") {
  return {
    pickup_id, dropoff_id,
    adult_fare: DEFAULT_FARE,
    child_fare: DEFAULT_FARE,
    senior_fare: DEFAULT_FARE,
    oku_fare: DEFAULT_FARE,
    currency,
    cts_route_code: "",
  };
}

export default function RoutesTab() {
  const [routes, setRoutes] = useState([]);
  const [stops, setStops] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [msg, setMsg] = useState("");
  const [dirty, setDirty] = useState(false);

  const load = () => {
    Promise.all([
      api.get("/admin/routes").then(({ data }) => setRoutes(data)),
      api.get("/admin/terminals").then(({ data }) => setStops(data)),
    ]).catch(() => {});
  };

  useEffect(() => { load(); }, []);

  const stopById = useMemo(() => Object.fromEntries(stops.map((s) => [s.id, s])), [stops]);
  const availablePickups = stops.filter((s) => s.is_pickup !== false);
  const availableDropoffs = stops.filter((s) => s.is_dropoff !== false);

  const startNew = () => {
    setSelectedId(null);
    setForm(EMPTY_FORM);
    setMsg("");
    setDirty(false);
  };

  const openRoute = async (r) => {
    setSelectedId(r.id);
    setForm({
      code: r.code || "",
      name: r.name || "",
      origin_city: r.origin_city || "",
      destination_city: r.destination_city || "",
      direction: r.direction || "",
      is_active: r.is_active !== false,
      boarding_stops: r.boarding_stops || [],
      alighting_stops: r.alighting_stops || [],
      pairings: r.pairings || [],
    });
    setMsg("");
    setDirty(false);
  };

  // --- Stop toggles ---
  const togglePickup = (stopId, offsetMin = 0) => {
    const has = form.boarding_stops.find((s) => s.terminal_id === stopId);
    let boarding, pairings = form.pairings;
    if (has) {
      boarding = form.boarding_stops.filter((s) => s.terminal_id !== stopId);
      pairings = pairings.filter((p) => p.pickup_id !== stopId);
    } else {
      boarding = [...form.boarding_stops, { terminal_id: stopId, offset_min: offsetMin }]
        .sort((a, b) => a.offset_min - b.offset_min);
    }
    setForm({ ...form, boarding_stops: boarding, pairings });
    setDirty(true);
  };

  const toggleDropoff = (stopId, offsetMin = 0) => {
    const has = form.alighting_stops.find((s) => s.terminal_id === stopId);
    let alighting, pairings = form.pairings;
    if (has) {
      alighting = form.alighting_stops.filter((s) => s.terminal_id !== stopId);
      pairings = pairings.filter((p) => p.dropoff_id !== stopId);
    } else {
      alighting = [...form.alighting_stops, { terminal_id: stopId, offset_min: offsetMin }]
        .sort((a, b) => a.offset_min - b.offset_min);
    }
    setForm({ ...form, alighting_stops: alighting, pairings });
    setDirty(true);
  };

  const setStopOffset = (which, stopId, minutes) => {
    const key = which === "boarding" ? "boarding_stops" : "alighting_stops";
    const list = form[key].map((s) => (s.terminal_id === stopId ? { ...s, offset_min: Number(minutes) || 0 } : s));
    setForm({ ...form, [key]: list.sort((a, b) => a.offset_min - b.offset_min) });
    setDirty(true);
  };

  // --- Pairing matrix ---
  const pairingKey = (pickup_id, dropoff_id) => `${pickup_id}::${dropoff_id}`;
  const pairingIndex = useMemo(() => {
    const idx = new Map();
    form.pairings.forEach((p, i) => idx.set(pairingKey(p.pickup_id, p.dropoff_id), i));
    return idx;
  }, [form.pairings]);

  const togglePairing = (pickup_id, dropoff_id) => {
    const k = pairingKey(pickup_id, dropoff_id);
    if (pairingIndex.has(k)) {
      setForm({ ...form, pairings: form.pairings.filter((_, i) => i !== pairingIndex.get(k)) });
    } else {
      const currency = stopById[pickup_id]?.country === "SG" ? "sgd" : "myr";
      setForm({ ...form, pairings: [...form.pairings, newPairing(pickup_id, dropoff_id, currency)] });
    }
    setDirty(true);
  };

  const updatePairing = (pickup_id, dropoff_id, patch) => {
    const k = pairingKey(pickup_id, dropoff_id);
    if (!pairingIndex.has(k)) return;
    const idx = pairingIndex.get(k);
    const pairings = [...form.pairings];
    pairings[idx] = { ...pairings[idx], ...patch };
    setForm({ ...form, pairings });
    setDirty(true);
  };

  const fillEmptyCells = () => {
    if (!window.confirm(`Enable ALL empty cells with RM ${DEFAULT_FARE} default fare?`)) return;
    const next = [...form.pairings];
    form.boarding_stops.forEach((b) => {
      form.alighting_stops.forEach((a) => {
        if (!pairingIndex.has(pairingKey(b.terminal_id, a.terminal_id))) {
          const currency = stopById[b.terminal_id]?.country === "SG" ? "sgd" : "myr";
          next.push(newPairing(b.terminal_id, a.terminal_id, currency));
        }
      });
    });
    setForm({ ...form, pairings: next });
    setDirty(true);
  };

  const clearAllPairings = () => {
    if (!window.confirm("Clear ALL pairings? This disables all combinations. Fares are lost.")) return;
    setForm({ ...form, pairings: [] });
    setDirty(true);
  };

  // --- Save / delete ---
  const save = async () => {
    setMsg("");
    const payload = {
      code: form.code.trim().toUpperCase(),
      name: form.name.trim(),
      origin_city: form.origin_city.trim(),
      destination_city: form.destination_city.trim(),
      direction: form.direction.trim() || null,
      is_active: !!form.is_active,
      boarding_stops: form.boarding_stops,
      alighting_stops: form.alighting_stops,
      pairings: form.pairings.map((p) => ({
        ...p,
        cts_route_code: (p.cts_route_code || "").trim() || null,
      })),
    };
    try {
      if (selectedId) {
        const { data } = await api.patch(`/admin/routes/${selectedId}`, payload);
        setMsg("Route updated.");
        setDirty(false);
        setRoutes((prev) => prev.map((r) => (r.id === selectedId ? data : r)));
      } else {
        const { data } = await api.post("/admin/routes", payload);
        setMsg("Route created.");
        setDirty(false);
        setSelectedId(data.id);
        setRoutes((prev) => [...prev, data].sort((a, b) => a.code.localeCompare(b.code)));
      }
    } catch (err) {
      setMsg(err?.response?.data?.detail || "Save failed");
    }
  };

  const remove = async () => {
    if (!selectedId) return;
    if (!window.confirm(`Delete route ${form.code}? This cannot be undone.`)) return;
    try {
      await api.delete(`/admin/routes/${selectedId}`);
      setRoutes((prev) => prev.filter((r) => r.id !== selectedId));
      startNew();
    } catch (err) {
      alert(err?.response?.data?.detail || "Delete failed");
    }
  };

  return (
    <div className="mt-6 grid grid-cols-1 lg:grid-cols-12 gap-6" data-testid="routes-tab">
      {/* Left column — list of routes */}
      <div className="lg:col-span-3">
        <div className="flex items-baseline justify-between mb-2">
          <div className="te-overline">Routes ({routes.length})</div>
          <button
            onClick={startNew}
            className="text-[10px] px-2 py-1 font-bold uppercase bg-[#002FA7] text-white hover:bg-black"
            data-testid="route-new-btn"
          >
            + New
          </button>
        </div>
        <div className="space-y-2 max-h-[720px] overflow-auto pr-1">
          {routes.length === 0 && (
            <div className="te-card p-4 text-xs text-zinc-500 font-mono">No routes yet — click + New to create one.</div>
          )}
          {routes.map((r) => (
            <button
              key={r.id}
              onClick={() => openRoute(r)}
              className={`w-full text-left te-card p-3 hover:bg-zinc-50 transition ${selectedId === r.id ? "ring-2 ring-[#002FA7]" : ""}`}
              data-testid={`route-list-${r.code}`}
            >
              <div className="font-mono text-[10px] font-bold">{r.code} {r.is_active === false && <span className="text-red-600">· INACTIVE</span>}</div>
              <div className="text-sm font-black">{r.name}</div>
              <div className="text-[10px] text-zinc-500">
                {r.origin_city} → {r.destination_city}
              </div>
              <div className="text-[10px] font-mono text-zinc-500 mt-1">
                {(r.boarding_stops || []).length} pickups · {(r.alighting_stops || []).length} dropoffs · {(r.pairings || []).length} pairings
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Right column — editor */}
      <div className="lg:col-span-9 space-y-6">
        <div className="te-card p-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="te-overline">{selectedId ? "Edit route" : "New route"}</div>
              <h2 className="text-2xl font-black">{form.name || "Untitled"}</h2>
            </div>
            <div className="flex items-center gap-2">
              {dirty && <span className="text-[10px] font-mono text-amber-600">● Unsaved changes</span>}
              <label className="flex items-center gap-2 text-xs font-bold uppercase">
                <input
                  type="checkbox"
                  checked={!!form.is_active}
                  onChange={(e) => { setForm({ ...form, is_active: e.target.checked }); setDirty(true); }}
                  data-testid="route-active-toggle"
                />
                Active
              </label>
              <button
                onClick={save}
                className="text-[10px] px-3 py-2 font-bold uppercase bg-[#002FA7] text-white hover:bg-black"
                data-testid="route-save-btn"
              >
                Save
              </button>
              {selectedId && (
                <button
                  onClick={remove}
                  className="text-[10px] px-3 py-2 font-bold uppercase border border-red-200 text-red-600 hover:bg-red-50"
                  data-testid="route-delete-btn"
                >
                  Delete
                </button>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div>
              <label className="te-label">Code</label>
              <input className="te-input font-mono uppercase" value={form.code} onChange={(e) => { setForm({ ...form, code: e.target.value.toUpperCase() }); setDirty(true); }} placeholder="KL-SG" data-testid="route-code" />
            </div>
            <div className="md:col-span-2">
              <label className="te-label">Route name</label>
              <input className="te-input" value={form.name} onChange={(e) => { setForm({ ...form, name: e.target.value }); setDirty(true); }} placeholder="KL → Singapore Overnight" data-testid="route-name" />
            </div>
            <div>
              <label className="te-label">Direction <span className="text-zinc-400">(opt)</span></label>
              <input className="te-input font-mono uppercase" value={form.direction} onChange={(e) => { setForm({ ...form, direction: e.target.value.toUpperCase() }); setDirty(true); }} placeholder="KL-SG" data-testid="route-direction" />
            </div>
            <div>
              <label className="te-label">Origin city</label>
              <input className="te-input" value={form.origin_city} onChange={(e) => { setForm({ ...form, origin_city: e.target.value }); setDirty(true); }} placeholder="Kuala Lumpur" data-testid="route-origin-city" />
            </div>
            <div>
              <label className="te-label">Destination city</label>
              <input className="te-input" value={form.destination_city} onChange={(e) => { setForm({ ...form, destination_city: e.target.value }); setDirty(true); }} placeholder="Singapore" data-testid="route-dest-city" />
            </div>
          </div>

          {msg && <div className="mt-3 text-xs font-bold" data-testid="route-msg">{msg}</div>}
        </div>

        {/* Stops picker */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <StopPicker
            title="Pickup stops"
            testIdPrefix="pickup"
            available={availablePickups}
            selected={form.boarding_stops}
            offsetLabel="min from departure"
            onToggle={togglePickup}
            onOffsetChange={(id, v) => setStopOffset("boarding", id, v)}
            stopById={stopById}
          />
          <StopPicker
            title="Drop-off stops"
            testIdPrefix="dropoff"
            available={availableDropoffs}
            selected={form.alighting_stops}
            offsetLabel="min from departure to arrival"
            onToggle={toggleDropoff}
            onOffsetChange={(id, v) => setStopOffset("alighting", id, v)}
            stopById={stopById}
          />
        </div>

        {/* Pairings matrix */}
        <div className="te-card p-6">
          <div className="flex items-baseline justify-between mb-4">
            <div>
              <div className="te-overline">Pairings matrix</div>
              <div className="text-sm text-zinc-500">Click a cell to enable / disable. Default fare: RM {DEFAULT_FARE}. Click an enabled cell&apos;s pencil to override per-tier fares.</div>
            </div>
            <div className="flex gap-2">
              <button onClick={fillEmptyCells} className="text-[10px] px-2 py-1 font-bold uppercase border border-black/15 hover:bg-zinc-50" data-testid="fill-empty-btn">Fill empty cells</button>
              <button onClick={clearAllPairings} className="text-[10px] px-2 py-1 font-bold uppercase border border-red-200 text-red-600 hover:bg-red-50" data-testid="clear-all-btn">Clear all</button>
            </div>
          </div>

          {form.boarding_stops.length === 0 || form.alighting_stops.length === 0 ? (
            <div className="text-xs font-mono text-zinc-500 py-8 text-center border border-dashed border-zinc-300">
              Add at least one pickup and one drop-off stop above to build the pairings matrix.
            </div>
          ) : (
            <PairingMatrix
              boarding={form.boarding_stops}
              alighting={form.alighting_stops}
              pairings={form.pairings}
              stopById={stopById}
              onToggle={togglePairing}
              onUpdate={updatePairing}
            />
          )}
        </div>
      </div>
    </div>
  );
}


function StopPicker({ title, testIdPrefix, available, selected, offsetLabel, onToggle, onOffsetChange, stopById }) {
  const [filter, setFilter] = useState("");
  const filtered = filter.trim()
    ? available.filter((s) => {
      const q = filter.trim().toLowerCase();
      return s.name?.toLowerCase().includes(q) || s.city?.toLowerCase().includes(q) || s.code?.toLowerCase().includes(q);
    })
    : available;
  return (
    <div className="te-card p-5" data-testid={`${testIdPrefix}-picker`}>
      <div className="flex items-baseline justify-between mb-3">
        <div className="te-overline">{title}</div>
        <div className="text-[10px] font-mono text-zinc-500">{selected.length} selected</div>
      </div>
      <input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter stops…"
        className="te-input !py-1 text-xs mb-2"
        data-testid={`${testIdPrefix}-filter`}
      />

      {selected.length > 0 && (
        <div className="mb-3 space-y-1">
          <div className="text-[10px] font-mono text-zinc-500 uppercase">Selected (sorted by time)</div>
          {selected.map((s) => {
            const stop = stopById[s.terminal_id];
            if (!stop) return null;
            return (
              <div key={s.terminal_id} className="grid grid-cols-12 items-center gap-2 border border-black/10 bg-zinc-50 px-2 py-1.5">
                <div className="col-span-7 text-xs truncate">
                  <span className="font-mono text-[10px] font-bold bg-zinc-900 text-white px-1 mr-1">{stop.code}</span>
                  {stop.name}
                </div>
                <div className="col-span-3">
                  <input
                    className="te-input !py-0.5 font-mono text-xs"
                    type="number"
                    min="0"
                    value={s.offset_min ?? 0}
                    onChange={(e) => onOffsetChange(s.terminal_id, e.target.value)}
                    data-testid={`${testIdPrefix}-offset-${stop.code}`}
                  />
                </div>
                <button
                  onClick={() => onToggle(s.terminal_id)}
                  className="col-span-2 text-[10px] font-bold uppercase text-red-600 hover:underline"
                  data-testid={`${testIdPrefix}-remove-${stop.code}`}
                >
                  remove
                </button>
              </div>
            );
          })}
          <div className="text-[9px] font-mono text-zinc-400 uppercase">{offsetLabel}</div>
        </div>
      )}

      <div className="max-h-[240px] overflow-auto space-y-1 border-t border-black/10 pt-2">
        {filtered
          .filter((s) => !selected.find((x) => x.terminal_id === s.id))
          .map((s) => (
            <button
              key={s.id}
              onClick={() => onToggle(s.id, 0)}
              className="w-full text-left grid grid-cols-12 items-center gap-2 hover:bg-zinc-50 px-2 py-1.5 border border-transparent hover:border-black/10"
              data-testid={`${testIdPrefix}-add-${s.code}`}
            >
              <div className="col-span-2">
                <span className="font-mono text-[10px] font-bold bg-zinc-900 text-white px-1">{s.code}</span>
              </div>
              <div className="col-span-8 text-xs truncate">{s.name} <span className="text-zinc-400">· {s.city}</span></div>
              <div className="col-span-2 text-right text-[10px] font-bold text-emerald-600">+ add</div>
            </button>
          ))}
        {filtered.filter((s) => !selected.find((x) => x.terminal_id === s.id)).length === 0 && (
          <div className="text-[10px] font-mono text-zinc-400 py-2 text-center">No more stops to add.</div>
        )}
      </div>
    </div>
  );
}


function PairingMatrix({ boarding, alighting, pairings, stopById, onToggle, onUpdate }) {
  const [editingCell, setEditingCell] = useState(null);
  const key = (p, d) => `${p}::${d}`;
  const pairingByKey = useMemo(() => {
    const m = new Map();
    pairings.forEach((p) => m.set(key(p.pickup_id, p.dropoff_id), p));
    return m;
  }, [pairings]);

  return (
    <div className="overflow-x-auto" data-testid="pairing-matrix">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr>
            <th className="p-2 border border-black/10 bg-zinc-50 text-left" />
            {alighting.map((a) => {
              const s = stopById[a.terminal_id];
              if (!s) return null;
              return (
                <th key={a.terminal_id} className="p-2 border border-black/10 bg-zinc-50 min-w-[100px] text-center">
                  <div className="font-mono text-[10px] font-bold bg-zinc-900 text-white px-1 inline-block">{s.code}</div>
                  <div className="text-[10px] font-black mt-1 leading-tight">{s.name}</div>
                  <div className="text-[9px] font-mono text-zinc-500">+{a.offset_min}m</div>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {boarding.map((b) => {
            const bs = stopById[b.terminal_id];
            if (!bs) return null;
            return (
              <tr key={b.terminal_id}>
                <th className="p-2 border border-black/10 bg-zinc-50 text-left min-w-[140px]">
                  <div className="font-mono text-[10px] font-bold bg-zinc-900 text-white px-1 inline-block">{bs.code}</div>
                  <div className="text-[10px] font-black mt-1 leading-tight">{bs.name}</div>
                  <div className="text-[9px] font-mono text-zinc-500">+{b.offset_min}m</div>
                </th>
                {alighting.map((a) => {
                  const k = key(b.terminal_id, a.terminal_id);
                  const p = pairingByKey.get(k);
                  const enabled = !!p;
                  const editing = editingCell === k;
                  const currencyLabel = p?.currency === "sgd" ? "S$" : "RM";
                  return (
                    <td key={a.terminal_id} className={`p-1 border border-black/10 text-center relative ${enabled ? "bg-emerald-50" : "bg-white"}`} data-testid={`pair-${bs.code}-${stopById[a.terminal_id].code}`}>
                      {editing && p ? (
                        <div className="p-2 bg-white shadow-xl border border-black/20 space-y-1 z-10 relative">
                          {["adult_fare", "child_fare", "senior_fare", "oku_fare"].map((k) => (
                            <div key={k} className="flex items-center gap-1">
                              <label className="text-[9px] font-mono font-bold uppercase w-10 text-left">{k.split("_")[0]}</label>
                              <input
                                type="number"
                                min="0"
                                step="0.5"
                                className="te-input !py-0.5 font-mono text-xs w-full"
                                value={p[k]}
                                onChange={(e) => onUpdate(b.terminal_id, a.terminal_id, { [k]: Number(e.target.value) || 0 })}
                              />
                            </div>
                          ))}
                          <div className="flex items-center gap-1">
                            <label className="text-[9px] font-mono font-bold uppercase w-10 text-left">ccy</label>
                            <select
                              value={p.currency}
                              onChange={(e) => onUpdate(b.terminal_id, a.terminal_id, { currency: e.target.value })}
                              className="te-input !py-0.5 text-xs w-full font-mono"
                            >
                              {CURRENCY_OPTIONS.map((o) => (
                                <option key={o.c} value={o.c}>{o.label}</option>
                              ))}
                            </select>
                          </div>
                          <div className="flex items-center gap-1">
                            <label className="text-[9px] font-mono font-bold uppercase w-10 text-left">cts</label>
                            <input
                              className="te-input !py-0.5 font-mono text-xs w-full"
                              placeholder="TBS-BUG"
                              value={p.cts_route_code || ""}
                              onChange={(e) => onUpdate(b.terminal_id, a.terminal_id, { cts_route_code: e.target.value.toUpperCase() })}
                            />
                          </div>
                          <button onClick={() => setEditingCell(null)} className="w-full text-[9px] font-bold uppercase bg-[#002FA7] text-white px-2 py-1">done</button>
                        </div>
                      ) : enabled ? (
                        <button
                          onClick={() => setEditingCell(k)}
                          className="w-full font-mono text-[11px] font-bold hover:bg-emerald-100 py-1.5"
                          title="Click to edit fares"
                        >
                          {currencyLabel} {p.adult_fare}
                          <div className="text-[8px] font-mono text-zinc-500">
                            C:{p.child_fare} S:{p.senior_fare} O:{p.oku_fare}
                          </div>
                        </button>
                      ) : (
                        <button
                          onClick={() => onToggle(b.terminal_id, a.terminal_id)}
                          className="w-full text-zinc-300 hover:text-emerald-600 hover:bg-emerald-50 py-1.5 text-lg font-bold"
                          title="Click to enable"
                        >
                          +
                        </button>
                      )}
                      {enabled && !editing && (
                        <button
                          onClick={() => onToggle(b.terminal_id, a.terminal_id)}
                          className="absolute top-0.5 right-0.5 text-[9px] font-bold text-red-600 hover:bg-red-50 px-1"
                          title="Disable this pairing"
                        >
                          ✕
                        </button>
                      )}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
