import { request } from "./client.js";

/**
 * Uploads a resume file to RWS's JWT-protected POST /v1/resumes, which forwards it to
 * Resume Intelligence server-side using RWS's own service API key -- the browser never
 * sees that key or calls Resume Intelligence directly.
 *
 * Returns the real backend response ({ candidate_id, is_new }) untouched. Backend
 * failures (422 unsupported file type, 502 Resume Intelligence/Gemini/Second Brain
 * integration failure, 401 expired session) surface as ApiError via the shared
 * request() wrapper -- nothing here re-validates or re-interprets them.
 *
 * @param {File} file
 * @returns {Promise<{ candidate_id: string, is_new: boolean }>}
 */
export function uploadResume(file) {
  const formData = new FormData();
  formData.append("file", file);
  return request("/v1/resumes", { method: "POST", body: formData });
}
