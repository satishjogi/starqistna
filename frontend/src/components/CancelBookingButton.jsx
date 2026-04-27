import React, { useState } from "react";
import api from "../lib/api";

const CCY_MAP = { MYR: "RM", SGD: "S$", USD: "$" };
const fmt = (v, ccy) => `${CCY_MAP[ccy] || ccy} ${Number(v).toFixed(2)}`;

export default function CancelBookingButton({ booking, onCancelled }) {
  const [open, setOpen] = useState(false);
  const [quote, setQuote] = useState(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState("");

  // Only confirmed bookings that haven't departed can be cancelled
  if (booking?.status !== "confirmed") return null;

  const openModal = async () => {
    setOpen(true);
    setErr("");
    setLoading(true);
    try {
      const { data } = await api.get(`/bookings/${booking.id}/cancellation-quote`);
      setQuote(data);
    } catch (e) {
      setErr(e?.response?.data?.detail || "Could not load cancellation details");
    } finally {
      setLoading(false);
    }
  };

  const close = () => {
    if (submitting) return;
    setOpen(false);
    setQuote(null);
    setErr("");
  };

  const confirmCancel = async () => {
    setSubmitting(true);
    setErr("");
    try {
      const { data } = await api.post(`/bookings/${booking.id}/cancel`);
      setOpen(false);
      setQuote(null);
      if (onCancelled) onCancelled(data);
    } catch (e) {
      setErr(e?.response?.data?.detail || "Cancellation failed");
    } finally {
      setSubmitting(false);
    }
  };

  const eligible = quote?.refund_eligible;
  const hours = quote?.hours_to_departure;

  return (
    <>
      <button
        type="button"
        onClick={openModal}
        className="text-[10px] font-mono font-bold uppercase tracking-wider px-3 py-2 border border-red-300 text-red-700 hover:bg-red-50"
        data-testid="cancel-booking-btn"
      >
        Cancel booking
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4"
          data-testid="cancel-booking-modal"
          onClick={close}
        >
          <div
            className="bg-white max-w-md w-full p-7 border border-black/10 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="te-overline mb-2">Cancel booking</div>
            <h2 className="text-2xl font-black tracking-tight mb-4" data-testid="cancel-modal-headline">
              {loading ? "Checking policy…" : eligible ? "Full refund" : "Ticket will be burned"}
            </h2>

            {loading && <div className="font-mono text-xs text-zinc-500">LOADING…</div>}

            {!loading && quote && (
              <>
                <div className="space-y-2 text-sm border-l-2 border-black/10 pl-4 py-1 mb-5">
                  <div className="flex justify-between">
                    <span className="text-zinc-500">Reference</span>
                    <span className="font-mono font-bold">{quote.reference}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-zinc-500">Time to departure</span>
                    <span className="font-mono">
                      {hours >= 0 ? `${hours.toFixed(1)} h` : "departed"}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-zinc-500">Refund threshold</span>
                    <span className="font-mono">≥ {quote.threshold_hours} h before</span>
                  </div>
                </div>

                {eligible ? (
                  <div className="bg-emerald-50 border border-emerald-200 p-4 mb-5" data-testid="cancel-refund-info">
                    <div className="te-overline text-[10px] text-emerald-700 mb-1">You'll be refunded</div>
                    <div className="text-2xl font-black font-mono text-emerald-700">
                      {fmt(quote.refund_amount, quote.currency)}
                    </div>
                    <div className="text-[11px] text-emerald-800 mt-2 leading-relaxed">
                      Refund goes back to your original payment method (Stripe). Funds typically appear in 5–10 business days.
                    </div>
                  </div>
                ) : (
                  <div className="bg-red-50 border border-red-200 p-4 mb-5" data-testid="cancel-burn-info">
                    <div className="te-overline text-[10px] text-red-700 mb-1">No refund</div>
                    <div className="text-sm font-bold text-red-800 leading-snug">
                      You're within {quote.threshold_hours} hours of departure. Per our refund policy, the ticket will be cancelled but no refund will be issued.
                    </div>
                  </div>
                )}
              </>
            )}

            {err && (
              <div className="text-xs font-bold text-red-700 mb-3" data-testid="cancel-error">
                {err}
              </div>
            )}

            <div className="flex gap-2 justify-end">
              <button
                type="button"
                onClick={close}
                disabled={submitting}
                className="px-4 py-2 text-xs font-mono font-bold uppercase tracking-wider border border-black/20 hover:border-black disabled:opacity-50"
                data-testid="cancel-modal-keep"
              >
                Keep booking
              </button>
              {!loading && quote && (
                <button
                  type="button"
                  onClick={confirmCancel}
                  disabled={submitting}
                  className={`px-4 py-2 text-xs font-mono font-bold uppercase tracking-wider text-white disabled:opacity-50 ${
                    eligible ? "bg-emerald-700 hover:bg-emerald-800" : "bg-red-700 hover:bg-red-800"
                  }`}
                  data-testid="cancel-modal-confirm"
                >
                  {submitting ? "Cancelling…" : eligible ? "Cancel & refund" : "Cancel anyway"}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
