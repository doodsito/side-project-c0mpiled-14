-- Platform event trigger function (ensure_rls): it fires on DDL regardless of EXECUTE, so nobody needs to call it via the API.
revoke all on function public.rls_auto_enable() from public, anon, authenticated;
