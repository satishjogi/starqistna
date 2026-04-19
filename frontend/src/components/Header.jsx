import React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

export default function Header() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <header className="sticky top-0 z-50 backdrop-blur-xl bg-white/85 border-b border-black/10">
      <div className="px-4 md:px-6 lg:px-10 h-16 flex items-center justify-between">
        <Link to="/" className="flex items-center gap-3" data-testid="brand-link">
          <img src="/logo.png" alt="Star Qistna" className="h-8 md:h-14 w-auto md:-my-3" />
          <div className="leading-none hidden md:block">
            <div className="text-[9px] tracking-[0.25em] font-mono text-zinc-500">FIRST CLASS MASSAGE COACH</div>
          </div>
        </Link>

        <nav className="hidden md:flex items-center gap-8 text-sm font-semibold">
          <Link to="/" className="hover:text-[#002FA7] transition" data-testid="nav-search">Search</Link>
          {user && <Link to="/dashboard" className="hover:text-[#002FA7] transition" data-testid="nav-dashboard">My Bookings</Link>}
          {user && <Link to="/security" className="hover:text-[#002FA7] transition" data-testid="nav-security">Security</Link>}
          {user?.is_admin && <Link to="/admin" className="hover:text-[#002FA7] transition" data-testid="nav-admin">Admin</Link>}
        </nav>

        <div className="flex items-center gap-3">
          {user ? (
            <>
              <div className="hidden sm:block text-xs font-mono text-zinc-600" data-testid="header-user-email">{user.email}</div>
              <button
                onClick={() => { logout(); navigate("/"); }}
                className="te-btn-outline !py-2 !px-4 text-xs"
                data-testid="logout-btn"
              >
                Log out
              </button>
            </>
          ) : (
            <>
              <Link to="/login" className="text-sm font-semibold hover:text-[#002FA7]" data-testid="header-login-link">Log in</Link>
              <Link to="/register" className="te-btn-primary !py-2 !px-4 text-xs" data-testid="header-register-link">Sign up</Link>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
