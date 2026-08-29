"""Every SQL statement Mabel runs, grouped by what it is about.

Two rules hold across this package:

**Every function takes a connection, never opens one.** The caller owns the
transaction, because the caller is the one that knows which tenant is in scope.
A query function that opened its own connection could not be inside
`tenant_scope()`, which is the only place tenant context exists.

**No query filters by tenant_id in its WHERE clause.** RLS does that. Writing
`WHERE tenant_id = :tenant_id` here would work, and it would also hide the day
somebody forgets, because the query would still look correct. Leaving it to the
policy means a missing `SET LOCAL` returns nothing rather than everything.
"""
