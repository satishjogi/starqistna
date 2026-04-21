import React, { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import GoogleAuthButton from "../components/GoogleAuthButton";
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

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ email: "", password: "", full_name: "", phone: "" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const upd = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const strength = useMemo(() => analyzePassword(form.password), [form.password]);

  // International phone: optional leading +, digits only, max 15 digits (E.164).
  const sanitizePhone = (v) => {
    let cleaned = v.replace(/[^\d+]/g, "");
    if (cleaned.includes("+")) {
      const hasLeadingPlus = cleaned.startsWith("+");
      cleaned = (hasLeadingPlus ? "+" : "") + cleaned.replace(/\+/g, "");
    }
    return cleaned.slice(0, 16);
  };
  const isValidPhone = (v) => /^\+[1-9]\d{6,14}$/.test(v);
  const updPhone = (e) => setForm({ ...form, phone: sanitizePhone(e.target.value) });

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    if (form.phone && !isValidPhone(form.phone)) {
      setError("Phone must be in international format, e.g. +60123456789.");
      return;
    }
    if (!strength.allPassed) {
      setError("Please satisfy all password requirements below.");
      return;
    }
    setLoading(true);
    try {
      await register(form);
      navigate("/dashboard");
    } catch (e) {
      setError(e?.response?.data?.detail || "Registration failed");
    } finally {
      setLoading(false);
    }
  };

  // Five-segment bar reflecting the 5 requirements.
  const segments = Array.from({ length: 5 }, (_, i) => i < strength.score);

  return (
    <div className="px-4 md:px-6 lg:px-10 py-16 flex justify-center">
      <div className="w-full max-w-md">
        <div className="te-overline mb-2">New passenger</div>
        <h1 className="text-4xl font-black tracking-tight mb-8">Create account</h1>
        <form onSubmit={submit} className="te-card p-8 space-y-4" data-testid="register-form">
          <GoogleAuthButton testId="register-google-btn" label="Sign up with Google" />
          <div className="flex items-center gap-3 text-[10px] font-mono text-zinc-400">
            <div className="flex-1 h-px bg-zinc-200" />
            OR SIGN UP WITH EMAIL
            <div className="flex-1 h-px bg-zinc-200" />
          </div>
          <div>
            <label className="te-label">Full name</label>
            <input className="te-input" required value={form.full_name} onChange={upd("full_name")} data-testid="register-name" />
          </div>
          <div>
            <label className="te-label">Email</label>
            <input className="te-input" type="email" required value={form.email} onChange={upd("email")} data-testid="register-email" />
          </div>
          <div>
            <label className="te-label">Phone</label>
            <input
              className="te-input"
              type="tel"
              inputMode="tel"
              value={form.phone}
              onChange={updPhone}
              placeholder="+60123456789"
              pattern="^\+[1-9]\d{6,14}$"
              title="International format, e.g. +60123456789"
              autoComplete="tel"
              data-testid="register-phone"
            />
            <div className="text-[10px] font-mono text-zinc-500 mt-1">
              International format starting with + and country code
            </div>
          </div>
          <div>
            <label className="te-label">Password</label>
            <input
              className="te-input"
              type="password"
              required
              minLength={8}
              value={form.password}
              onChange={upd("password")}
              data-testid="register-password"
              autoComplete="new-password"
            />
            {/* Strength meter */}
            <div className="mt-2 flex items-center gap-2">
              <div className="flex-1 flex gap-[3px]" data-testid="password-strength-bar">
                {segments.map((on, i) => (
                  <div
                    key={i}
                    className={`h-1.5 flex-1 rounded-sm transition-colors ${on ? strength.color : "bg-zinc-200"}`}
                  />
                ))}
              </div>
              <span
                className={`text-[10px] font-mono font-bold uppercase tracking-wider min-w-[44px] text-right ${
                  strength.score <= 2 ? "text-red-600" :
                  strength.score === 3 ? "text-amber-600" : "text-emerald-700"
                }`}
                data-testid="password-strength-label"
              >
                {strength.label}
              </span>
            </div>
            {/* Checklist */}
            <ul className="mt-3 space-y-1 text-xs" data-testid="password-checklist">
              <Req ok={strength.checks.length} testId="pw-req-length">At least 8 characters</Req>
              <Req ok={strength.checks.upper} testId="pw-req-upper">One uppercase letter (A–Z)</Req>
              <Req ok={strength.checks.lower} testId="pw-req-lower">One lowercase letter (a–z)</Req>
              <Req ok={strength.checks.digit} testId="pw-req-digit">One number (0–9)</Req>
              <Req ok={strength.checks.notCommon} testId="pw-req-common">Not a commonly-used password</Req>
            </ul>
          </div>
          {error && <div className="text-xs font-bold text-red-600" data-testid="register-error">{error}</div>}
          <button
            disabled={loading || !strength.allPassed}
            className="te-btn-primary w-full disabled:opacity-50 disabled:cursor-not-allowed"
            data-testid="register-submit-btn"
          >
            {loading ? "Creating…" : "Create account"}
          </button>
          <div className="text-center text-sm mt-2">
            Have an account? <Link className="font-bold underline" to="/login">Log in</Link>
          </div>
        </form>
      </div>
    </div>
  );
}
