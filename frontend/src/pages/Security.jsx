import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { QRCodeSVG } from "qrcode.react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";

export default function Security() {
  const { user, loading, refresh } = useAuth();
  const navigate = useNavigate();

  const [mode, setMode] = useState("idle"); // idle | setup | disable
  const [password, setPassword] = useState("");
  const [secret, setSecret] = useState("");
  const [otpauthUri, setOtpauthUri] = useState("");
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!loading && !user) navigate("/login");
  }, [user, loading, navigate]);

  const startSetup = async (e) => {
    e.preventDefault();
    setErr(""); setMsg(""); setBusy(true);
    try {
      const { data } = await api.post("/auth/2fa/setup", { password });
      setSecret(data.secret);
      setOtpauthUri(data.otpauth_uri);
      setMode("setup");
    } catch (e) {
      setErr(e?.response?.data?.detail || "Setup failed");
    } finally { setBusy(false); }
  };

  const confirmEnable = async (e) => {
    e.preventDefault();
    setErr(""); setMsg(""); setBusy(true);
    try {
      await api.post("/auth/2fa/enable", { code });
      setMsg("Two-factor authentication enabled.");
      setMode("idle");
      setPassword(""); setCode(""); setSecret(""); setOtpauthUri("");
      await refresh();
    } catch (e) {
      setErr(e?.response?.data?.detail || "Invalid code");
    } finally { setBusy(false); }
  };

  const disable = async (e) => {
    e.preventDefault();
    setErr(""); setMsg(""); setBusy(true);
    try {
      await api.post("/auth/2fa/disable", { password, code });
      setMsg("Two-factor authentication disabled.");
      setMode("idle");
      setPassword(""); setCode("");
      await refresh();
    } catch (e) {
      setErr(e?.response?.data?.detail || "Failed");
    } finally { setBusy(false); }
  };

  if (!user) return null;

  const enabled = user.totp_enabled;

  return (
    <div className="px-4 md:px-6 lg:px-10 py-10 max-w-3xl">
      <div className="te-overline mb-2">Account</div>
      <h1 className="text-4xl md:text-5xl font-black tracking-tight">Security</h1>

      <div className="te-card p-6 md:p-8 mt-8" data-testid="security-2fa-card">
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div>
            <div className="te-overline text-[10px]">Two-factor authentication</div>
            <div className="font-black text-xl mt-1">{enabled ? "2FA is ON" : "2FA is OFF"}</div>
            <p className="text-sm text-zinc-600 mt-2 max-w-md">
              {enabled
                ? "Your account is protected by time-based one-time codes. You'll be asked for a 6-digit code each time you log in."
                : "Strongly recommended for admin accounts. Use Google Authenticator, Authy, 1Password, or any TOTP app."}
            </p>
          </div>
          <div className={`text-[10px] font-mono font-black uppercase px-3 py-2 ${enabled ? "bg-emerald-100 text-emerald-700" : "bg-zinc-100 text-zinc-600"}`} data-testid="2fa-status-pill">
            {enabled ? "ENABLED" : "DISABLED"}
          </div>
        </div>

        {mode === "idle" && (
          <div className="mt-6 flex gap-3">
            {!enabled ? (
              <button onClick={() => setMode("setup-password")} className="te-btn-primary" data-testid="enable-2fa-btn">Enable 2FA</button>
            ) : (
              <button onClick={() => setMode("disable")} className="te-btn-outline text-red-600 border-red-200" data-testid="disable-2fa-btn">Disable 2FA</button>
            )}
          </div>
        )}

        {mode === "setup-password" && (
          <form onSubmit={startSetup} className="mt-6 space-y-4 max-w-sm" data-testid="2fa-setup-password-form">
            <div>
              <label className="te-label">Confirm your password</label>
              <input type="password" required className="te-input" value={password} onChange={(e) => setPassword(e.target.value)} data-testid="2fa-setup-password" />
            </div>
            <div className="flex gap-2">
              <button type="submit" disabled={busy} className="te-btn-primary">{busy ? "…" : "Continue →"}</button>
              <button type="button" onClick={() => { setMode("idle"); setPassword(""); setErr(""); }} className="te-btn-outline">Cancel</button>
            </div>
          </form>
        )}

        {mode === "setup" && (
          <div className="mt-6">
            <div className="te-overline mb-3">Step 1 · Scan the QR with your authenticator</div>
            <div className="flex items-start gap-6 flex-wrap">
              <div className="bg-white p-3 border border-black/10" data-testid="2fa-qr">
                <QRCodeSVG value={otpauthUri} size={180} level="M" />
              </div>
              <div className="flex-1 min-w-[240px]">
                <div className="text-xs text-zinc-600 mb-2">Or enter this secret manually:</div>
                <code className="block font-mono text-sm bg-zinc-100 px-3 py-2 break-all select-all" data-testid="2fa-secret">{secret}</code>
                <div className="text-[10px] font-mono text-zinc-500 mt-2">TIME-BASED · 30 SECOND WINDOW</div>
              </div>
            </div>

            <form onSubmit={confirmEnable} className="mt-8 space-y-3 max-w-sm" data-testid="2fa-confirm-form">
              <div className="te-overline">Step 2 · Verify code</div>
              <input
                required
                maxLength={6}
                className="te-input font-mono text-2xl tracking-[0.4em] text-center"
                placeholder="000000"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                data-testid="2fa-confirm-code"
              />
              <div className="flex gap-2">
                <button type="submit" disabled={busy || code.length !== 6} className="te-btn-primary" data-testid="2fa-confirm-submit">
                  {busy ? "Verifying…" : "Enable 2FA"}
                </button>
                <button type="button" onClick={() => { setMode("idle"); setCode(""); setSecret(""); setOtpauthUri(""); setErr(""); }} className="te-btn-outline">Cancel</button>
              </div>
            </form>
          </div>
        )}

        {mode === "disable" && (
          <form onSubmit={disable} className="mt-6 space-y-4 max-w-sm" data-testid="2fa-disable-form">
            <div>
              <label className="te-label">Password</label>
              <input type="password" required className="te-input" value={password} onChange={(e) => setPassword(e.target.value)} data-testid="2fa-disable-password" />
            </div>
            <div>
              <label className="te-label">Current 2FA code</label>
              <input required maxLength={6} className="te-input font-mono tracking-widest text-center" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} data-testid="2fa-disable-code" />
            </div>
            <div className="flex gap-2">
              <button type="submit" disabled={busy || code.length !== 6} className="te-btn-outline text-red-600 border-red-200" data-testid="2fa-disable-submit">
                {busy ? "…" : "Disable 2FA"}
              </button>
              <button type="button" onClick={() => { setMode("idle"); setPassword(""); setCode(""); setErr(""); }} className="te-btn-outline">Cancel</button>
            </div>
          </form>
        )}

        {msg && <div className="mt-4 text-xs font-bold text-emerald-700" data-testid="security-msg">{msg}</div>}
        {err && <div className="mt-4 text-xs font-bold text-red-600" data-testid="security-err">{err}</div>}
      </div>

      <div className="text-xs text-zinc-500 mt-4 font-mono">
        Recommended apps: Google Authenticator · Authy · 1Password · Microsoft Authenticator
      </div>
    </div>
  );
}
