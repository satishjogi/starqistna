import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import api from "../lib/api";
import { analyzePassword } from "../lib/password-strength";

export default function ChangePassword() {
  const { user, refresh, logout } = useAuth();
  const navigate = useNavigate();
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  const forced = !!user?.must_change_password;
  const strength = analyzePassword(newPw);
  const matches = newPw.length > 0 && newPw === confirmPw;
  const canSubmit = currentPw.length > 0 && strength.allPassed && matches && !loading;

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setSuccess("");
    setLoading(true);
    try {
      await api.post("/auth/change-password", {
        current_password: currentPw,
        new_password: newPw,
      });
      setSuccess("Password updated. Redirecting…");
      setCurrentPw("");
      setNewPw("");
      setConfirmPw("");
      await refresh();
      setTimeout(() => navigate(user?.is_admin ? "/admin" : "/dashboard"), 800);
    } catch (e) {
      setError(e?.response?.data?.detail || "Could not change password");
    } finally {
      setLoading(false);
    }
  };

  if (!user) {
    navigate("/login");
    return null;
  }

  return (
    <div className="px-4 md:px-6 lg:px-10 py-16 flex justify-center">
      <div className="w-full max-w-md">
        <div className="te-overline mb-2">Account · Security</div>
        <h1 className="text-4xl font-black tracking-tight" data-testid="change-pw-headline">
          {forced ? "Set a new password" : "Change password"}
        </h1>
        {forced && (
          <div className="mt-4 bg-amber-50 border border-amber-200 p-4 text-sm text-amber-900" data-testid="change-pw-forced-notice">
            <div className="font-bold mb-1">First-time login security check</div>
            Your account is using the default seed password. For security, please choose a unique password before continuing.
          </div>
        )}

        <form onSubmit={submit} className="te-card p-6 mt-6 space-y-4" data-testid="change-pw-form">
          <div>
            <label className="te-label">Current password</label>
            <input
              type="password"
              autoComplete="current-password"
              required
              className="te-input"
              value={currentPw}
              onChange={(e) => setCurrentPw(e.target.value)}
              data-testid="change-pw-current"
            />
          </div>

          <div>
            <label className="te-label">New password</label>
            <input
              type="password"
              autoComplete="new-password"
              required
              className="te-input"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              data-testid="change-pw-new"
            />
            {newPw.length > 0 && (
              <>
                <div className="mt-2 flex gap-1" data-testid="change-pw-strength-bar">
                  {[1, 2, 3, 4, 5].map((i) => (
                    <div
                      key={i}
                      className={`h-1.5 flex-1 ${i <= strength.score ? strength.color : "bg-zinc-200"}`}
                    />
                  ))}
                </div>
                <div className="text-[10px] font-mono font-bold uppercase tracking-wider mt-1" data-testid="change-pw-strength-label">
                  {strength.label}
                </div>
                <ul className="mt-3 space-y-1 text-xs">
                  {[
                    ["length", "At least 8 characters"],
                    ["upper", "One uppercase letter"],
                    ["lower", "One lowercase letter"],
                    ["digit", "One number"],
                    ["notCommon", "Not a common password"],
                  ].map(([k, label]) => (
                    <li key={k} className={strength.checks[k] ? "text-emerald-700" : "text-zinc-400"}>
                      {strength.checks[k] ? "✓" : "○"} {label}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>

          <div>
            <label className="te-label">Confirm new password</label>
            <input
              type="password"
              autoComplete="new-password"
              required
              className="te-input"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              data-testid="change-pw-confirm"
            />
            {confirmPw.length > 0 && !matches && (
              <div className="text-xs text-red-600 mt-1" data-testid="change-pw-mismatch">
                Passwords do not match
              </div>
            )}
          </div>

          {error && (
            <div className="text-xs font-bold text-red-700" data-testid="change-pw-error">
              {error}
            </div>
          )}
          {success && (
            <div className="text-xs font-bold text-emerald-700" data-testid="change-pw-success">
              {success}
            </div>
          )}

          <button
            type="submit"
            disabled={!canSubmit}
            className="te-btn-primary w-full disabled:opacity-40 disabled:cursor-not-allowed"
            data-testid="change-pw-submit"
          >
            {loading ? "Updating…" : "Update password"}
          </button>

          {forced && (
            <button
              type="button"
              onClick={() => { logout(); navigate("/login"); }}
              className="text-[10px] font-mono font-bold uppercase tracking-wider text-zinc-500 hover:text-black w-full text-center"
              data-testid="change-pw-logout"
            >
              Log out instead
            </button>
          )}
        </form>
      </div>
    </div>
  );
}
