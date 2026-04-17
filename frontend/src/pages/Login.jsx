import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
      navigate("/dashboard");
    } catch (e) {
      setError(e?.response?.data?.detail || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="px-6 md:px-12 lg:px-20 py-16 flex justify-center">
      <div className="w-full max-w-md">
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
            ADMIN DEMO · admin@transit.my / Admin@123
          </div>
        </form>
      </div>
    </div>
  );
}
