import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { clearToken, decodeJwt, getToken, setToken } from "../auth/token.js";
import { login as loginRequest } from "../api/auth.js";

const AuthContext = createContext(null);

function recruiterFromToken(token) {
  const payload = decodeJwt(token);
  if (!payload) return null;
  return { email: payload.email, recruiterId: payload.sub };
}

export function AuthProvider({ children }) {
  const [token, setTokenState] = useState(() => getToken());
  const [recruiter, setRecruiter] = useState(() => recruiterFromToken(getToken()));

  const login = useCallback(async (email, password) => {
    const result = await loginRequest(email, password);
    setToken(result.access_token);
    setTokenState(result.access_token);
    setRecruiter(recruiterFromToken(result.access_token));
    return result;
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setTokenState(null);
    setRecruiter(null);
  }, []);

  const value = useMemo(
    () => ({
      recruiter,
      isAuthenticated: Boolean(token),
      login,
      logout,
    }),
    [recruiter, token, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
