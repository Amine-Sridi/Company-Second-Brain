// Thin fetch wrapper -- the one place that knows how to attach the JWT, how to detect
// session expiry, and how to normalize backend errors. Every other API module (auth.js,
// resumes.js, and future ones for jobs/applications/etc.) calls through this instead of
// using fetch directly. Handles both plain JSON bodies and multipart FormData bodies
// (resume upload).
//
// All paths passed in must be relative ("/v1/...") -- never an absolute URL to another
// service. The browser talks only to RWS; Resume Intelligence, Second Brain, and
// VoiceHR are reached exclusively server-side, from RWS itself. No service API key
// exists anywhere in this codebase -- there is nothing here that could leak one.
import { getToken, clearToken } from "../auth/token.js";

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * @param {string} path - relative API path, e.g. "/v1/auth/login"
 * @param {object} [options]
 * @param {"GET"|"POST"|"PUT"|"PATCH"|"DELETE"} [options.method]
 * @param {object|FormData} [options.body] - a JSON-serializable object, or a FormData
 *   instance for multipart uploads (e.g. resume upload). FormData bodies are passed
 *   through untouched and get no Content-Type header from this wrapper -- the browser
 *   sets it itself, including the multipart boundary; setting it manually would break
 *   the upload.
 * @param {boolean} [options.skipAuth] - don't attach the Authorization header, and
 *   don't treat a 401 as "session expired" (used only by the login call itself, which
 *   has no token yet and whose own 401 means "wrong credentials", not "expired session")
 */
export async function request(path, { method = "GET", body, skipAuth = false } = {}) {
  const isFormData = body instanceof FormData;
  const headers = isFormData ? {} : { "Content-Type": "application/json" };

  if (!skipAuth) {
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : isFormData ? body : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Unable to reach the server. Check your connection and try again.");
  }

  if (response.status === 401 && !skipAuth) {
    clearToken();
    if (window.location.pathname !== "/login") {
      window.location.assign("/login");
    }
    throw new ApiError(401, "Session expired. Please log in again.");
  }

  if (!response.ok) {
    let detail = "Request failed.";
    try {
      const payload = await response.json();
      if (payload && typeof payload.detail === "string") detail = payload.detail;
    } catch {
      // Response body wasn't JSON (or was empty) -- keep the generic message.
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

/**
 * Like request(), but for a binary response (currently only the interview
 * recording proxy) -- returns a Blob instead of parsing JSON. A plain <audio src>
 * can't attach the Authorization header itself, so a caller that wants to play a
 * recording fetches it as a blob here and feeds `URL.createObjectURL(blob)` into
 * the <audio> element instead (see ReportDetailPage) -- the object URL must be
 * revoked by the caller when no longer needed.
 *
 * @param {string} path - relative API path, e.g. "/v1/interviews/{id}/recording"
 * @returns {Promise<Blob>}
 */
export async function requestBlob(path) {
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(path, { headers });
  } catch {
    throw new ApiError(0, "Unable to reach the server. Check your connection and try again.");
  }

  if (response.status === 401) {
    clearToken();
    if (window.location.pathname !== "/login") {
      window.location.assign("/login");
    }
    throw new ApiError(401, "Session expired. Please log in again.");
  }

  if (!response.ok) {
    throw new ApiError(response.status, "Request failed.");
  }

  return response.blob();
}
