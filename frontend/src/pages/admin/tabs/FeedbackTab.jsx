import React, { useEffect, useState } from "react";
import api from "../../../lib/api";

export default function FeedbackTab() {
  const [feedback, setFeedback] = useState({ summary: null, items: [] });
  const [filter, setFilter] = useState({ status: "all", category: "all" });

  const load = (f = filter) => {
    const params = new URLSearchParams();
    if (f.status && f.status !== "all") params.set("status_filter", f.status);
    if (f.category && f.category !== "all") params.set("category", f.category);
    api.get(`/admin/feedback?${params.toString()}`)
      .then(({ data }) => setFeedback(data))
      .catch(() => {});
  };

  useEffect(() => {
    load(filter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter.status, filter.category]);

  const updateStatus = async (id, status) => {
    await api.patch(`/admin/feedback/${id}`, { status });
    load(filter);
  };

  return (
    <div className="mt-6 space-y-4" data-testid="feedback-admin-section">
      {feedback.summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-[1px] bg-black/10 border border-black/10">
          <div className="bg-white p-4"><div className="te-overline text-[9px]">Total</div><div className="text-2xl font-black font-mono">{feedback.summary.total}</div></div>
          <div className="bg-white p-4"><div className="te-overline text-[9px] text-[#B5121B]">New</div><div className="text-2xl font-black font-mono text-[#B5121B]">{feedback.summary.new}</div></div>
          <div className="bg-white p-4"><div className="te-overline text-[9px] text-amber-700">In progress</div><div className="text-2xl font-black font-mono text-amber-700">{feedback.summary.in_progress}</div></div>
          <div className="bg-white p-4"><div className="te-overline text-[9px] text-emerald-700">Resolved</div><div className="text-2xl font-black font-mono text-emerald-700">{feedback.summary.resolved}</div></div>
        </div>
      )}

      <div className="te-card p-4">
        <div className="te-overline mb-3">Filters</div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <select className="te-input" value={filter.status} onChange={(e) => setFilter({ ...filter, status: e.target.value })} data-testid="feedback-filter-status">
            <option value="all">All statuses</option>
            <option value="new">New</option>
            <option value="in_progress">In progress</option>
            <option value="resolved">Resolved</option>
          </select>
          <select className="te-input" value={filter.category} onChange={(e) => setFilter({ ...filter, category: e.target.value })} data-testid="feedback-filter-category">
            <option value="all">All categories</option>
            <option value="general">General</option>
            <option value="booking_issue">Booking issue</option>
            <option value="complaint">Complaint</option>
            <option value="suggestion">Suggestion</option>
            <option value="praise">Praise</option>
          </select>
          <button type="button" onClick={() => load(filter)} className="te-btn-outline" data-testid="feedback-refresh-btn">Refresh</button>
        </div>
      </div>

      <div className="space-y-3">
        {feedback.items.length === 0 && (
          <div className="te-card p-6 text-sm text-zinc-500" data-testid="feedback-empty">
            No feedback matches these filters yet.
          </div>
        )}
        {feedback.items.map((f) => {
          const catLabel = { general: "General", booking_issue: "Booking issue", complaint: "Complaint", suggestion: "Suggestion", praise: "Praise" }[f.category] || f.category;
          const statusColor = f.status === "new" ? "bg-[#B5121B] text-white" : f.status === "in_progress" ? "bg-amber-500 text-white" : "bg-emerald-600 text-white";
          return (
            <div key={f.id} className="te-card p-5 space-y-2" data-testid={`feedback-row-${f.id}`}>
              <div className="flex flex-wrap items-center gap-3 justify-between">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono font-black text-sm">{f.reference}</span>
                  <span className="inline-block px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider bg-zinc-200">{catLabel}</span>
                  <span className={`inline-block px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${statusColor}`}>{f.status.replace("_", " ")}</span>
                  {f.rating && <span className="text-amber-500 text-sm">{"★".repeat(f.rating)}{"☆".repeat(5 - f.rating)}</span>}
                </div>
                <div className="font-mono text-[10px] text-zinc-500">{new Date(f.created_at).toLocaleString()}</div>
              </div>
              <div className="text-xs font-mono text-zinc-600">
                <a href={`mailto:${f.email}`} className="text-[#002FA7] font-bold">{f.email}</a>
                {f.name && <span> · {f.name}</span>}
                {f.booking_reference && <span> · Booking: <b>{f.booking_reference}</b></span>}
              </div>
              <div className="text-sm text-zinc-800 whitespace-pre-wrap border-l-2 border-[#002FA7] pl-3 py-1">{f.message}</div>
              <div className="flex gap-2 flex-wrap pt-2">
                {f.status !== "in_progress" && (
                  <button type="button" onClick={() => updateStatus(f.id, "in_progress")} className="text-[10px] font-mono font-bold text-amber-700 hover:underline uppercase tracking-wider" data-testid={`feedback-mark-progress-${f.id}`}>
                    Mark in progress
                  </button>
                )}
                {f.status !== "resolved" && (
                  <button type="button" onClick={() => updateStatus(f.id, "resolved")} className="text-[10px] font-mono font-bold text-emerald-700 hover:underline uppercase tracking-wider" data-testid={`feedback-mark-resolved-${f.id}`}>
                    Mark resolved
                  </button>
                )}
                {f.status === "resolved" && (
                  <button type="button" onClick={() => updateStatus(f.id, "new")} className="text-[10px] font-mono font-bold text-zinc-700 hover:underline uppercase tracking-wider" data-testid={`feedback-reopen-${f.id}`}>
                    Reopen
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
