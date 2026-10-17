// Backend calls. The UI talks to the backend ONLY through these three functions.
// They follow the team schema in supabase/migrations (see supabase/README.md, "Front (Person 1)"):
// phone sign-in with Supabase Phone OTP (Twilio is the SMS provider, set in the Supabase dashboard),
// then the signed-in user updates their own profile and their 4 envelopes, and gets a connector key.

// The publishable key is meant to live in the browser: Row Level Security limits each user to their own rows.
const SUPABASE_URL = "https://ucsysxhiydfgukkkbtdx.supabase.co";
const SUPABASE_PUBLISHABLE_KEY = "sb_publishable_kH8DQml2zIAyghryfM1uOA_CJVJFF7J";

// The MCP server's public HTTPS address, no trailing slash. Verified working end to end
// against this Supabase project.
//
// NOTE: this is a quick tunnel, so it changes whenever the tunnel restarts. If connector
// links stop working, that is the first thing to check — ask for the current URL.
// It is replaced by a stable domain before the demo.
const MCP_BASE_URL = "https://knowing-converter-ion-groove.trycloudflare.com";

const SESSION_KEY = "eevee-session";

// One call to Supabase. Server errors throw; other answers come back as { ok, status, data }.
async function callSupabase(path, { method = "POST", body = {}, token = null, prefer = null } = {}) {
  const headers = { apikey: SUPABASE_PUBLISHABLE_KEY, "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (prefer) headers.Prefer = prefer;
  const response = await fetch(`${SUPABASE_URL}${path}`, { method, headers, body: JSON.stringify(body) });
  const text = await response.text();
  if (response.status >= 500) throw new Error(`Supabase ${path} failed (${response.status}): ${text}`);
  return { ok: response.ok, status: response.status, data: text ? JSON.parse(text) : null };
}

// The signed-in user: { accessToken, userId }. Also kept for the tab so a reload doesn't sign them out.
let session = readSession();

function readSession() {
  try {
    return JSON.parse(sessionStorage.getItem(SESSION_KEY));
  } catch (error) {
    return null;
  }
}

function writeSession(value) {
  session = value;
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(value));
  } catch (error) {
    // Storage blocked: the session lasts until reload.
  }
}

// Updates exactly one of the user's rows. Row Level Security turns a wrong filter into "0 rows", so check it.
async function updateOwnRow(path, values, token) {
  const { ok, data } = await callSupabase(path, { method: "PATCH", body: values, token, prefer: "return=representation" });
  if (!ok || !Array.isArray(data) || data.length !== 1) throw new Error(`Update ${path} did not change exactly one row`);
}

const toCents = (dollars) => Math.round(dollars * 100);

// Texts a 6-digit code to the phone (E.164). Returns false if Supabase refuses the number.
async function sendCode(phone) {
  const { ok } = await callSupabase("/auth/v1/otp", { body: { phone } });
  return ok;
}

// Checks the code and signs the user in. Their profile and 4 envelopes are created on first sign-in.
async function verifyCode(phone, code) {
  const { ok, data } = await callSupabase("/auth/v1/verify", { body: { type: "sms", phone, token: code } });
  if (!ok) return false;
  writeSession({ accessToken: data.access_token, userId: data.user.id });
  return true;
}

// Saves the allowance and envelope limits (dollars in, cents in the database) and returns the connector link.
// The updates are independent and idempotent, so they run together and a retry after a failure is safe.
// The phone in `data` is not written: the signed-in session already identifies the user.
async function saveSetup(data) {
  if (!session) throw new Error("saveSetup: nobody is signed in");
  const { accessToken: token, userId } = session;

  await Promise.all([
    updateOwnRow(`/rest/v1/profiles?id=eq.${userId}`, { weekly_allowance_cents: toCents(data.weekly_budget) }, token),
    ...data.envelopes.map((envelope) => updateOwnRow(
      `/rest/v1/envelopes?user_id=eq.${userId}&category=eq.${envelope.name}`,
      { weekly_limit_cents: toCents(envelope.weekly_limit), auto_approve_under_cents: toCents(envelope.auto_approve_under) },
      token,
    )),
  ]);

  // A new key each time; the previous one stops working.
  const { ok, data: key } = await callSupabase("/rest/v1/rpc/rotate_connector_key", { token });
  if (!ok) throw new Error("rotate_connector_key failed");
  return `${MCP_BASE_URL}/mcp/${key}`;
}
