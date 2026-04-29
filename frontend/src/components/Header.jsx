import React, { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

function getInitials(user) {
  const src = (user?.full_name || user?.email || "?").trim();
  if (user?.full_name) {
    const parts = user.full_name.trim().split(/\s+/);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return src.slice(0, 1).toUpperCase();
}

export default function Header() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const menuRef = useRef(null);

  // Close on outside click + Escape
  useEffect(() => {
    if (!open) return;
    const onClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setOpen(false);
    };
    const onEsc = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const handleLogout = () => {
    setOpen(false);
    logout();
    navigate("/");
  };

  const initials = getInitials(user);
  const firstName = (user?.full_name || user?.email || "").split(/[\s@]/)[0];

  return (
    <header className="sticky top-0 z-50 backdrop-blur-xl bg-white/90 border-b border-black/10">
      <div className="px-4 md:px-6 lg:px-10 h-16 flex items-center justify-between">
        <Link to="/" className="flex items-center gap-3" data-testid="brand-link">
          <img src="/logo.png" alt="Star Qistna" className="h-8 md:h-12 w-auto md:-my-2" />
          <div className="leading-none hidden md:block">
            <div className="text-[9px] tracking-[0.25em] font-mono text-zinc-500">FIRST CLASS MASSAGE COACH</div>
          </div>
        </Link>

        <nav className="hidden md:flex items-center gap-10 text-sm font-semibold">
          <Link to="/" className="hover:text-[#002FA7] transition" data-testid="nav-search">Search</Link>
          <Link to="/feedback" className="hover:text-[#002FA7] transition" data-testid="nav-feedback">Feedback</Link>
        </nav>

        <div className="flex items-center gap-3">
          {!user ? (
            <>
              <Link to="/login" className="text-sm font-semibold hover:text-[#002FA7]" data-testid="header-login-link">Log in</Link>
              <Link to="/register" className="te-btn-primary !py-2 !px-4 text-xs" data-testid="header-register-link">Sign up</Link>
            </>
          ) : (
            <div className="relative" ref={menuRef}>
              <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                aria-haspopup="menu"
                className="flex items-center gap-2 pl-1 pr-3 py-1 border border-black/10 hover:border-black/40 transition rounded-full"
                data-testid="user-menu-trigger"
              >
                <span
                  className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-black text-white ${
                    user.is_admin ? "bg-[#B5121B]" : "bg-[#002FA7]"
                  }`}
                  data-testid="user-menu-avatar"
                >
                  {initials}
                </span>
                <span className="text-xs font-semibold hidden sm:inline" data-testid="user-menu-name">
                  {firstName}
                </span>
                {user.is_admin && (
                  <span className="hidden sm:inline text-[9px] font-mono font-bold uppercase tracking-widest text-[#B5121B]" data-testid="user-menu-role">
                    Admin
                  </span>
                )}
                <svg className={`w-3 h-3 transition ${open ? "rotate-180" : ""}`} viewBox="0 0 12 12" fill="none" aria-hidden="true">
                  <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>

              {open && (
                <div
                  role="menu"
                  className="absolute right-0 mt-2 w-64 bg-white border border-black/10 shadow-2xl"
                  data-testid="user-menu-dropdown"
                >
                  <div className="px-4 py-3 border-b border-black/10">
                    <div className="font-bold text-sm truncate" data-testid="user-menu-fullname">
                      {user.full_name || firstName}
                    </div>
                    <div className="text-[11px] font-mono text-zinc-500 truncate" data-testid="user-menu-email">
                      {user.email}
                    </div>
                  </div>

                  <Link
                    to="/dashboard"
                    onClick={() => setOpen(false)}
                    role="menuitem"
                    className="px-4 py-2.5 text-sm hover:bg-zinc-50 flex items-center gap-3"
                    data-testid="user-menu-bookings"
                  >
                    <span className="w-1 h-1 bg-[#002FA7] rounded-full" /> My Bookings
                  </Link>
                  <Link
                    to="/security"
                    onClick={() => setOpen(false)}
                    role="menuitem"
                    className="px-4 py-2.5 text-sm hover:bg-zinc-50 flex items-center gap-3"
                    data-testid="user-menu-security"
                  >
                    <span className="w-1 h-1 bg-zinc-400 rounded-full" /> Security · 2FA
                  </Link>
                  <Link
                    to="/change-password"
                    onClick={() => setOpen(false)}
                    role="menuitem"
                    className="px-4 py-2.5 text-sm hover:bg-zinc-50 flex items-center gap-3"
                    data-testid="user-menu-change-password"
                  >
                    <span className="w-1 h-1 bg-zinc-400 rounded-full" /> Change password
                  </Link>

                  {user.is_admin && (
                    <>
                      <div className="h-px bg-black/10 my-1" />
                      <Link
                        to="/admin"
                        onClick={() => setOpen(false)}
                        role="menuitem"
                        className="px-4 py-2.5 text-sm hover:bg-zinc-50 flex items-center gap-3 text-[#B5121B] font-bold"
                        data-testid="user-menu-admin"
                      >
                        <span className="w-1 h-1 bg-[#B5121B] rounded-full" /> Admin console
                      </Link>
                    </>
                  )}

                  <div className="h-px bg-black/10 my-1" />
                  <button
                    type="button"
                    onClick={handleLogout}
                    role="menuitem"
                    className="w-full text-left px-4 py-2.5 text-sm hover:bg-zinc-50 text-zinc-600"
                    data-testid="user-menu-logout"
                  >
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
