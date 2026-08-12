import React, { useEffect, useState } from "react";
import api from "../../../lib/api";

/**
 * Modal for editing a single schedule.
 *
 *   1. Load impact (confirmed bookings / CTS QRs) so admin can see the blast radius
 *   2. Present editable fields with the current values pre-filled
 *   3. Detect if time/date changed → surface the notify-passengers checkbox
 *   4. PATCH the schedule; if notify=true and time/date changed the backend
 *      dispatches emails to every affected passenger
 */
export default function EditScheduleModal({ schedule, terminals, routes, onClose, onSaved }) {
  const [form, setForm] = useState({
    departure_date: schedule.departure_date || "",
    departure_time: schedule.departure_time || "",
    arrival_time: schedule.arrival_time || "",
    bus_operator: schedule.bus_operator || "",
    bus_type: schedule.bus_type || "Executive",
    adult_fare: schedule.adult_fare ?? "",
    child_fare: schedule.child_fare ?? "",
    total_seats: schedule.total_seats ?? 40,
    route_id: schedule.route_id || "",
    trip_no: schedule.trip_no || "",
  });
  const [notify, setNotify] = useState(false);
  const [impact, setImpact] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    let alive = true;
    api.get(`/admin/schedules/${schedule.id}/impact`)
      .then(({ data }) => { if (alive) setImpact(data); })
      .catch(() => { if (alive) setImpact({ confirmed_bookings: 0 }); });
    return () => { alive = false; };
  }, [schedule.id]);

  const timeOrDateChanged =
    form.departure_date !== schedule.departure_date ||
    form.departure_time !== schedule.departure_time;
  const shouldOfferNotify =
    (impact?.confirmed_bookings || 0) > 0 && timeOrDateChanged;

  const fromTerm = terminals.find((t) => t.id === schedule.from_terminal_id);
  const toTerm = terminals.find((t) => t.id === schedule.to_terminal_id);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setSuccess("");
    // Only send fields that ACTUALLY changed — makes the audit log cleaner and
    // avoids clobbering values with default form state.
    const payload = { notify_passengers: notify };
    for (const [k, v] of Object.entries(form)) {
      if (v !== "" && v !== null && v !== undefined && String(v) !== String(schedule[k] ?? "")) {
        payload[k] = ["adult_fare", "child_fare"].includes(k) ? Number(v) :
                     k === "total_seats" ? parseInt(v, 10) : v;
      }
    }
    if (Object.keys(payload).length === 1) {
      setError("No fields changed.");
      return;
    }
    setSaving(true);
    try {
      const { data } = await api.patch(`/admin/schedules/${schedule.id}`, payload);
      const notified = data.notified_passengers || 0;
      setSuccess(
        notified > 0
          ? `Saved — notification email dispatched to ${notified} passenger${notified === 1 ? "" : "s"}.`
          : "Saved."
      );
      // Give the operator a beat to read the success message before we close.
      setTimeout(() => { onSaved?.(); onClose?.(); }, 1200);
    } catch (e) {
      const detail = e?.response?.data?.detail;
      setError(typeof detail === "string" ? detail : (detail?.message || "Failed to save changes."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={onClose} data-testid="edit-schedule-modal">
      <div className="bg-white max-w-2xl w-full max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="border-b border-black/10 px-6 py-4 sticky top-0 bg-white z-10 flex items-baseline justify-between">
          <div>
            <div className="te-overline">Edit schedule</div>
            <div className="font-mono text-sm mt-1">
              {fromTerm?.code} → {toTerm?.code}
              <span className="text-zinc-400 ml-2">· {schedule.departure_date} · {schedule.departure_time}</span>
            </div>
          </div>
          <button onClick={onClose} className="text-2xl leading-none text-zinc-400 hover:text-black" data-testid="close-edit-modal">×</button>
        </div>

        {/* Impact banner — shows blast radius BEFORE admin edits */}
        {impact && (
          <div className={`px-6 py-3 border-b ${
            impact.confirmed_bookings > 0
              ? "bg-amber-50 border-amber-200 text-amber-900"
              : "bg-emerald-50 border-emerald-200 text-emerald-900"
          }`} data-testid="impact-banner">
            <div className="text-xs font-mono uppercase tracking-wider">
              {impact.confirmed_bookings > 0 ? "⚠️  Bookings exist" : "✓ No confirmed bookings"}
            </div>
            <div className="text-sm mt-1">
              {impact.confirmed_bookings > 0 ? (
                <>
                  <b>{impact.confirmed_bookings}</b> confirmed booking{impact.confirmed_bookings === 1 ? "" : "s"} 
                  {" "}({impact.confirmed_passengers} passenger{impact.confirmed_passengers === 1 ? "" : "s"})
                  {impact.bookings_with_cts_qr > 0 && (
                    <span className="block mt-1 text-xs text-red-700 font-bold">
                      🎫 {impact.bookings_with_cts_qr} booking{impact.bookings_with_cts_qr === 1 ? "" : "s"} already have TBS QR passes issued —
                      changing departure time may require reissuing them (use Retry TBS from the booking page).
                    </span>
                  )}
                </>
              ) : "Safe to edit freely — no passengers affected."}
            </div>
          </div>
        )}

        <form onSubmit={submit} className="p-6 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="te-label">Departure date</label>
              <input type="date" className="te-input" value={form.departure_date}
                     onChange={(e) => setForm({ ...form, departure_date: e.target.value })}
                     data-testid="edit-departure-date" />
            </div>
            <div>
              <label className="te-label">Departure time</label>
              <input type="time" className="te-input" value={form.departure_time}
                     onChange={(e) => setForm({ ...form, departure_time: e.target.value })}
                     data-testid="edit-departure-time" />
            </div>
            <div>
              <label className="te-label">Arrival time</label>
              <input type="time" className="te-input" value={form.arrival_time}
                     onChange={(e) => setForm({ ...form, arrival_time: e.target.value })} />
            </div>
            <div>
              <label className="te-label">Trip no. (TBS)</label>
              <input type="text" className="te-input" value={form.trip_no}
                     onChange={(e) => setForm({ ...form, trip_no: e.target.value })}
                     placeholder="e.g. SQ001" data-testid="edit-trip-no" />
            </div>
            <div>
              <label className="te-label">Bus operator</label>
              <input type="text" className="te-input" value={form.bus_operator}
                     onChange={(e) => setForm({ ...form, bus_operator: e.target.value })} />
            </div>
            <div>
              <label className="te-label">Bus type</label>
              <select className="te-input" value={form.bus_type}
                      onChange={(e) => setForm({ ...form, bus_type: e.target.value })}>
                <option>Executive</option>
                <option>Standard</option>
                <option>VIP 27</option>
              </select>
            </div>
            <div>
              <label className="te-label">Adult fare ({schedule.currency?.toUpperCase() || "MYR"})</label>
              <input type="number" step="0.01" className="te-input" value={form.adult_fare}
                     onChange={(e) => setForm({ ...form, adult_fare: e.target.value })} />
            </div>
            <div>
              <label className="te-label">Child fare</label>
              <input type="number" step="0.01" className="te-input" value={form.child_fare}
                     onChange={(e) => setForm({ ...form, child_fare: e.target.value })} />
            </div>
            <div>
              <label className="te-label">Total seats</label>
              <input type="number" min={12} max={60} className="te-input" value={form.total_seats}
                     onChange={(e) => setForm({ ...form, total_seats: e.target.value })} />
            </div>
            <div>
              <label className="te-label">Route</label>
              <select className="te-input" value={form.route_id}
                      onChange={(e) => setForm({ ...form, route_id: e.target.value })}>
                <option value="">— unlinked —</option>
                {routes.map((r) => <option key={r.id} value={r.id}>{r.code} · {r.name}</option>)}
              </select>
            </div>
          </div>

          {/* Notify-passengers checkbox — only shown when date/time changed AND there are bookings */}
          {shouldOfferNotify && (
            <label className="flex items-start gap-3 p-4 border-2 border-[#002FA7]/30 bg-blue-50 cursor-pointer" data-testid="notify-checkbox-container">
              <input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)}
                     className="mt-0.5" data-testid="notify-checkbox" />
              <div>
                <div className="font-bold text-sm">Send notification email to affected passengers</div>
                <div className="text-xs text-zinc-600 mt-1">
                  {impact?.confirmed_bookings} passenger{impact?.confirmed_bookings === 1 ? "" : "s"} will
                  receive an email with the new departure time. Leave unchecked to silently update.
                </div>
              </div>
            </label>
          )}

          {error && <div className="text-sm text-red-600 font-bold" data-testid="edit-error">{error}</div>}
          {success && <div className="text-sm text-emerald-700 font-bold" data-testid="edit-success">{success}</div>}

          <div className="flex gap-3 pt-2 border-t border-black/10">
            <button type="submit" className="te-btn-primary" disabled={saving} data-testid="save-schedule-btn">
              {saving ? "Saving…" : "Save changes"}
            </button>
            <button type="button" className="te-btn-outline" onClick={onClose} disabled={saving}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
