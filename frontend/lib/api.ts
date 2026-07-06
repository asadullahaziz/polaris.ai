// Session-cookie API client for the v2 backend.
//
// Every request sends `credentials: "include"` so the `sessionid` cookie rides
// along; unsafe methods attach the `X-CSRFToken` header read from the
// `csrftoken` cookie. All shapes mirror the backend serializers / dict
// serialization exactly (snake_case, bare arrays — no pagination envelope).

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
export const WS_BASE = process.env.NEXT_PUBLIC_WS_BASE ?? "ws://localhost:8000";

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

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res
      .json()
      .then((d) => (d.detail as string) || (d.error as string))
      .catch(() => null);
    throw new ApiError(res.status, detail || `${res.status} ${res.statusText}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ---- Auth / me ----------------------------------------------------------------
export type Profile = {
  preferences: Record<string, unknown>;
  bio: string;
  company: string;
  avatar_url: string;
  auto_reply_when_away: boolean;
  agent_autonomy: "draft_for_approval" | "auto_send";
  agent_reply_cap: number;
  agent_instructions: string;
};

export type User = {
  id: number;
  email: string;
  full_name: string;
  phone: string;
  preferred_channel: "in_app" | "sms" | "email" | "whatsapp";
  is_email_verified: boolean;
  is_staff: boolean;
  date_joined: string;
  profile: Profile;
};

export type ProfilePatch = Partial<
  Pick<User, "full_name" | "phone" | "preferred_channel"> &
    Omit<Profile, "preferences"> & { preferences: Record<string, unknown> }
>;

export const primeCsrf = () => apiFetch("/api/auth/csrf/");

export async function login(email: string, password: string): Promise<User> {
  await primeCsrf(); // ensure the csrftoken cookie exists before the POST
  return apiFetch("/api/auth/login/", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  }).then((r) => json<User>(r));
}

export const logout = () =>
  apiFetch("/api/auth/logout/", { method: "POST" }).then((r) => json<void>(r));

export async function fetchMe(): Promise<User | null> {
  const res = await apiFetch("/api/auth/me/");
  if (res.status === 401 || res.status === 403) return null;
  return json<User>(res);
}

export const patchMe = (body: ProfilePatch) =>
  apiFetch("/api/auth/me/", {
    method: "PATCH",
    body: JSON.stringify(body),
  }).then((r) => json<User>(r));

export async function register(
  email: string,
  password: string,
  full_name?: string,
): Promise<{ detail: string }> {
  await primeCsrf();
  return apiFetch("/api/auth/register/", {
    method: "POST",
    body: JSON.stringify({ email, password, full_name }),
  }).then((r) => json<{ detail: string }>(r));
}

export const verifyEmail = (token: string) =>
  apiFetch("/api/auth/verify/", {
    method: "POST",
    body: JSON.stringify({ token }),
  }).then((r) => json<{ detail: string }>(r));

export const resendVerification = (email: string) =>
  apiFetch("/api/auth/resend/", {
    method: "POST",
    body: JSON.stringify({ email }),
  }).then((r) => json<{ detail: string }>(r));

export const changePassword = (current_password: string, new_password: string) =>
  apiFetch("/api/auth/password/change/", {
    method: "POST",
    body: JSON.stringify({ current_password, new_password }),
  }).then((r) => json<{ detail: string }>(r));

export async function requestPasswordReset(email: string) {
  await primeCsrf();
  return apiFetch("/api/auth/password/reset/", {
    method: "POST",
    body: JSON.stringify({ email }),
  }).then((r) => json<{ detail: string }>(r));
}

export async function confirmPasswordReset(token: string, new_password: string) {
  await primeCsrf();
  return apiFetch("/api/auth/password/reset/confirm/", {
    method: "POST",
    body: JSON.stringify({ token, new_password }),
  }).then((r) => json<{ detail: string }>(r));
}

// ---- Catalog: properties + listings ---------------------------------------------
export type Property = {
  id: number;
  address_raw: string;
  property_type: string | null;
  beds: number | null;
  baths: number | null;
  sqft: number | null;
  lot_size_sqft: number | null;
  year_built: number | null;
  condition: number | null;
  grade: number | null;
  waterfront: boolean;
};

export type PropertyLookup =
  | { found: false; normalized: string }
  | { found: true; normalized: string; property: Property & { address_norm: string } };

export const lookupProperty = (address: string) =>
  apiFetch(`/api/properties/lookup?address=${encodeURIComponent(address)}`).then(
    (r) => json<PropertyLookup>(r),
  );

export type PropertySearchResult = Property & {
  address_norm: string;
  last_sale_price: number | null;
  last_sale_date: string | null;
};

export const searchProperties = (q: string, limit = 8) =>
  apiFetch(
    `/api/properties/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  ).then((r) => json<{ results: PropertySearchResult[] }>(r));

export type ListingSummary = {
  id: number;
  title: string;
  status: string;
  bundle_type: "single" | "package" | "portfolio";
  asking_price: string | number | null;
  created_at: string;
  primary_property: Property | null;
  cover_url: string | null;
};

export type MandateShape =
  | { exists: false }
  | {
      exists: true;
      floor_price: number | null;
      ceiling_price: number | null;
      must_haves: string[];
      availability_window: string;
      instructions: string;
    };

export type ListingDetail = {
  id: number;
  title: string;
  description: string;
  status: string;
  bundle_type: "single" | "package" | "portfolio";
  asking_price: string | number | null;
  created_at: string;
  updated_at: string;
  properties: {
    property: Property;
    asking_price: string | number | null;
    sort_order: number;
  }[];
  media: { id: number; kind: string; url: string; sort_order: number }[];
  mandate: MandateShape;
};

export type PropertyItemInput = {
  property_id?: number;
  address?: string;
  property_type?: string;
  beds?: number;
  baths?: number;
  sqft?: number;
  lot_size_sqft?: number;
  year_built?: number;
  condition?: number;
  grade?: number;
  waterfront?: boolean;
  asking_price?: number;
  sort_order?: number;
};

export type MandateInput = {
  floor_price?: number | null;
  ceiling_price?: number | null;
  must_haves?: string[];
  availability_window?: string;
  instructions?: string;
};

export type ListingCreateInput = {
  title?: string;
  description?: string;
  asking_price?: number;
  bundle_type?: "single" | "package" | "portfolio";
  status?: string;
  properties: PropertyItemInput[];
  media?: { kind?: "photo" | "document"; url: string; sort_order?: number }[];
  mandate?: MandateInput;
};

export const listListings = () =>
  apiFetch("/api/listings/").then((r) => json<ListingSummary[]>(r));

export const getListing = (id: number) =>
  apiFetch(`/api/listings/${id}/`).then((r) => json<ListingDetail>(r));

export const createListing = (body: ListingCreateInput) =>
  apiFetch("/api/listings/", {
    method: "POST",
    body: JSON.stringify(body),
  }).then((r) => json<ListingDetail>(r));

export const updateListing = (
  id: number,
  body: Partial<Omit<ListingCreateInput, "properties" | "media">>,
) =>
  apiFetch(`/api/listings/${id}/`, {
    method: "PATCH",
    body: JSON.stringify(body),
  }).then((r) => json<ListingDetail>(r));

export type Comp = {
  id: number;
  address: string;
  beds: number | null;
  baths: number | null;
  sqft: number | null;
  grade: number | null;
  condition: number | null;
  waterfront: boolean;
  price: number | null;
  sold_on: string | null;
  ppsf: number | null;
  distance_mi: number | null;
};

export type Valuation = {
  low: number | null;
  point: number | null;
  high: number | null;
  basis: {
    n_comps: number;
    radius_mi: number;
    relaxed: string;
    arv: boolean;
    met_min_n: boolean;
    ppsf_low?: number;
    ppsf_median?: number;
    ppsf_high?: number;
  };
  comps: Comp[];
};

export const getValuation = (id: number, arv = false) =>
  apiFetch(`/api/listings/${id}/valuation/?arv=${arv ? 1 : 0}`).then((r) =>
    json<Valuation>(r),
  );

export const getListingMandate = (id: number) =>
  apiFetch(`/api/listings/${id}/mandate/`).then((r) => json<MandateShape>(r));

export const setListingMandate = (id: number, body: MandateInput) =>
  apiFetch(`/api/listings/${id}/mandate/`, {
    method: "PUT",
    body: JSON.stringify(body),
  }).then((r) => json<MandateShape>(r));

// ---- Buy-boxes -------------------------------------------------------------------
export type BuyBoxGeo = {
  id: number;
  geo_type: string;
  mode: string;
  state_code: string;
  county_fips: string;
  city: string;
  zip: string;
  radius_mi: number | null;
  center_lat: number | null;
  center_lon: number | null;
};

export type BuyBox = {
  buy_box_id: number;
  name: string;
  strategy: string;
  is_primary: boolean;
  is_active: boolean;
  price_min: number | null;
  price_max: number | null;
  arv_min: number | null;
  arv_max: number | null;
  beds_min: number | null;
  baths_min: number | null;
  sqft_min: number | null;
  sqft_max: number | null;
  year_built_min: number | null;
  max_rehab_cost: number | null;
  property_types: string[];
  geos: BuyBoxGeo[];
  n_geos: number;
  mandate: {
    ceiling_price: number | null;
    must_haves: string[];
    instructions: string;
  } | null;
};

export type BuyBoxInput = Partial<{
  name: string;
  strategy: string;
  is_primary: boolean;
  is_active: boolean;
  price_min: number | null;
  price_max: number | null;
  arv_min: number | null;
  arv_max: number | null;
  beds_min: number | null;
  baths_min: number | null;
  sqft_min: number | null;
  sqft_max: number | null;
  year_built_min: number | null;
  max_rehab_cost: number | null;
  property_types: string[];
  ceiling_price: number | null;
  must_haves: string[];
  instructions: string;
  geo: {
    geo_type: string;
    mode?: string;
    center_lat?: number;
    center_lon?: number;
    radius_mi?: number;
    state_code?: string;
    county_fips?: string;
    city?: string;
    zip?: string;
  };
}>;

export const listBuyBoxes = () =>
  apiFetch("/api/buy-boxes/").then((r) => json<BuyBox[]>(r));

export const createBuyBox = (body: BuyBoxInput) =>
  apiFetch("/api/buy-boxes/", {
    method: "POST",
    body: JSON.stringify(body),
  }).then((r) => json<BuyBox>(r));

export const updateBuyBox = (id: number, body: BuyBoxInput) =>
  apiFetch(`/api/buy-boxes/${id}/`, {
    method: "PATCH",
    body: JSON.stringify(body),
  }).then((r) => json<BuyBox>(r));

export const deleteBuyBox = (id: number) =>
  apiFetch(`/api/buy-boxes/${id}/`, { method: "DELETE" }).then((r) =>
    json<{ deleted: boolean; buy_box_id: number }>(r),
  );

// ---- Buyer ranking (the /buyers ad-hoc matcher) ------------------------------------
export type RankedBuyer = {
  user_id: number;
  name: string;
  score: number;
  reason: string;
  features: Record<string, number>;
  buy_box_completeness: number;
  n_purchases: number;
  n_nearby: number;
  cash: boolean;
};

export type BuyerRankResult = {
  ranked: RankedBuyer[];
  n_candidates: number;
  radius_mi?: number;
  weights?: Record<string, number>;
  note?: string;
  resolved: boolean;
};

export const rankBuyers = (params: {
  address: string;
  price?: number;
  beds?: number;
  sqft?: number;
  condition?: number;
  property_type?: string;
  limit?: number;
}) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  return apiFetch(`/api/buyers/rank?${q}`).then((r) => json<BuyerRankResult>(r));
};

// ---- AI copilot (AiChat) ------------------------------------------------------------
export type AiChatSummary = {
  id: number;
  title: string | null;
  status: "open" | "archived";
  created_at: string;
  updated_at: string;
};

export type AiMessage = {
  id: number;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  // Set on a resolved/expired confirm row (role='tool'): the card payload + resolution.
  tool_calls?: Record<string, unknown> | null;
  created_at: string;
};

// The retrieve view: transcript + any parked confirm-every-write awaiting approval.
// `pending_confirm` is the ConfirmPayload the card renders (cast at the call site), or
// null. Non-null means a write is paused and the composer should stay gated on reopen.
export type AiChatDetail = AiChatSummary & {
  messages: AiMessage[];
  pending_confirm: Record<string, unknown> | null;
};

export const listAiChats = () =>
  apiFetch("/api/ai/chats/").then((r) => json<AiChatSummary[]>(r));

export const getAiChat = (id: number) =>
  apiFetch(`/api/ai/chats/${id}/`).then((r) => json<AiChatDetail>(r));

export const getAiChatMessages = (id: number) =>
  apiFetch(`/api/ai/chats/${id}/messages/`).then((r) => json<AiMessage[]>(r));

export const deleteAiChat = (id: number) =>
  apiFetch(`/api/ai/chats/${id}/`, { method: "DELETE" }).then((r) => json<void>(r));

// ---- Outreach campaigns ---------------------------------------------------------------
export type OutreachRecipient = {
  id: number;
  recipient_user: number;
  name: string;
  rank_score: string | null;
  rank_reason: string;
  draft_body: string;
  status: "pending" | "sent" | "skipped_already_contacted" | "failed" | "cancelled";
  chat_id: number | null;
};

export type OutreachCampaign = {
  id: number;
  listing: number;
  listing_address: string | null;
  copilot_ai_chat: number | null;
  status: "awaiting_approval" | "sending" | "done" | "cancelled";
  created_at: string;
  recipients: OutreachRecipient[];
};

export const listCampaigns = () =>
  apiFetch("/api/ai/outreach/campaigns/").then((r) => json<OutreachCampaign[]>(r));

export const approveCampaign = (id: number) =>
  apiFetch(`/api/ai/outreach/campaigns/${id}/approve/`, {
    method: "POST",
    body: "{}",
  }).then((r) => json<Record<string, unknown>>(r));

export const cancelCampaign = (id: number) =>
  apiFetch(`/api/ai/outreach/campaigns/${id}/cancel/`, {
    method: "POST",
    body: "{}",
  }).then((r) => json<Record<string, unknown>>(r));

// ---- Human chat -------------------------------------------------------------------------
export type ListingBrief = {
  listing_id: number;
  title: string;
  status: string;
  asking_price: number | null;
  address: string | null;
};

export type MessageAttachment = {
  id: number;
  kind: "listing" | "file" | "photo";
  listing_id: number | null;
  listing: ListingBrief | null;
  sort_order: number;
};

export type ChatMessage = {
  id: number;
  chat_id: number;
  kind: "human" | "agent" | "system";
  sender: number | null;
  action: string | null;
  body: string;
  status: "sent" | "draft";
  reply_to: number | null;
  created_at: string;
  attachments: MessageAttachment[];
};

export type ChatRow = {
  id: number;
  counterparty: { id: number; name: string; avatar_url: string } | null;
  status: "open" | "paused" | "escalated" | "closed";
  terminal: "matched" | "no_fit" | "needs_decision" | null;
  updated_at: string;
  unread: boolean;
  last_message: {
    body: string;
    kind: string;
    sender: number | null;
    action: string | null;
    created_at: string;
  } | null;
};

export const listChats = () =>
  apiFetch("/api/chats/").then((r) => json<ChatRow[]>(r));

export const getChat = (id: number) =>
  apiFetch(`/api/chats/${id}/`).then((r) => json<ChatRow>(r));

export const openChatWith = (body: {
  counterparty_id: number;
  body?: string;
  attachment_listing_ids?: number[];
  listing_id?: number;
}) =>
  apiFetch("/api/chats/", {
    method: "POST",
    body: JSON.stringify(body),
  }).then((r) => json<ChatRow>(r));

export const getChatMessages = (id: number) =>
  apiFetch(`/api/chats/${id}/messages/`).then((r) => json<ChatMessage[]>(r));

export const sendChatMessage = (
  id: number,
  body: { body?: string; attachment_listing_ids?: number[]; client_dedup_uuid?: string },
) =>
  apiFetch(`/api/chats/${id}/messages/`, {
    method: "POST",
    body: JSON.stringify(body),
  }).then((r) => json<ChatMessage & { duplicate?: boolean }>(r));

export const markChatRead = (id: number) =>
  apiFetch(`/api/chats/${id}/read/`, { method: "POST", body: "{}" }).then((r) =>
    json<{ status: string }>(r),
  );

export const approveChatDraft = (chatId: number, message_id: number) =>
  apiFetch(`/api/chats/${chatId}/approve-draft/`, {
    method: "POST",
    body: JSON.stringify({ message_id }),
  }).then((r) =>
    json<{ status: string; message_id: number; chat_id: number; body: string; action: string | null }>(r),
  );

export const discardChatDraft = (chatId: number, message_id: number) =>
  apiFetch(`/api/chats/${chatId}/discard-draft/`, {
    method: "POST",
    body: JSON.stringify({ message_id }),
  }).then((r) => json<{ status: string; message_id: number }>(r));

// ---- Notifications -------------------------------------------------------------------
export type Notification = {
  id: number;
  type: "inbound_message" | "outreach_received" | "approval_required" | "escalation";
  chat: number | null;
  payload: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
};

export const listNotifications = () =>
  apiFetch("/api/notifications/").then((r) => json<Notification[]>(r));

export const readNotification = (id: number) =>
  apiFetch(`/api/notifications/${id}/read/`, { method: "POST", body: "{}" });

export const readAllNotifications = () =>
  apiFetch("/api/notifications/read-all/", { method: "POST", body: "{}" });
