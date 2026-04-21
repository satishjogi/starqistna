import React, { useState } from "react";
import { Link } from "react-router-dom";
import api from "../lib/api";

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await api.post("/auth/forgot-password", { email });
      setSubmitted(true);
    } catch (e) {
      setError(e?.response?.data?.detail || "Could not send reset link. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="px-4 md:px-6 lg:px-10 py-16 flex justify-center">
      <div className="w-full max-w-md">
        <div className="te-overline mb-2">Account · recovery</div>
        <h1 className="text-4xl font-black tracking-tight mb-8">Forgot password</h1>
        {!submitted ? (
          <form onSubmit={submit} className="te-card p-8 space-y-4" data-testid="forgot-form">
            <p className="text-sm text-zinc-600">
              Enter the email linked to your Star Qistna account. If we find a match, we'll send a
              password reset link that stays valid for 60 minutes.
            </p>
            <div>
              <label className="te-label">Email</label>
              <input
                className="te-input"
                type="email"
                required
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                data-testid="forgot-email"
                autoComplete="email"
              />
            </div>
            {error && <div className="text-xs font-bold text-red-600" data-testid="forgot-error">{error}</div>}
            <button disabled={loading} className="te-btn-primary w-full" data-testid="forgot-submit-btn">
              {loading ? "Sending…" : "Send reset link"}
            </button>
            <div className="text-center text-sm mt-2">
              Remembered it? <Link className="font-bold underline" to="/login">Back to log in</Link>
            </div>
          </form>
        ) : (
          <div className="te-card p-8 space-y-4" data-testid="forgot-success">
            <div className="te-overline text-emerald-700">Check your inbox</div>
            <h2 className="text-2xl font-black tracking-tight">Reset link sent</h2>
            <p className="text-sm text-zinc-600 leading-relaxed">
              If an account exists for <b className="font-mono">{email}</b>, a password reset link is on its way.
              The link expires in 60 minutes. Remember to check your spam folder if you don't see it.
            </p>
            <Link className="te-btn-outline w-full block text-center" to="/login">
              Back to log in
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
