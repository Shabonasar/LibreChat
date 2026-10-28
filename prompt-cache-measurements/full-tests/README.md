# Full test sets removed from the PRs in r13

During the r13 reduction the prompt-cache PRs were cut down to the minimum; the larger pre-reduction test sets are kept here as patches. Each patch is `git diff <PR head> <old full-tests head> -- <test files>`, limited to test files with added lines.

Layout: `librechat/<PR>/full-tests.patch` (LibreChat-AI/LibreChat, PR numbers 16369, 17146, 17147, 17149, 17189) and `agents/<PR>/full-tests.patch` (LibreChat-AI/agents, PR numbers 613, 614, 615, 619, 629). #17145, #17148 (closed, merged into #17146 / #17147) and #17514 have no separate set.

Apply on the r13 head of the PR (heads are listed in LibreChat-AI/LibreChat#17102; `integration/prompt-cache` in both forks holds all PR heads together):

```
git checkout <PR head>
git apply --check full-tests.patch && git apply full-tests.patch
```

Run in docker (Node 22/24, `npm ci` first), for example `npx jest <file>` in `api/`, `npx jest <file>` in `packages/api`, `npx vitest run <file>` in agents.

Notes: the tests were written for the pre-reduction code and may need adjusting. Later PRs in the chain contain earlier PR tests, so patches overlap. Because the old branches were based on an older upstream, a patch can also contain a few hunks that only revert upstream drift in the same test file; review before use.
