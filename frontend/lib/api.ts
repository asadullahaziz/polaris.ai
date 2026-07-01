// Session-cookie API client (implementation_plan §4.1).
//
// Every request sends `credentials: "include"` so the `sessionid` cookie rides
// along; unsafe methods attach the `X-CSRFToken` header read from the
// `csrftoken` cookie. localhost:3000 -> localhost:8000 is same-site, so the
// SameSite=Lax cookies are sent on these cross-origin credentialed requests.

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
export const WS_BASE = process.env.NEXT_PUBLIC_WS_BASE ?? "ws://localhost:8000";

export type User = {
  id: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  is_staff: boolean;
};

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);

function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(
    new RegExp("(^|;\\s*)" + name + "=([^;]*)"),
  );
  return match ? decodeURIComponent(match[2]) : null;
}

export async function apiFetch(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers(options.headers);

  if (!SAFE_METHODS.has(method)) {
    const csrf = getCookie("csrftoken");
    if (csrf) headers.set("X-CSRFToken", csrf);
  }
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  return fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });
}

export async function primeCsrf(): Promise<void> {
  await apiFetch("/api/auth/csrf/");
}

export async function login(username: string, password: string): Promise<User> {
  await primeCsrf(); // ensure the csrftoken cookie exists before the POST
  const res = await apiFetch("/api/auth/login/", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((d) => d.detail as string)
      .catch(() => "Login failed");
    throw new Error(detail ?? "Login failed");
  }
  return res.json();
}

export async function logout(): Promise<void> {
  const res = await apiFetch("/api/auth/logout/", { method: "POST" });
  if (!res.ok) throw new Error("Logout failed");
}

export async function fetchMe(): Promise<User | null> {
  const res = await apiFetch("/api/auth/me/");
  if (res.status === 401 || res.status === 403) return null;
  if (!res.ok) throw new Error("Failed to load current user");
  return res.json();
}
