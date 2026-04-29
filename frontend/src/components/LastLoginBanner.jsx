import React from "react";
import { Link } from "react-router-dom";

function relativeTime(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diff = Math.max(0, Math.floor((now - then) / 1000));
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)} day${Math.floor(diff / 86400) === 1 ? "" : "s"} ago`;
  return new Date(iso).toLocaleDateString();
}

/**
 * Bank-style "last login" banner. Shows the *previous* successful login so an
 * unfamiliar entry stands out (the current session is right now — not a useful
 * signal). If the user has never logged in before, we just stay quiet.
 */
export default function LastLoginBanner({ user }) {
  if (!user?.previous_login_at) return null;
  const when = relativeTime(user.previous_login_at);
  const ip = user.previous_login_ip;
  const exact = new Date(user.previous_login_at).toLocaleString();

  return (
    <div
      className="te-card mt-6 px-4 py-3 flex flex-wrap items-center gap-3 justify-between text-xs"
      data-testid="last-login-banner"
    >
      <div className="flex items-center gap-2">
        <span className="te-overline text-[9px] text-zinc-500">Last sign-in</span>
        <span className="font-mono font-bold" title={exact} data-testid="last-login-when">
          {when}
        </span>
        {ip && (
          <span className="font-mono text-zinc-500">
            · from <span data-testid="last-login-ip">{ip}</span>
          </span>
        )}
      </div>
      <Link
        to="/change-password"
        className="font-mono font-bold uppercase tracking-wider text-[10px] text-[#002FA7] hover:underline"
        data-testid="last-login-not-you"
      >
        Not you? Change password →
      </Link>
    </div>
  );
}
