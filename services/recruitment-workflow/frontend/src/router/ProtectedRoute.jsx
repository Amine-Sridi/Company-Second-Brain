import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";

/**
 * Wraps the authenticated part of the route tree (see AppRouter). Redirects to /login
 * when there is no JWT -- this is the only place that decision is made; individual
 * pages don't each need their own auth check.
 */
export default function ProtectedRoute({ children }) {
  const { isAuthenticated } = useAuth();
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }
  return children;
}
