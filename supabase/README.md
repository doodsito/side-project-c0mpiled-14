# Database

Supabase project `ucsysxhiydfgukkkbtdx`. Schema source of truth: `migrations/`. TypeScript types: `types.ts`.

## Rules

- Money is **integer cents** everywhere (`2900` = $29.00). Divide by 100 for display.
- Budget = sum of **approved** requests of the **last 7 days** (rolling, no reset job), checked against both the envelope limit and the weekly allowance.
- An approved request counts immediately ("reserved"). Pending, declined and expired ones don't count.
- One pending request per user at a time. Pending requests expire 10 minutes after creation (applied lazily by every RPC).
- Categories are fixed: `food`, `travel`, `shopping`, `software` (case-insensitive in RPCs).

| Situation | `decision` | `reason` |
|---|---|---|
| Below auto-approve (strictly `<`) and in budget | `approved` | `within_auto` |
| In budget, at or above auto-approve | `pending` | `over_auto` |
| Over the envelope limit | `pending` | `over_envelope` (`over_by_cents`) |
| Over the weekly allowance | `pending` | `over_total` (`over_by_cents`) |
| Another request is pending (nothing inserted) | `blocked` | `pending_exists` |

## Tables

- `profiles`: one per auth user, created at signup. `phone` (E.164) is copied from `auth.users` once the OTP is confirmed. `weekly_allowance_cents`. `connector_key_hash` (sha256, never the key itself).
- `envelopes`: the 4 categories, created at signup. `weekly_limit_cents`, `auto_approve_under_cents`.
- `spend_requests`: `status` `pending | approved | declined | expired`, `decided_by` `auto | sms | timeout`, `reason`, `amount_cents`, `merchant`, `description`, `over_by_cents`, `expires_at`.

## Front (Person 1): publishable key + user session

Auth is Supabase Phone OTP (`signInWithOtp({ phone })` then `verifyOtp`), with Twilio as SMS provider in the dashboard.

```ts
await supabase.from('profiles').update({ weekly_allowance_cents: 10000 }).eq('id', userId)
await supabase.from('envelopes').update({ weekly_limit_cents: 5000, auto_approve_under_cents: 2000 })
  .eq('user_id', userId).eq('category', 'software')
const { data: key } = await supabase.rpc('rotate_connector_key') // shown once; calling again invalidates the old key
```

Users can read their profile, envelopes and requests, and update only the columns above. Rows are never inserted or deleted from the front.

## MCP server (Person 4) + Twilio webhook (Person 3): secret key, server side only

All return `jsonb`. Errors are raised: `invalid_connector_key`, `invalid_category`, `request_not_found`.

- `check_budget(p_key)` → `{ weekly_allowance_cents, total_spent_cents, total_remaining_cents, has_pending, envelopes: [{ category, weekly_limit_cents, auto_approve_under_cents, spent_cents, remaining_cents }] }`
- `request_spend(p_key, p_amount_cents, p_category, p_merchant, p_description?)` → `{ decision, request_id, status, reason, amount_cents, category, merchant, over_by_cents, envelope_remaining_cents, total_remaining_cents, phone, expires_at, ... }`. On `pending`, send the approval text to `phone`.
- `get_status(p_key, p_request_id)` → same request object.
- `resolve_reply(p_phone, p_reply)` → `{ result, ...request }`, where `result` is `approved | declined | expired | no_pending | invalid_reply | unknown_phone`. `p_phone` is Twilio's `From`. Accepts `Y/YES/N/NO`, any case.

`*_remaining_cents` already include approved requests, so after an approval `envelope_remaining_cents` is the "$75 left" of the text.

```ts
const { data } = await supabase.rpc('request_spend', {
  p_key, p_amount_cents: 2900, p_category: 'software', p_merchant: 'Figma',
})
```

## Workflow (no Docker)

```
npx supabase migration new <name>          # write SQL
npx supabase db push                       # apply to the project
npx supabase gen types --lang typescript --linked > supabase/types.ts
npx supabase db query --linked -f supabase/tests/scenarios.sql   # rolled back, must print "all scenarios passed"
npx supabase db advisors --linked
```

Never change the schema from the dashboard.
