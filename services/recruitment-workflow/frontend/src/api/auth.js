import { request } from "./client.js";

/**
 * Calls the real POST /v1/auth/login. Returns { access_token, token_type, expires_at }
 * on success, or throws ApiError with the backend's own detail message (e.g. "Invalid
 * email or password") on failure.
 */
export function login(email, password) {
  return request("/v1/auth/login", {
    method: "POST",
    body: { email, password },
    skipAuth: true,
  });
}
