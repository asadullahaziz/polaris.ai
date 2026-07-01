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
  full_name: string | null;
  preferred_channel: string;
  preferences: Record<string, unknown>;
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

// ---- Copilot conversations (P1.2) -------------------------------------------
export type Conversation = {
  id: number;
  title: string | null;
  status: string;
  updated_at: string;
};

export type Message = {
  id: number;
  author_type: "human" | "agent" | "system";
  body: string;
  created_at: string;
};

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const listConversations = () =>
  apiFetch("/api/copilot/conversations/").then((r) => json<Conversation[]>(r));

export const createConversation = () =>
  apiFetch("/api/copilot/conversations/", { method: "POST", body: "{}" }).then((r) =>
    json<Conversation>(r),
  );

export const renameConversation = (id: number, title: string) =>
  apiFetch(`/api/copilot/conversations/${id}/`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  }).then((r) => json<Conversation>(r));

export const deleteConversation = (id: number) =>
  apiFetch(`/api/copilot/conversations/${id}/`, { method: "DELETE" });

export const loadMessages = (id: number) =>
  apiFetch(`/api/copilot/conversations/${id}/messages/`).then((r) => json<Message[]>(r));

// ---- Listings + valuation (P1.2 / P1.11) ------------------------------------
export type Listing = {
  id: number;
  status: string;
  asking_price: number | null;
  property: {
    address_raw: string;
    beds: number | null;
    sqft: number | null;
    baths: number | null;
    condition: number | null;
  } | null;
};

export type Comp = {
  id: number;
  address: string;
  beds: number | null;
  sqft: number | null;
  grade: number | null;
  price: number | null;
  ppsf: number | null;
  sold_on: string | null;
  distance_mi: number | null;
};

export type Valuation = {
  low: number | null;
  point: number | null;
  high: number | null;
  basis: Record<string, unknown>;
  comps: Comp[];
  subject?: Record<string, unknown>;
};

export const listListings = () =>
  apiFetch("/api/listings/").then((r) => json<Listing[]>(r));

export const getValuation = (id: number, arv = false) =>
  apiFetch(`/api/listings/${id}/valuation/?arv=${arv ? 1 : 0}`).then((r) =>
    json<Valuation>(r),
  );

// ---- Shared context store (P1.10) -------------------------------------------
export type Memory = { id: number; namespace: string; content: string };

export const listMemory = () =>
  apiFetch("/api/context/memory/").then((r) => json<Memory[]>(r));

export const addMemory = (content: string, namespace = "general") =>
  apiFetch("/api/context/memory/", {
    method: "POST",
    body: JSON.stringify({ content, namespace }),
  }).then((r) => json<Memory>(r));

export const getPreferences = () =>
  apiFetch("/api/auth/preferences/").then((r) => json<Record<string, unknown>>(r));

// ---- Outreach campaigns (P2.7) ----------------------------------------------
export type OutreachRecipient = {
  id: number;
  name: string;
  kind: "registered" | "prospect";
  rank_score: string | null;
  rank_reason: string | null;
  draft_body: string | null;
  status: string;
  conversation_id: number | null;
};

export type OutreachCampaign = {
  id: number;
  listing: number;
  listing_address: string | null;
  copilot_conversation: number | null;
  status: string;
  created_at: string;
  recipients: OutreachRecipient[];
};

export const listCampaigns = () =>
  apiFetch("/api/outreach/campaigns/").then((r) => json<OutreachCampaign[]>(r));

export const approveCampaign = (id: number) =>
  apiFetch(`/api/outreach/campaigns/${id}/approve/`, { method: "POST", body: "{}" }).then((r) =>
    json<Record<string, unknown>>(r),
  );

export const cancelCampaign = (id: number) =>
  apiFetch(`/api/outreach/campaigns/${id}/cancel/`, { method: "POST", body: "{}" }).then((r) =>
    json<Record<string, unknown>>(r),
  );

// ---- Shared threads + auto-responder (P3) -----------------------------------
export type ThreadListItem = {
  id: number;
  listing_id: number | null;
  listing_address: string | null;
  my_side: "buyer" | "seller";
  counterparty_name: string;
  counterparty_kind: string;
  status: string;
  terminal: string | null;
  updated_at: string;
  last_message: {
    body: string;
    author_type: string;
    author_side: string | null;
    created_at: string;
  } | null;
};

export type ThreadMessage = {
  id: number;
  author_type: "human" | "agent" | "system";
  author_side: "buyer" | "seller" | null;
  action: string | null;
  body: string;
  status: "sent" | "draft";
  created_at: string;
};

export type ThreadMandate = {
  side?: "buyer" | "seller";
  has_mandate?: boolean;
  auto_reply?: boolean | null;
  autonomy?: string | null;
  instructions?: string | null;
  error?: string;
};

export type Notification = {
  id: number;
  type: string;
  conversation: number | null;
  payload: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
};

export const listThreads = () =>
  apiFetch("/api/threads/").then((r) => json<ThreadListItem[]>(r));

export const getThreadMessages = (id: number) =>
  apiFetch(`/api/threads/${id}/messages/`).then((r) => json<ThreadMessage[]>(r));

export const getThreadMandate = (id: number) =>
  apiFetch(`/api/threads/${id}/mandate/`).then((r) => json<ThreadMandate>(r));

export const setThreadMandate = (id: number, body: Partial<ThreadMandate>) =>
  apiFetch(`/api/threads/${id}/mandate/`, {
    method: "PUT",
    body: JSON.stringify(body),
  }).then((r) => json<ThreadMandate>(r));

export const approveThreadDraft = (id: number, message_id: number) =>
  apiFetch(`/api/threads/${id}/approve-draft/`, {
    method: "POST",
    body: JSON.stringify({ message_id }),
  }).then((r) => json<Record<string, unknown>>(r));

export const listNotifications = () =>
  apiFetch("/api/notifications/").then((r) => json<Notification[]>(r));

export const readAllNotifications = () =>
  apiFetch("/api/notifications/read-all/", { method: "POST", body: "{}" });
