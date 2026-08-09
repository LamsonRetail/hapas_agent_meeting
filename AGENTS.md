# MeetingxLark agent entrypoint

Before changing, diagnosing, or operating this repository, read
[`docs/CURRENT_CONTEXT.md`](docs/CURRENT_CONTEXT.md) completely. It is the current
live handoff and overrides stale status statements in older handoff documents.

Then read only the relevant sections of `docs/V2_MAINTENANCE.md` and run:

```powershell
git status --short
python -m v2 selftest
```

Safety rules:

- Treat every existing worktree change as user-owned; do not reset or discard it.
- Ask before live ACL changes, external writes, message deletion/sending, service
  interruption, secret rotation, or other risky operations.
- Never grant meeting access from Base permissions, session memory,
  `minute_viewers`, or near-time calendar matching.
- Do not print another user's transcript while testing permissions.

