// Plain JS JWT storage/decoding -- no React here on purpose, so it can be called from
// anywhere (including the API client, outside any component) without a hook.
//
// sessionStorage, not localStorage: the token is gone when the tab closes, which is the
// right default for a recruiter demo session. This never stores anything but the JWT
// itself -- no service API key is ever written here or anywhere else in this app; the
// browser never receives one in the first place.

const TOKEN_KEY = "rws_jwt";

export function getToken() {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  sessionStorage.removeItem(TOKEN_KEY);
}

/**
 * Decodes a JWT's payload for display purposes only (e.g. showing the recruiter's
 * email in the top bar). This is NOT verification -- the signature is never checked
 * client-side, and nothing here is used to make an authorization decision; the backend
 * is the only thing that ever verifies the token.
 */
export function decodeJwt(token) {
  if (!token) return null;
  try {
    const base64Url = token.split(".")[1];
    const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
    const jsonPayload = decodeURIComponent(
      atob(base64)
        .split("")
        .map((c) => "%" + c.charCodeAt(0).toString(16).padStart(2, "0"))
        .join(""),
    );
    return JSON.parse(jsonPayload);
  } catch {
    return null;
  }
}
