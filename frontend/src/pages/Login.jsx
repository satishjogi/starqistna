import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import api from "../lib/api";

export default function Login() {
  const { refresh } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // 2FA challenge state
  const [challenge, setChallenge] = useState(null);
  const [code, setCode] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const { data } = await api.post("/auth/login", { email, password });
      if (data.requires_2fa) {
        setChallenge(data.challenge_token);
      } else {
        localStorage.setItem("te_token", data.access_token);
        await refresh();
        navigate("/dashboard");
      }
    } catch (e) {
      setError(e?.response?.data?.detail || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  const verify = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const { data } = await api.post("/auth/2fa/verify", { challenge_token: challenge, code });
      localStorage.setItem("te_token", data.access_token);
      await refresh();
      navigate("/dashboard");
    } catch (e) {
      setError(e?.response?.data?.detail || "Invalid code");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="px-6 md:px-12 lg:px-20 py-16 flex justify-center">
      <div className="w-full max-w-md">
        {!challenge ? (
          <>
            <div className="te-overline mb-2">Access</div>
            <h1 className="text-4xl font-black tracking-tight mb-8">Log in</h1>
            <form onSubmit={submit} className="te-card p-8 space-y-4" data-testid="login-form">
              <div>
                <label className="te-label">Email</label>
                <input className="te-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} data-testid="login-email" />
              </div>
              <div>
                <label className="te-label">Password</label>
                <input className="te-input" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} data-testid="login-password" />
              </div>
              {error && <div className="text-xs font-bold text-red-600" data-testid="login-error">{error}</div>}
              <button disabled={loading} className="te-btn-primary w-full" data-testid="login-submit-btn">
                {loading ? "Signing in…" : "Log in"}
              </button>
              <div className="text-center text-sm mt-2">
                No account? <Link className="font-bold underline" to="/register">Create one</Link>
              </div>
              <div className="text-[10px] font-mono text-zinc-500 text-center mt-4">
                ADMIN DEMO · admin@starqistna.com / Admin@123
              </div>
            </form>
          </>
        ) : (
          <>
            <div className="te-overline mb-2">Access · 2FA</div>
            <h1 className="text-4xl font-black tracking-tight mb-2">Verify it's you</h1>
            <p className="text-sm text-zinc-600 mb-6">Enter the 6-digit code from your authenticator app.</p>
            <form onSubmit={verify} className="te-card p-8 space-y-4" data-testid="2fa-challenge-form">
              <div>
                <label className="te-label">Code</label>
                <input
                  className="te-input font-mono text-3xl tracking-[0.4em] text-center"
                  placeholder="000000"
                  required
                  maxLength={6}
                  autoFocus
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  data-testid="2fa-challenge-code"
                />
              </div>
              {error && <div className="text-xs font-bold text-red-600" data-testid="2fa-challenge-error">{error}</div>}
              <button disabled={loading || code.length !== 6} className="te-btn-primary w-full" data-testid="2fa-challenge-submit">
                {loading ? "Verifying…" : "Verify & continue"}
              </button>
              <button type="button" onClick={() => { setChallenge(null); setCode(""); setError(""); }} className="w-full text-xs font-mono text-zinc-500 hover:text-black">
                ← BACK TO LOGIN
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
