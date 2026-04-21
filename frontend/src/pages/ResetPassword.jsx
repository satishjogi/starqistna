import React, { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { analyzePassword } from "../lib/password-strength";

function Req({ ok, children, testId }) {
  return (
    <li className={`flex items-center gap-2 ${ok ? "text-emerald-700" : "text-zinc-500"}`} data-testid={testId}>
      <span className={`inline-block w-4 h-4 rounded-full text-[10px] font-bold flex items-center justify-center ${ok ? "bg-emerald-100 text-emerald-700 border border-emerald-300" : "bg-zinc-100 text-zinc-400 border border-zinc-200"}`}>
        {ok ? "✓" : "·"}
      </span>
      <span>{children}</span>
    </li>
  );
}

export default function ResetPassword() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") || "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  const strength = useMemo(() => analyzePassword(password), [password]);
  const match = password.length > 0 && password === confirm;
  const segments = Array.from({ length: 5 }, (_, i) => i < strength.score);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    if (!token) {
      setError("Missing or invalid reset token.");
      return;
    }
    if (!strength.allPassed) {
      setError("Please satisfy all password requirements below.");
      return;
    }
    if (!match) {
      setError("Passwords do not match.");
      return;
    }
    setLoading(true);
    try {
      await api.post("/auth/reset-password", { token, new_password: password });
      setDone(true);
      setTimeout(() => navigate("/login"), 2500);
    } catch (e) {
      setError(e?.response?.data?.detail || "Could not reset password. The link may have expired.");
    } finally {
      setLoading(false);
    }
  };

  if (!token) {
    return (
      <div className="px-4 md:px-6 lg:px-10 py-16 flex justify-center">
        <div className="w-full max-w-md te-card p-8" data-testid="reset-missing-token">
          <div className="te-overline text-red-600 mb-2">Invalid link</div>
          <h1 className="text-3xl font-black tracking-tight mb-4">Reset link missing</h1>
          <p className="text-sm text-zinc-600 mb-6">
            The reset link seems incomplete. Request a new one and try again.
          </p>
          <Link className="te-btn-primary w-full block text-center" to="/forgot-password">
            Request a new link
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="px-4 md:px-6 lg:px-10 py-16 flex justify-center">
      <div className="w-full max-w-md">
        <div className="te-overline mb-2">Account · recovery</div>
        <h1 className="text-4xl font-black tracking-tight mb-8">Choose a new password</h1>
        {done ? (
          <div className="te-card p-8 space-y-4" data-testid="reset-success">
            <div className="te-overline text-emerald-700">Success</div>
            <h2 className="text-2xl font-black tracking-tight">Password updated</h2>
            <p className="text-sm text-zinc-600">Redirecting you to the log in page…</p>
          </div>
        ) : (
          <form onSubmit={submit} className="te-card p-8 space-y-4" data-testid="reset-form">
            <div>
              <label className="te-label">New password</label>
              <input
                className="te-input"
                type="password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                data-testid="reset-password"
                autoComplete="new-password"
                autoFocus
              />
              <div className="mt-2 flex items-center gap-2">
                <div className="flex-1 flex gap-[3px]" data-testid="reset-strength-bar">
                  {segments.map((on, i) => (
                    <div key={i} className={`h-1.5 flex-1 rounded-sm transition-colors ${on ? strength.color : "bg-zinc-200"}`} />
                  ))}
                </div>
                <span
                  className={`text-[10px] font-mono font-bold uppercase tracking-wider min-w-[44px] text-right ${
                    strength.score <= 2 ? "text-red-600" :
                    strength.score === 3 ? "text-amber-600" : "text-emerald-700"
                  }`}
                  data-testid="reset-strength-label"
                >
                  {strength.label}
                </span>
              </div>
              <ul className="mt-3 space-y-1 text-xs" data-testid="reset-checklist">
                <Req ok={strength.checks.length} testId="reset-req-length">At least 8 characters</Req>
                <Req ok={strength.checks.upper} testId="reset-req-upper">One uppercase letter (A–Z)</Req>
                <Req ok={strength.checks.lower} testId="reset-req-lower">One lowercase letter (a–z)</Req>
                <Req ok={strength.checks.digit} testId="reset-req-digit">One number (0–9)</Req>
                <Req ok={strength.checks.notCommon} testId="reset-req-common">Not a commonly-used password</Req>
              </ul>
            </div>
            <div>
              <label className="te-label">Confirm new password</label>
              <input
                className="te-input"
                type="password"
                required
                minLength={8}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                data-testid="reset-confirm"
                autoComplete="new-password"
              />
              {confirm.length > 0 && !match && (
                <div className="text-[11px] font-bold text-red-600 mt-1" data-testid="reset-mismatch">
                  Passwords don't match.
                </div>
              )}
            </div>
            {error && <div className="text-xs font-bold text-red-600" data-testid="reset-error">{error}</div>}
            <button
              disabled={loading || !strength.allPassed || !match}
              className="te-btn-primary w-full disabled:opacity-50 disabled:cursor-not-allowed"
              data-testid="reset-submit-btn"
            >
              {loading ? "Updating…" : "Update password"}
            </button>
            <div className="text-center text-sm mt-2">
              <Link className="font-bold underline" to="/login">Back to log in</Link>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
