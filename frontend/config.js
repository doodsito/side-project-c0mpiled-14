// Deployment settings. This is the ONE file to edit when an address changes.

// The MCP server's public HTTPS address, no trailing slash. Connector links are
// `${MCP_BASE_URL}/mcp/${connector_key}`, so this must be reachable from the public
// internet — the Claude app calls it from Anthropic's cloud, not from this browser.
//
// This is an ngrok STATIC domain: it survives restarts of the tunnel and the server, so
// connector links handed out earlier keep working. Verified end to end against the live
// Supabase project.
//
// If links ever stop working, check the server is actually up first:
// curl https://sepia-pupil-giddily.ngrok-free.dev/health
const MCP_BASE_URL = "https://sepia-pupil-giddily.ngrok-free.dev";

// Supabase. The publishable key belongs in the browser: Row Level Security limits each
// user to their own rows, and the MCP server's functions are not granted to it at all.
const SUPABASE_URL = "https://ucsysxhiydfgukkkbtdx.supabase.co";
const SUPABASE_PUBLISHABLE_KEY = "sb_publishable_kH8DQml2zIAyghryfM1uOA_CJVJFF7J";
