# Frontend

Landing page and the 4 setup screens (verify phone, weekly allowance, envelopes, connect Claude). Plain HTML/CSS/JS, no build step.

## Run

Open `index.html` in a browser, or run `npx serve frontend` from the repo root.

## Configuration

**`config.js` is the only file to edit when an address changes.** It holds `MCP_BASE_URL`,
`SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`; `index.html` loads it before `api.js`.

`MCP_BASE_URL` is currently a cloudflared quick tunnel, which **gets a new address every
time it restarts**. When connector links stop working, that is almost always why — ask
the MCP server person for the current URL and change that one line. The Connect screen
now checks the server's `/health` and says so on the page, so a stale address shows up
as "EEVEE is not reachable at that address" instead of failing later inside Claude.

## Backend calls

Everything that talks to a server is in `api.js`, following `supabase/README.md`:

- `sendCode(phone)` / `verifyCode(phone, code)`: Supabase Phone OTP. Twilio must be set as the SMS provider in the Supabase dashboard.
- `saveSetup(data)`: as the signed-in user, updates `profiles.weekly_allowance_cents` and the 4 `envelopes` rows (dollars are converted to cents), then calls `rotate_connector_key()` and returns `{MCP_BASE_URL}/mcp/{key}`.

The browser never calls the MCP server except for that `/health` check — the connector
link is handed to Claude, and Claude calls it from Anthropic's cloud. That is why
`MCP_BASE_URL` must be a public address and `localhost` only works for Claude Code.

## Known blocker: phone sign-in is off

**Supabase phone auth is currently disabled on the project**, so `sendCode()` fails for
everyone and no one can reach the Connect screen through the real flow:

```
{"code":400,"error_code":"phone_provider_disabled","msg":"Unsupported phone provider"}
```

Someone with dashboard access has to turn it on: Authentication → Sign In / Providers →
Phone, with the Twilio credentials (Account SID, Auth Token, Message Service SID). Until
then the setup flow cannot be demoed end to end, though everything after sign-in works —
`rotate_connector_key()` and the link it builds are verified against the live project.

While it is off, add the demo phone under Authentication → Phone → test numbers with code
`123456` so the "Demo: use 123456" hint works without sending a real SMS.

## Verified

- `rotate_connector_key()` returns a `wk_` + 48-hex key through the same path the browser uses, and `{MCP_BASE_URL}/mcp/{key}` is accepted by the MCP server over public HTTPS: `request_spend` $29 → `pending` → reply Y → `approved`.
- The Connect screen's status line renders correctly for a healthy server, a server whose database is down, and an unreachable address.
- The page loads with no script errors after the `config.js` split, and `api.js` still sees the Supabase constants.

## Notes

- The UI uses the name EEVEE, and the Claude Code command registers the server as `eevee`.
- Envelope categories are the fixed four (`food`, `travel`, `shopping`, `software`), matching the schema, which creates all four per user. Don't add free-text categories.
