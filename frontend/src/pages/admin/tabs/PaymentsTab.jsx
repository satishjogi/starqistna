import React, { useEffect, useState } from "react";
import api from "../../../lib/api";

function SummaryCard({ label, value, accent }) {
  const accentClass =
    accent === "emerald"
      ? "text-emerald-700"
      : accent === "red"
      ? "text-red-700"
      : accent === "zinc"
      ? "text-zinc-600"
      : "text-black";
  return (
    <div className="te-card p-4">
      <div className="te-overline text-[9px] text-zinc-500">{label}</div>
      <div className={`text-2xl font-black font-mono mt-1 ${accentClass}`}>{value}</div>
    </div>
  );
}

export default function PaymentsTab() {
  const [payments, setPayments] = useState({ summary: null, items: [] });
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(false);

  const load = (f = filter) => {
    setLoading(true);
    const q = f && f !== "all" ? `?status_filter=${f}` : "";
    api
      .get(`/admin/payments${q}`)
      .then(({ data }) => setPayments(data))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load(filter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  return (
    <div className="mt-6" data-testid="admin-payments-panel">
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-5">
        <SummaryCard label="Total" value={payments.summary?.total ?? 0} />
        <SummaryCard label="Paid" value={payments.summary?.paid ?? 0} accent="emerald" />
        <SummaryCard label="Initiated" value={payments.summary?.initiated ?? 0} accent="zinc" />
        <SummaryCard label="Failed" value={payments.summary?.failed ?? 0} accent="red" />
        <SummaryCard label="Gross MYR" value={`RM ${(payments.summary?.gross_myr ?? 0).toFixed(2)}`} />
        <SummaryCard label="Gross SGD" value={`S$ ${(payments.summary?.gross_sgd ?? 0).toFixed(2)}`} />
      </div>

      <div className="flex gap-2 flex-wrap mb-4">
        {["all", "paid", "initiated", "failed", "refunded"].map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1.5 text-[10px] font-mono font-bold uppercase border ${
              filter === f
                ? "bg-black text-white border-black"
                : "border-black/20 text-zinc-600 hover:border-black"
            }`}
            data-testid={`payments-filter-${f}`}
          >
            {f}
          </button>
        ))}
        <button
          onClick={() => load(filter)}
          className="px-3 py-1.5 text-[10px] font-mono font-bold uppercase border border-black/20 text-zinc-600 hover:border-black ml-auto"
          data-testid="payments-refresh"
        >
          ↻ Refresh
        </button>
      </div>

      <div className="te-card overflow-hidden">
        <div className="hidden md:grid grid-cols-12 gap-3 px-4 py-3 bg-zinc-50 border-b border-black/10 text-[10px] font-mono font-bold uppercase text-zinc-500">
          <div className="col-span-2">Created</div>
          <div className="col-span-2">Booking</div>
          <div className="col-span-3">Customer</div>
          <div className="col-span-2">Amount</div>
          <div className="col-span-2">Session</div>
          <div className="col-span-1 text-right">Status</div>
        </div>

        {loading && (
          <div className="p-6 font-mono text-xs text-zinc-500" data-testid="payments-loading">LOADING…</div>
        )}
        {!loading && (payments.items?.length ?? 0) === 0 && (
          <div className="p-6 font-mono text-xs text-zinc-500" data-testid="payments-empty">
            NO TRANSACTIONS
          </div>
        )}
        {!loading &&
          payments.items?.map((p) => {
            const created = (p.created_at || "").slice(0, 19).replace("T", " ");
            const ccy = (p.currency || "myr").toUpperCase();
            const amt = Number(p.amount || 0).toFixed(2);
            const ps = (p.payment_status || "initiated").toLowerCase();
            const badgeCls =
              ps === "paid"
                ? "bg-emerald-100 text-emerald-700"
                : ps === "failed"
                ? "bg-red-100 text-red-700"
                : ps === "refunded" || ps === "canceled" || ps === "expired"
                ? "bg-amber-100 text-amber-700"
                : "bg-zinc-100 text-zinc-600";
            const bookingRef = p?.metadata?.booking_reference || p.booking_reference || "—";
            const stripeUrl = p.session_id
              ? `https://dashboard.stripe.com/test/payments?query=${encodeURIComponent(p.session_id)}`
              : null;
            return (
              <div
                key={p.session_id || p.id}
                className="grid grid-cols-1 md:grid-cols-12 gap-3 px-4 py-3 border-b border-black/5 last:border-b-0 text-sm items-center"
                data-testid={`payment-row-${p.session_id || p.id}`}
              >
                <div className="md:col-span-2 font-mono text-xs text-zinc-600">{created}</div>
                <div className="md:col-span-2 font-mono font-bold">{bookingRef}</div>
                <div className="md:col-span-3 truncate" title={p.user_email}>
                  {p.user_email || "—"}
                </div>
                <div className="md:col-span-2 font-mono font-bold">
                  {ccy} {amt}
                </div>
                <div className="md:col-span-2 font-mono text-[10px] text-zinc-500 truncate">
                  {p.session_id ? (
                    stripeUrl ? (
                      <a
                        href={stripeUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="underline hover:text-black"
                        title="Open in Stripe Dashboard"
                      >
                        {p.session_id.slice(0, 18)}…
                      </a>
                    ) : (
                      p.session_id.slice(0, 18) + "…"
                    )
                  ) : (
                    "—"
                  )}
                </div>
                <div className="md:col-span-1 md:text-right">
                  <span className={`text-[10px] font-mono font-bold uppercase px-2 py-1 ${badgeCls}`}>
                    {ps}
                  </span>
                </div>
              </div>
            );
          })}
      </div>
    </div>
  );
}
