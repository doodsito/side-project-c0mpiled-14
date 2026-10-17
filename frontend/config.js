// Deployment settings. This is the ONE file to edit when an address changes.

// The MCP server's public HTTPS address, no trailing slash. Connector links are
// `${MCP_BASE_URL}/mcp/${connector_key}`, so this must be reachable from the public
// internet — the Claude app calls it from Anthropic's cloud, not from this browser.
//
// Currently a cloudflared quick tunnel, which gets a NEW address every time it
// restarts. When connector links stop working, this is almost always why: ask the MCP
// server person for the current URL and change the line below. A stable ngrok domain or
// a Railway deploy replaces it before the demo.
const MCP_BASE_URL = "https://knowing-converter-ion-groove.trycloudflare.com";

// Supabase. The publishable key belongs in the browser: Row Level Security limits each
// user to their own rows, and the MCP server's functions are not granted to it at all.
const SUPABASE_URL = "https://ucsysxhiydfgukkkbtdx.supabase.co";
const SUPABASE_PUBLISHABLE_KEY = "sb_publishable_kH8DQml2zIAyghryfM1uOA_CJVJFF7J";
