# Wallie — project spec (shared across all teams)

Source: team Claude Doc (pasted by user 2026-10-17). This is a copy; the doc is the source of truth if they differ.

**Changes agreed since the doc (2026-10-17):** envelope categories are free text, user-defined (food/travel/shopping/software are examples, not a fixed set). Only one purchase may be in flight per user (concurrency lock). Integration details per team: `docs/TEAM-CONTRACT.md`.

## Product summary

Wallie gives your AI a spending allowance and texts you when it needs permission. Goal for today: a working MVP and a 30-second demo in 3 hours.

- Users set a weekly budget split into envelopes: Food, Travel, Shopping, Software.
- Each envelope has a weekly limit and an auto-approve amount.
- Claude checks with Wallie before any purchase. Bigger purchases trigger a text; reply Y or N.
- Works with the Claude app and Claude Code via an MCP connector.
- All amounts are test dollars. No real money moves.

Core promise: Your AI can ask to spend. You set the rules.

## Team roles

Four builders work in parallel once the shared contract below is locked. Patrick owns copy, testing, and the demo.

| Owner | Area | Deliverables |
| --- | --- | --- |
| Person 1 | Landing page + setup | Landing page (headline, text-thread hero, how it works, Set up Wallie button). 4 setup screens: verify phone, weekly allowance, envelopes, connect Claude. Writes to Supabase. |
| Person 2 | Database | Supabase tables, budget math (last 7 days spent + reserved), per-user connector key. Shares schema in first 15 min. |
| Person 3 | Texting | Twilio phone verification, approval texts, Y/N reply webhook, over-budget texts, 10-min expiry. |
| **Person 4 (us)** | **MCP server** | **check_budget, request_spend, get_status. Approval rules. Test in Claude app and Claude Code.** |

Landing copy: Headline "Give your AI an allowance." Subhead "Set spending limits for Claude. Approve purchases by text." Small line: "Early access: purchases use test dollars."

Connect Claude screen shows: the user's connector link, the Claude Code command `claude mcp add wallie <link>`, and one line to paste into instructions: "Before any purchase, check with Wallie."

## Shared contract

Lock these in the first 15 minutes; everyone builds against them.

```sql
create table users (
  id uuid primary key default gen_random_uuid(),
  phone text unique not null,
  connector_key text unique not null,
  weekly_budget numeric not null,
  created_at timestamptz default now()
);

create table envelopes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references users(id),
  category text not null,          -- food | travel | shopping | software
  weekly_limit numeric not null,
  auto_approve_under numeric not null
);

create table requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references users(id),
  envelope_id uuid references envelopes(id),
  amount numeric not null,
  merchant text,
  description text,
  status text not null default 'pending', -- pending | approved | declined | expired
  created_at timestamptz default now(),
  decided_at timestamptz
);
```

- Statuses: pending, approved, declined, expired.
- Spent in envelope = sum of approved requests in the last 7 days. Pending counts as reserved.
- The connector_key in the user's link identifies them to the MCP server.

## Approval rules

request_spend applies these in order. The tool description must say: "Always call request_spend before any purchase."

| Situation | Wallie does |
| --- | --- |
| In budget, under auto-approve | Approve instantly |
| In budget, over auto-approve | Text user, status pending |
| Over envelope budget | Text: "Over by $X. Approve anyway? Y/N" |
| Reply N | Declined |
| No reply in 10 min | Expired |

- One pending request per user at a time, so a bare Y/N is never ambiguous.
- Claude polls get_status while pending and tells the user it's waiting.
- After approval, Claude shares the purchase link; the user buys it themselves.

## Timeline and demo

Demo script: Software envelope $75/week, auto-approve under $10. Claude requests a $29 subscription → text arrives → reply Y → Claude confirms and shares the link.

Tester question: "What would you trust Claude to buy for you?"

- [ ] Schema locked
- [ ] Twilio number live
- [ ] Landing + setup live
- [ ] MCP connected in Claude app
- [ ] MCP connected in Claude Code
- [ ] Demo recorded
- [ ] Sent to 5 testers
