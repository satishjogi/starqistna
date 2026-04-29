import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

// Routes a forced-password-change user can still visit. Everything else
// redirects them back to /change-password.
const ALLOWED_PATHS = new Set(["/change-password", "/login", "/logout", "/forgot-password", "/reset-password"]);

/**
 * Sits at the top of the router. When the authenticated user has
 * `must_change_password`, they're locked into /change-password (with the
 * usual escape hatch of logging out) until they pick a new password.
 */
export default function ForcePasswordChangeGate() {
  const { user, loading } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    if (loading) return;
    if (!user?.must_change_password) return;
    if (ALLOWED_PATHS.has(location.pathname)) return;
    navigate("/change-password", { replace: true });
  }, [user, loading, location.pathname, navigate]);

  return null;
}
