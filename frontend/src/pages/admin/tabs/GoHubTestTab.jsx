import React, { useState } from "react";
import api from "../../../lib/api";

// Runs a live reserve → confirm → query against the TBS test endpoint and
// renders the real CTS QR + ticket details returned. Requires GOHUB_ENABLED=true
// on the backend AND the calling server IP to be TBS-whitelisted.
const DEFAULT_FORM = {
  trip_no: "SQ001",
  boarding_date: "",   // YYYYMMDD; blank = today MY
  boarding_time: "2330",
  seat: "1A",
  seat_type: "A",
  from_counter: "TBS01",
  to_counter: "GMC01",
  ic_no: "",
  contact: "",
  confirm: true,
  cancel: false,
};

export default function GoHubTestTab() {
  const [form, setForm] = useState(DEFAULT_FORM);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const toggle = (k) => () => setForm({ ...form, [k]: !form[k] });

  const run = async () => {
    setRunning(true);
    setError("");
    setResult(null);
    try {
      const { data } = await api.post("/admin/gohub/probe", form);
      setResult(data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || "Probe failed");
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="mt-6 grid grid-cols-1 lg:grid-cols-12 gap-6" data-testid="gohub-test-tab">
      {/* Left: form */}
      <div className="lg:col-span-4">
        <div className="te-card p-6 space-y-3">
          <div className="te-overline">TBS Test Probe</div>
          <p className="text-xs text-zinc-500">Hits <span className="font-mono">GOHUB_BASE_URL</span> with a live reserve + optional confirm. Backend must have <span className="font-mono">GOHUB_ENABLED=true</span> and the server IP must be TBS-whitelisted.</p>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="te-label">Trip No</label>
              <input className="te-input font-mono" value={form.trip_no} onChange={set("trip_no")} data-testid="gh-trip" />
            </div>
            <div>
              <label className="te-label">Boarding Date <span className="text-zinc-400">(blank=today)</span></label>
              <input className="te-input font-mono" placeholder="YYYYMMDD" value={form.boarding_date} onChange={set("boarding_date")} data-testid="gh-date" />
            </div>
            <div>
              <label className="te-label">Boarding Time</label>
              <input className="te-input font-mono" placeholder="HHmm" value={form.boarding_time} onChange={set("boarding_time")} data-testid="gh-time" />
            </div>
            <div>
              <label className="te-label">Seat</label>
              <input className="te-input font-mono" value={form.seat} onChange={set("seat")} data-testid="gh-seat" />
            </div>
            <div>
              <label className="te-label">Seat Type</label>
              <select className="te-input" value={form.seat_type} onChange={set("seat_type")} data-testid="gh-seat-type">
                <option value="A">A · Adult</option>
                <option value="C">C · Child</option>
                <option value="S">S · Senior</option>
                <option value="O">O · OKU</option>
              </select>
            </div>
            <div />
            <div>
              <label className="te-label">From Counter</label>
              <input className="te-input font-mono uppercase" value={form.from_counter} onChange={set("from_counter")} data-testid="gh-from" />
            </div>
            <div>
              <label className="te-label">To Counter</label>
              <input className="te-input font-mono uppercase" value={form.to_counter} onChange={set("to_counter")} data-testid="gh-to" />
            </div>
            <div>
              <label className="te-label">IC / Passport</label>
              <input className="te-input font-mono" value={form.ic_no} onChange={set("ic_no")} data-testid="gh-ic" />
            </div>
            <div>
              <label className="te-label">Contact</label>
              <input className="te-input font-mono" value={form.contact} onChange={set("contact")} data-testid="gh-contact" />
            </div>
          </div>

          <div className="flex flex-col gap-2 pt-2">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" checked={form.confirm} onChange={toggle("confirm")} data-testid="gh-confirm" />
              Also run <span className="font-mono">confirmOnlineQR_V2</span> (real ticket)
            </label>
            <label className="flex items-center gap-2 text-sm text-red-600 cursor-pointer">
              <input type="checkbox" checked={form.cancel} onChange={toggle("cancel")} data-testid="gh-cancel" />
              Cancel afterwards (test refund path)
            </label>
          </div>

          <button
            onClick={run}
            disabled={running}
            className="w-full text-sm px-4 py-3 font-bold uppercase bg-[#002FA7] text-white hover:bg-black disabled:opacity-40"
            data-testid="gh-run"
          >
            {running ? "Contacting TBS…" : "Run TBS Probe"}
          </button>

          {error && <div className="text-xs font-bold text-red-600" data-testid="gh-error">{error}</div>}
        </div>
      </div>

      {/* Right: result */}
      <div className="lg:col-span-8 space-y-4">
        {!result && !error && (
          <div className="te-card p-12 text-center text-zinc-400 text-sm font-mono">
            Fill the form on the left and click <span className="font-bold">Run TBS Probe</span> to see the live ticket + QR.
          </div>
        )}

        {result && (
          <>
            {/* Ticket render */}
            {result.qr_png_data_url && (
              <div className="te-card p-8 grid grid-cols-1 md:grid-cols-2 gap-6 items-center bg-gradient-to-br from-zinc-50 to-white" data-testid="gh-ticket">
                <div>
                  <div className="te-overline mb-2">Star Qistina · TBS Boarding Pass</div>
                  <div className="text-3xl font-black mb-4">{form.trip_no}</div>
                  {result.steps?.map((s, i) => s.ok && s.data && Object.entries(s.data).map(([k, v]) =>
                    v && typeof v !== "object" && k !== "qr" && !k.startsWith("status") && (
                      <div key={`${i}-${k}`} className="text-xs font-mono py-1 border-b border-black/5">
                        <span className="text-zinc-500 uppercase mr-2">{k}:</span>
                        <span className="font-bold">{String(v)}</span>
                      </div>
                    )
                  ))}
                  <div className="text-[10px] font-mono text-zinc-500 mt-4">TransID: {result.trans_id}</div>
                </div>
                <div className="text-center">
                  <img src={result.qr_png_data_url} alt="TBS QR" className="w-full max-w-[280px] mx-auto border border-black/10" data-testid="gh-qr-img" />
                  <div className="text-[10px] font-mono text-zinc-500 mt-2 break-all">{result.qr_string}</div>
                </div>
              </div>
            )}

            {/* Step-by-step trace */}
            <div className="te-card p-6" data-testid="gh-steps">
              <div className="te-overline mb-3">SOAP round-trip trace</div>
              {result.steps?.map((s, i) => (
                <div key={i} className={`text-xs border-l-4 pl-3 py-2 mb-2 font-mono ${s.ok ? "border-emerald-500 bg-emerald-50" : "border-red-500 bg-red-50"}`}>
                  <div className="font-bold uppercase mb-1">
                    {s.ok ? "✓" : "✗"} {s.step}
                  </div>
                  {s.ok ? (
                    <div className="space-y-0.5">
                      {Object.entries(s.data || {}).map(([k, v]) => (
                        <div key={k}><span className="text-zinc-500">{k}:</span> {String(v)}</div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-red-700">
                      [{s.error?.code}] {s.error?.message}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
