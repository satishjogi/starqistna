import React, { useState } from "react";
import { Link } from "react-router-dom";

/**
 * TEMPORARY PREVIEW PAGE — /header-preview
 * Three header concepts stacked. Pick one and we'll wire it in for real.
 * Drop this file + the route after the choice is made.
 */

// Sample user shapes for preview
const guestUser = null;
const memberUser = { full_name: "Aisha Tan", email: "aisha.tan@gmail.com", is_admin: false };
const adminUser = { full_name: "Star Qistna Admin", email: "admin@starqistna.com", is_admin: true, role: "super_admin" };

function Frame({ label, accent, children }) {
  return (
    <div className="mb-12">
      <div className="flex items-baseline gap-3 mb-3 px-1">
        <span className={`text-[10px] font-mono font-bold uppercase tracking-wider px-2 py-0.5 ${accent}`}>{label}</span>
      </div>
      <div className="border border-black/10 bg-white">{children}</div>
    </div>
  );
}

// ============================================================
// OPTION A — Minimal centre + Avatar dropdown
// ============================================================
function HeaderA({ user }) {
  const [open, setOpen] = useState(false);
  const initials = (user?.full_name || user?.email || "?").trim().slice(0, 1).toUpperCase();
  return (
    <header className="sticky top-0 z-50 backdrop-blur-xl bg-white/90 border-b border-black/10">
      <div className="px-4 md:px-6 lg:px-10 h-16 flex items-center justify-between">
        <Link to="/" className="flex items-center gap-3">
          <img src="/logo.png" alt="Star Qistna" className="h-8 md:h-12 w-auto md:-my-2" />
          <div className="hidden md:block leading-none">
            <div className="text-[9px] tracking-[0.25em] font-mono text-zinc-500">FIRST CLASS MASSAGE COACH</div>
          </div>
        </Link>

        <nav className="hidden md:flex items-center gap-10 text-sm font-semibold">
          <Link to="/" className="hover:text-[#002FA7] transition">Search</Link>
          <Link to="/feedback" className="hover:text-[#002FA7] transition">Feedback</Link>
        </nav>

        <div className="flex items-center gap-3">
          {!user ? (
            <>
              <Link to="/login" className="text-sm font-semibold hover:text-[#002FA7]">Log in</Link>
              <Link to="/register" className="te-btn-primary !py-2 !px-4 text-xs">Sign up</Link>
            </>
          ) : (
            <div className="relative">
              <button
                type="button"
                onClick={() => setOpen(!open)}
                className="flex items-center gap-2 pl-1 pr-3 py-1 border border-black/10 hover:border-black/40 transition rounded-full"
              >
                <span className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-black text-white ${user.is_admin ? "bg-[#B5121B]" : "bg-[#002FA7]"}`}>
                  {initials}
                </span>
                <span className="text-xs font-semibold hidden sm:inline">{(user.full_name || user.email).split(" ")[0]}</span>
                {user.is_admin && (
                  <span className="hidden sm:inline text-[9px] font-mono font-bold uppercase tracking-widest text-[#B5121B]">Admin</span>
                )}
                <svg className={`w-3 h-3 transition ${open ? "rotate-180" : ""}`} viewBox="0 0 12 12" fill="none">
                  <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
              {open && (
                <div className="absolute right-0 mt-2 w-64 bg-white border border-black/10 shadow-2xl">
                  <div className="px-4 py-3 border-b border-black/10">
                    <div className="font-bold text-sm truncate">{user.full_name}</div>
                    <div className="text-[11px] font-mono text-zinc-500 truncate">{user.email}</div>
                  </div>
                  <Link to="/dashboard" className="block px-4 py-2.5 text-sm hover:bg-zinc-50 flex items-center gap-3">
                    <span className="w-1 h-1 bg-[#002FA7] rounded-full" /> My Bookings
                  </Link>
                  <Link to="/security" className="block px-4 py-2.5 text-sm hover:bg-zinc-50 flex items-center gap-3">
                    <span className="w-1 h-1 bg-zinc-400 rounded-full" /> Security · 2FA
                  </Link>
                  <Link to="/change-password" className="block px-4 py-2.5 text-sm hover:bg-zinc-50 flex items-center gap-3">
                    <span className="w-1 h-1 bg-zinc-400 rounded-full" /> Change password
                  </Link>
                  {user.is_admin && (
                    <>
                      <div className="h-px bg-black/10 my-1" />
                      <Link to="/admin" className="block px-4 py-2.5 text-sm hover:bg-zinc-50 flex items-center gap-3 text-[#B5121B] font-bold">
                        <span className="w-1 h-1 bg-[#B5121B] rounded-full" /> Admin console
                      </Link>
                    </>
                  )}
                  <div className="h-px bg-black/10 my-1" />
                  <button type="button" className="block w-full text-left px-4 py-2.5 text-sm hover:bg-zinc-50 text-zinc-600">
                    Log out
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

// ============================================================
// OPTION B — Bold pill nav + Right CTA + Avatar
// ============================================================
function HeaderB({ user }) {
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-50 bg-white border-b border-black/10">
      <div className="px-4 md:px-6 lg:px-10 h-16 flex items-center justify-between gap-4">
        <Link to="/" className="flex items-center gap-3 shrink-0">
          <img src="/logo.png" alt="Star Qistna" className="h-8 md:h-12 w-auto md:-my-2" />
        </Link>

        <nav className="hidden md:flex items-center gap-1 bg-zinc-100 rounded-full p-1">
          <Link to="/" className="px-4 py-1.5 text-xs font-bold uppercase tracking-wider rounded-full bg-white text-black shadow-sm">Search</Link>
          <Link to="/feedback" className="px-4 py-1.5 text-xs font-bold uppercase tracking-wider text-zinc-600 hover:text-black">Feedback</Link>
          <Link to="/" className="px-4 py-1.5 text-xs font-bold uppercase tracking-wider text-zinc-600 hover:text-black">Routes</Link>
        </nav>

        <div className="flex items-center gap-3">
          {!user ? (
            <>
              <Link to="/login" className="text-sm font-semibold hover:text-[#002FA7] hidden sm:inline">Log in</Link>
              <Link to="/register" className="te-btn-primary !py-2 !px-4 text-xs">Sign up</Link>
            </>
          ) : (
            <>
              <Link to="/" className="te-btn-accent !py-2 !px-4 text-xs hidden sm:inline-block">Book a trip</Link>
              <div className="relative">
                <button type="button" onClick={() => setOpen(!open)} className="w-9 h-9 rounded-full bg-black text-white flex items-center justify-center text-xs font-black hover:scale-105 transition">
                  {(user.full_name || user.email).slice(0, 1).toUpperCase()}
                </button>
                {open && (
                  <div className="absolute right-0 mt-2 w-60 bg-white border border-black/10 shadow-2xl">
                    <div className="px-4 py-3">
                      <div className="font-bold text-sm">{user.full_name}</div>
                      <div className="text-[11px] font-mono text-zinc-500">{user.email}</div>
                      {user.is_admin && (
                        <span className="mt-2 inline-block text-[9px] font-mono font-bold uppercase tracking-widest text-[#B5121B] bg-red-50 px-1.5 py-0.5">{user.role || "admin"}</span>
                      )}
                    </div>
                    <div className="h-px bg-black/10" />
                    <Link to="/dashboard" className="block px-4 py-2.5 text-sm hover:bg-zinc-50">My Bookings</Link>
                    <Link to="/security" className="block px-4 py-2.5 text-sm hover:bg-zinc-50">Security · 2FA</Link>
                    <Link to="/change-password" className="block px-4 py-2.5 text-sm hover:bg-zinc-50">Change password</Link>
                    {user.is_admin && <Link to="/admin" className="block px-4 py-2.5 text-sm hover:bg-zinc-50 text-[#B5121B] font-bold">Admin console</Link>}
                    <div className="h-px bg-black/10" />
                    <button type="button" className="block w-full text-left px-4 py-2.5 text-sm text-zinc-600 hover:bg-zinc-50">Log out</button>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  );
}

// ============================================================
// OPTION C — Editorial / typographic (closest to current vibe)
// ============================================================
function HeaderC({ user }) {
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-50 backdrop-blur-xl bg-white/85 border-b-2 border-black">
      <div className="px-4 md:px-6 lg:px-10 h-[68px] flex items-center justify-between">
        <Link to="/" className="flex items-center gap-3">
          <img src="/logo.png" alt="Star Qistna" className="h-8 md:h-12 w-auto md:-my-2" />
          <div className="hidden md:block leading-none">
            <div className="text-[9px] tracking-[0.25em] font-mono text-zinc-500">FIRST CLASS MASSAGE COACH</div>
          </div>
        </Link>

        <nav className="hidden md:flex items-center gap-7 text-[11px] font-mono font-bold uppercase tracking-[0.2em]">
          <Link to="/" className="hover:text-[#B5121B] transition">Search</Link>
          <span className="text-zinc-300">·</span>
          <Link to="/feedback" className="hover:text-[#B5121B] transition">Feedback</Link>
        </nav>

        <div className="flex items-center gap-4">
          {!user ? (
            <>
              <Link to="/login" className="text-[11px] font-mono font-bold uppercase tracking-widest hover:text-[#B5121B]">Log in</Link>
              <Link to="/register" className="text-[11px] font-mono font-bold uppercase tracking-widest bg-black text-white px-4 py-2 hover:bg-[#B5121B] transition">Sign up</Link>
            </>
          ) : (
            <div className="relative">
              <button
                type="button"
                onClick={() => setOpen(!open)}
                className="flex items-center gap-3 group"
              >
                <div className="text-right hidden sm:block">
                  <div className="text-xs font-bold leading-tight">{(user.full_name || user.email).split(" ")[0]}</div>
                  <div className="text-[9px] font-mono uppercase tracking-widest text-zinc-500 leading-tight">
                    {user.is_admin ? (user.role || "admin").replace("_", "-") : "passenger"}
                  </div>
                </div>
                <span className={`w-9 h-9 flex items-center justify-center text-xs font-black border ${user.is_admin ? "bg-[#B5121B] text-white border-[#B5121B]" : "bg-black text-white border-black"} group-hover:rotate-3 transition`}>
                  {(user.full_name || user.email).slice(0, 1).toUpperCase()}
                </span>
              </button>
              {open && (
                <div className="absolute right-0 mt-2 w-72 bg-white border-2 border-black shadow-[6px_6px_0_0_rgba(0,0,0,0.9)]">
                  <div className="px-5 py-4 bg-zinc-50 border-b-2 border-black">
                    <div className="text-[9px] font-mono uppercase tracking-widest text-zinc-500">Signed in as</div>
                    <div className="font-black mt-0.5">{user.full_name}</div>
                    <div className="text-[11px] font-mono text-zinc-600 truncate">{user.email}</div>
                  </div>
                  <Link to="/dashboard" className="block px-5 py-3 text-sm font-bold uppercase tracking-wider text-[11px] hover:bg-black hover:text-white transition">→ My Bookings</Link>
                  <Link to="/security" className="block px-5 py-3 text-sm font-bold uppercase tracking-wider text-[11px] hover:bg-black hover:text-white transition">→ Security · 2FA</Link>
                  <Link to="/change-password" className="block px-5 py-3 text-sm font-bold uppercase tracking-wider text-[11px] hover:bg-black hover:text-white transition">→ Change password</Link>
                  {user.is_admin && (
                    <Link to="/admin" className="block px-5 py-3 text-sm font-bold uppercase tracking-wider text-[11px] bg-[#B5121B] text-white hover:bg-black transition">→ Admin console</Link>
                  )}
                  <div className="border-t-2 border-black">
                    <button type="button" className="block w-full text-left px-5 py-3 text-[11px] font-mono font-bold uppercase tracking-widest text-zinc-500 hover:bg-zinc-100">Log out</button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

// ============================================================
// Page
// ============================================================
export default function HeaderPreview() {
  return (
    <div className="px-4 md:px-6 lg:px-10 py-10">
      <div className="te-overline mb-2">Preview · Internal</div>
      <h1 className="text-3xl font-black tracking-tight mb-2">Header concepts</h1>
      <p className="text-zinc-600 text-sm max-w-2xl">
        Three takes on the top bar. Each shows three states: guest · signed-in passenger · signed-in admin.
        Click a chip to test the dropdown.
      </p>

      {/* OPTION A */}
      <div className="mt-10">
        <h2 className="text-base font-black uppercase tracking-wider mb-1">A · Minimal + avatar dropdown</h2>
        <p className="text-xs text-zinc-500 mb-4">Public nav cut to 2 items. Account actions live behind a single avatar pill — clean, modern, app-like (Stripe / Linear vibe).</p>
        <Frame label="Guest" accent="bg-zinc-100 text-zinc-600"><HeaderA user={guestUser} /></Frame>
        <Frame label="Passenger" accent="bg-blue-100 text-[#002FA7]"><HeaderA user={memberUser} /></Frame>
        <Frame label="Admin" accent="bg-red-100 text-[#B5121B]"><HeaderA user={adminUser} /></Frame>
      </div>

      {/* OPTION B */}
      <div className="mt-12">
        <h2 className="text-base font-black uppercase tracking-wider mb-1">B · Pill nav + Book-a-trip CTA</h2>
        <p className="text-xs text-zinc-500 mb-4">Adds a always-visible "Book a trip" CTA next to the avatar — best for conversion. Active page indicated by a soft white pill in a grey rail.</p>
        <Frame label="Guest" accent="bg-zinc-100 text-zinc-600"><HeaderB user={guestUser} /></Frame>
        <Frame label="Passenger" accent="bg-blue-100 text-[#002FA7]"><HeaderB user={memberUser} /></Frame>
        <Frame label="Admin" accent="bg-red-100 text-[#B5121B]"><HeaderB user={adminUser} /></Frame>
      </div>

      {/* OPTION C */}
      <div className="mt-12">
        <h2 className="text-base font-black uppercase tracking-wider mb-1">C · Editorial / typographic</h2>
        <p className="text-xs text-zinc-500 mb-4">Stays closest to the current Bauhaus / mono / signal-red brand. Squared avatar, neo-brutalist drop-shadow on the dropdown, role tag under the name.</p>
        <Frame label="Guest" accent="bg-zinc-100 text-zinc-600"><HeaderC user={guestUser} /></Frame>
        <Frame label="Passenger" accent="bg-blue-100 text-[#002FA7]"><HeaderC user={memberUser} /></Frame>
        <Frame label="Admin" accent="bg-red-100 text-[#B5121B]"><HeaderC user={adminUser} /></Frame>
      </div>
    </div>
  );
}
