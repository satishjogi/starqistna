import React, { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";

const CATEGORIES = [
  { value: "general", label: "General" },
  { value: "booking_issue", label: "Booking issue" },
  { value: "complaint", label: "Complaint" },
  { value: "suggestion", label: "Suggestion" },
  { value: "praise", label: "Praise" },
];

function Star({ filled, onClick, idx }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="transition-transform hover:scale-110"
      data-testid={`feedback-star-${idx}`}
      aria-label={`${idx} star${idx === 1 ? "" : "s"}`}
    >
      <svg width="28" height="28" viewBox="0 0 24 24" fill={filled ? "#F59E0B" : "none"} stroke={filled ? "#B45309" : "#A1A1AA"} strokeWidth="1.5" strokeLinejoin="round">
        <path d="M12 2 L14.9 8.5 L22 9.3 L16.5 14.1 L18.2 21 L12 17.3 L5.8 21 L7.5 14.1 L2 9.3 L9.1 8.5 Z" />
      </svg>
    </button>
  );
}

export default function Feedback() {
  const { user } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();

  const [form, setForm] = useState({
    name: user?.full_name || "",
    email: user?.email || "",
    category: "general",
    booking_reference: params.get("ref") || "",
    rating: null,
    message: "",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(null);

  const upd = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    if (form.message.trim().length < 10) {
      setError("Please write at least 10 characters so we can help properly.");
      return;
    }
    setLoading(true);
    try {
      const { data } = await api.post("/feedback", {
        name: form.name || null,
        email: form.email,
        category: form.category,
        booking_reference: form.booking_reference || null,
        rating: form.rating,
        message: form.message,
      });
      setSuccess({ reference: data.reference });
    } catch (e) {
      setError(e?.response?.data?.detail || "Could not submit — please try again.");
    } finally {
      setLoading(false);
    }
  };

  if (success) {
    return (
      <div className="px-4 md:px-6 lg:px-10 py-16 flex justify-center">
        <div className="w-full max-w-md te-card p-8 space-y-4" data-testid="feedback-success">
          <div className="te-overline text-emerald-700">Received</div>
          <h1 className="text-3xl font-black tracking-tight">Thanks for writing to us.</h1>
          <p className="text-sm text-zinc-600 leading-relaxed">
            We've sent a confirmation to <b className="font-mono">{form.email}</b>. Our team will reply within 24 hours on working days.
          </p>
          <div className="font-mono text-xs bg-zinc-100 border border-black/10 px-4 py-3">
            <div className="te-overline text-[9px] mb-1">Reference</div>
            <div className="text-lg font-black" data-testid="feedback-reference">{success.reference}</div>
          </div>
          <div className="flex gap-2">
            <Link to="/" className="te-btn-outline flex-1 text-center">Back to home</Link>
            <button type="button" onClick={() => { setSuccess(null); setForm({ ...form, message: "", rating: null }); }} className="te-btn-primary flex-1" data-testid="feedback-send-another">
              Send another
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="px-4 md:px-6 lg:px-10 py-16 flex justify-center">
      <div className="w-full max-w-lg">
        <div className="te-overline mb-2">Customer support</div>
        <h1 className="text-4xl font-black tracking-tight mb-3">We'd love to hear from you.</h1>
        <p className="text-sm text-zinc-600 mb-8">
          Found a bug, want to praise a driver, or suggest a route? Drop a note below — a human reads every one.
        </p>
        <form onSubmit={submit} className="te-card p-8 space-y-4" data-testid="feedback-form">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="te-label">Name (optional)</label>
              <input
                className="te-input"
                value={form.name}
                onChange={upd("name")}
                data-testid="feedback-name"
              />
            </div>
            <div>
              <label className="te-label">Email</label>
              <input
                className="te-input"
                type="email"
                required
                value={form.email}
                onChange={upd("email")}
                data-testid="feedback-email"
              />
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="te-label">Category</label>
              <select className="te-input" value={form.category} onChange={upd("category")} data-testid="feedback-category">
                {CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            </div>
            <div>
              <label className="te-label">Booking reference (optional)</label>
              <input
                className="te-input font-mono"
                placeholder="e.g. SQA7B2M"
                value={form.booking_reference}
                onChange={(e) => setForm({ ...form, booking_reference: e.target.value.toUpperCase() })}
                data-testid="feedback-booking-ref"
              />
            </div>
          </div>
          <div>
            <label className="te-label">Rating (optional)</label>
            <div className="flex items-center gap-2" data-testid="feedback-rating">
              {[1, 2, 3, 4, 5].map((n) => (
                <Star
                  key={n}
                  idx={n}
                  filled={form.rating != null && n <= form.rating}
                  onClick={() => setForm({ ...form, rating: form.rating === n ? null : n })}
                />
              ))}
              {form.rating != null && (
                <button type="button" onClick={() => setForm({ ...form, rating: null })} className="text-[10px] font-mono text-zinc-500 underline ml-2" data-testid="feedback-rating-clear">
                  clear
                </button>
              )}
            </div>
          </div>
          <div>
            <label className="te-label">Your message</label>
            <textarea
              required
              rows={6}
              maxLength={4000}
              className="te-input"
              value={form.message}
              onChange={upd("message")}
              placeholder="Tell us what happened or what you'd like to see improved…"
              data-testid="feedback-message"
            />
            <div className="text-[10px] font-mono text-zinc-400 mt-1 text-right">
              {form.message.length} / 4000
            </div>
          </div>
          {error && <div className="text-xs font-bold text-red-600" data-testid="feedback-error">{error}</div>}
          <button disabled={loading} className="te-btn-primary w-full" data-testid="feedback-submit-btn">
            {loading ? "Sending…" : "Send feedback"}
          </button>
          <div className="text-[10px] font-mono text-zinc-500 text-center mt-1">
            YOU'LL GET A COPY BY EMAIL · WE RESPOND IN 24H ON WORKING DAYS
          </div>
        </form>
      </div>
    </div>
  );
}
