import React, { useState } from "react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";

/**
 * Prompt for Google-authenticated users who haven't set a password yet.
 * Renders NOTHING for users who already have one. Non-blocking — user can
 * ignore and keep using Google Sign-in indefinitely.
 */
export default function SetPasswordCard() {
  const { user, refresh } = useAuth();
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [status, setStatus] = useState(""); // "" | "saving" | "ok" | "err:<msg>"

  // Nothing to do if user already has a password (or auth still loading).
  if (!user || user.has_password) return null;

  const submit = async (e) => {
    e.preventDefault();
    if (pw.length < 8) return setStatus("err:Password must be at least 8 characters.");
    if (pw !== confirm) return setStatus("err:Passwords do not match.");
    setStatus("saving");
    try {
      await api.post("/auth/set-password", { new_password: pw });
      setStatus("ok");
      // Refresh /me so this card unmounts on the next render.
      await refresh?.();
      setTimeout(() => setOpen(false), 1200);
    } catch (e) {
      const msg = e?.response?.data?.detail?.message || e?.response?.data?.detail || "Could not set password.";
      setStatus("err:" + msg);
    }
  };

  return (
    <div className="te-card p-6 mb-6 border-l-4 border-[#002FA7]" data-testid="set-password-card">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <div className="te-overline text-[10px] text-[#002FA7] mb-1">Secure your account</div>
          <div className="font-bold text-lg">Add a password</div>
          <p className="text-sm text-zinc-600 mt-1">
            You signed in with Google. Set a password to also log in with email — useful if you ever lose access to your Google account.
          </p>
        </div>
        {!open && (
          <button
            onClick={() => setOpen(true)}
            className="te-btn-primary self-start whitespace-nowrap"
            data-testid="set-password-open"
          >
            Set password
          </button>
        )}
      </div>

      {open && (
        <form onSubmit={submit} className="mt-4 space-y-3" data-testid="set-password-form">
          <input
            type="password"
            className="te-input w-full"
            placeholder="New password (min 8 characters)"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            autoComplete="new-password"
            autoFocus
            data-testid="set-password-input"
          />
          <input
            type="password"
            className="te-input w-full"
            placeholder="Confirm password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            data-testid="set-password-confirm"
          />
          {status.startsWith("err:") && (
            <div className="text-sm text-red-600" data-testid="set-password-error">{status.slice(4)}</div>
          )}
          {status === "ok" && (
            <div className="text-sm text-emerald-700" data-testid="set-password-success">
              Password set. You can now sign in with email and password.
            </div>
          )}
          <div className="flex gap-2">
            <button
              type="submit"
              className="te-btn-primary"
              disabled={status === "saving"}
              data-testid="set-password-submit"
            >
              {status === "saving" ? "Saving…" : "Save password"}
            </button>
            <button
              type="button"
              className="te-btn-outline"
              onClick={() => { setOpen(false); setStatus(""); setPw(""); setConfirm(""); }}
              data-testid="set-password-cancel"
            >
              Later
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
