import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import GoogleAuthButton from "../components/GoogleAuthButton";

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ email: "", password: "", full_name: "", phone: "" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const upd = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    setError("");
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
            <input className="te-input" value={form.phone} onChange={upd("phone")} data-testid="register-phone" />
          </div>
          <div>
            <label className="te-label">Password (min 6)</label>
            <input className="te-input" type="password" required minLength={6} value={form.password} onChange={upd("password")} data-testid="register-password" />
          </div>
          {error && <div className="text-xs font-bold text-red-600" data-testid="register-error">{error}</div>}
          <button disabled={loading} className="te-btn-primary w-full" data-testid="register-submit-btn">
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
