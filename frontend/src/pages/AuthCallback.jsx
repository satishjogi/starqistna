import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";

// REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
export default function AuthCallback() {
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const hasProcessed = useRef(false);
  const [status, setStatus] = useState("Completing Google sign-in…");
  const [error, setError] = useState("");

  // 2FA challenge state (when account has TOTP)
  const [challenge, setChallenge] = useState(null);
  const [code, setCode] = useState("");
  const [verifying, setVerifying] = useState(false);

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const hash = window.location.hash || "";
    const match = hash.match(/session_id=([^&]+)/);
    if (!match) {
      setError("Missing session. Please try signing in again.");
      return;
    }
    const sessionId = decodeURIComponent(match[1]);

    (async () => {
      try {
        const { data } = await api.post("/auth/google/session", { session_id: sessionId });
        // Clear the hash so refresh doesn't re-process
        window.history.replaceState(null, "", window.location.pathname);
        if (data.requires_2fa) {
          setChallenge(data.challenge_token);
          setStatus("");
        } else {
          localStorage.setItem("te_token", data.access_token);
          await refresh();
          navigate("/dashboard", { replace: true });
        }
      } catch (e) {
        setError(e?.response?.data?.detail || "Google sign-in failed.");
      }
    })();
  }, [navigate, refresh]);

  const verify = async (e) => {
    e.preventDefault();
    setVerifying(true);
    setError("");
    try {
      const { data } = await api.post("/auth/2fa/verify", { challenge_token: challenge, code });
      localStorage.setItem("te_token", data.access_token);
      await refresh();
      navigate("/dashboard", { replace: true });
    } catch (e) {
      setError(e?.response?.data?.detail || "Invalid code");
    } finally {
      setVerifying(false);
    }
  };

  return (
    <div className="px-6 md:px-12 lg:px-20 py-24 flex justify-center" data-testid="auth-callback">
      <div className="w-full max-w-md text-center">
        {!challenge ? (
          <>
            <div className="te-overline mb-2">Access · Google</div>
            <h1 className="text-3xl font-black tracking-tight mb-4">
              {error ? "Sign-in failed" : status}
            </h1>
            {error && (
              <div className="te-card p-6 text-sm text-red-600 font-bold" data-testid="auth-callback-error">
                {error}
                <div className="mt-4">
                  <button
                    onClick={() => navigate("/login", { replace: true })}
                    className="te-btn-primary w-full"
                    data-testid="auth-callback-back"
                  >
                    Back to login
                  </button>
                </div>
              </div>
            )}
          </>
        ) : (
          <>
            <div className="te-overline mb-2">Access · 2FA</div>
            <h1 className="text-3xl font-black tracking-tight mb-2">Verify it's you</h1>
            <p className="text-sm text-zinc-600 mb-6">Your account has 2FA enabled. Enter the 6-digit code from your authenticator app.</p>
            <form onSubmit={verify} className="te-card p-8 space-y-4 text-left" data-testid="google-2fa-form">
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
                  data-testid="google-2fa-code"
                />
              </div>
              {error && <div className="text-xs font-bold text-red-600" data-testid="google-2fa-error">{error}</div>}
              <button
                disabled={verifying || code.length !== 6}
                className="te-btn-primary w-full"
                data-testid="google-2fa-submit"
              >
                {verifying ? "Verifying…" : "Verify & continue"}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
