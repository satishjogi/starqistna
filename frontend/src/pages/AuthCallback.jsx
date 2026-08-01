import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";

// REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
// Handles Google OAuth redirect back to our SPA.
//  1. Reads ?code=... from Google
//  2. Sends it (plus the same redirect_uri we used to initiate) to the backend
//  3. Backend exchanges code for tokens, verifies id_token, upserts user, returns JWT
//  4. Store JWT and redirect to /dashboard, OR if 2FA is required, show the challenge.
export default function AuthCallback() {
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const hasProcessed = useRef(false);
  const [status, setStatus] = useState("Completing Google sign-in…");
  const [error, setError] = useState("");

  // 2FA challenge state (when the linked account has TOTP)
  const [challenge, setChallenge] = useState(null);
  const [code2fa, setCode2fa] = useState("");
  const [verifying, setVerifying] = useState(false);

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const params = new URLSearchParams(window.location.search);
    const oauthCode = params.get("code");
    const oauthError = params.get("error");

    if (oauthError) {
      setError(oauthError === "access_denied"
        ? "Sign-in cancelled. Please try again."
        : `Google returned an error: ${oauthError}`);
      return;
    }
    if (!oauthCode) {
      setError("Missing authorization code. Please try signing in again.");
      return;
    }

    // Must match exactly what GoogleAuthButton sent.
    const redirectUri = window.location.origin + "/auth/google";

    (async () => {
      try {
        const { data } = await api.post("/auth/google/callback", {
          code: oauthCode,
          redirect_uri: redirectUri,
        });
        // Strip ?code=... from history so a refresh doesn't reuse a spent code.
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
      const { data } = await api.post("/auth/2fa/verify", { challenge_token: challenge, code: code2fa });
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
    <div className="px-4 md:px-6 lg:px-10 py-24 flex justify-center" data-testid="auth-callback">
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
            <h1 className="text-3xl font-black tracking-tight mb-2">Verify it&apos;s you</h1>
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
                  value={code2fa}
                  onChange={(e) => setCode2fa(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  data-testid="google-2fa-code"
                />
              </div>
              {error && <div className="text-xs font-bold text-red-600" data-testid="google-2fa-error">{error}</div>}
              <button
                disabled={verifying || code2fa.length !== 6}
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
