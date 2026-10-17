# Frontend (Person 1)

Landing page and the 4 setup screens (verify phone, weekly allowance, envelopes, connect Claude). Plain HTML/CSS/JS, no build step.

## Run

Open `index.html` in a browser, or run `npx serve frontend` from the repo root.

## Backend calls

Everything that talks to a server is in `api.js`, following `supabase/README.md` ("Front (Person 1)"):

- `sendCode(phone)` / `verifyCode(phone, code)`: Supabase Phone OTP. Twilio must be set as the SMS provider in the Supabase dashboard.
- `saveSetup(data)`: as the signed-in user, updates `profiles.weekly_allowance_cents` and the 4 `envelopes` rows (dollars are converted to cents), then calls `rotate_connector_key()` and returns `{MCP_BASE_URL}/mcp/{key}`.

## Before the demo

- Set `MCP_BASE_URL` at the top of `api.js` to the MCP server's public HTTPS address.
- The hint "Demo: use 123456" only works if the demo phone is added as a test number (code 123456) in Supabase Auth → Phone settings. Otherwise the real SMS code is needed.
- The UI uses the name EEVEE, and the Claude Code command registers the server as `eevee`.
