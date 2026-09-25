# Multi-chapter development: branching and the guards on `main`

*Set up 09-25-26.*

Multi-chapter support means turning Parliament from one chapter's app into one that serves many chapters. That touches almost every model and view, so it is built on a **long-lived branch** and must not reach `main` until it is done, because **prod deploys are `git pull` of `main`**.

## Branches

| Branch | What goes there |
|---|---|
| `main` | The live chapter app. Bug fixes and small features continue here as usual. |
| `multi-chapter` | Integration branch for the multi-chapter work. Always runnable, never deployed. |
| `multi-chapter/<topic>` | Optional short-lived feature branches (e.g. `multi-chapter/tenant-model`). Merge them into `multi-chapter`, never into `main`. |

**Keep `multi-chapter` current with `main`.** Merge `main` into `multi-chapter` regularly (weekly, or after any migration lands on `main`):

```bash
git checkout multi-chapter
git merge main
git push
```

Merging in this direction is always safe. Two long-lived branches that drift apart for months are the thing that actually kills projects like this, and migrations are where they collide first.

## The marker file

`multi-chapter` carries a file called **`.multi-chapter`** that exists on no other branch. Both guards look for it. The marker catches the work however it travels: a renamed branch, a cherry-pick onto another branch, or a local `git merge` pushed straight to `main`. A rule based only on the branch name can't catch those.

**Don't delete `.multi-chapter`** until the release merge (see below). Don't copy it to `main` either.

## The guards

1. **Local: `scripts/pre-push.sh`** (installed with `make hooks`). This refuses any push to `main` whose commit contains `.multi-chapter`. It runs before the test suite, so it works even where Django isn't importable. `git push --no-verify` bypasses it, so don't use that on `main`.
2. **Server: `.github/workflows/protect-main.yml`.**
   - **On a pull request into `main`:** the check fails if the source branch is `multi-chapter`/`multi-chapter/*` or if the code contains the marker.
   - **On a direct push to `main`:** it can't undo the push, but it fails loudly so you see it before you `git pull` on the server.
   - It runs as `pull_request_target`, which uses the copy of the workflow on `main`, so a branch can't edit the guard to get past it.
3. **CI (`ci.yml`)** now also runs the test suite on pushes to `multi-chapter` and `multi-chapter/**`, and on PRs into `multi-chapter`.

## One-time GitHub setup (do this in the browser)

The workflow **reports**. It only **blocks** a merge once it is a required check. In the repo, go to **Settings → Rules → Rulesets → New ruleset → New branch ruleset**:

- **Name:** `Protect main`. **Enforcement status:** Active.
- **Target branches:** Add target → *Include default branch*.
- **Bypass list:** leave it **empty** if you're willing to merge through PRs. If you add yourself, your own merges also skip the checks.
- Rules:
  - ✅ **Restrict deletions**
  - ✅ **Block force pushes**
  - ✅ **Require status checks to pass**, then add **`Block multi-chapter merges`**. Optionally also add `Run Tests`.
  - ✅ **Require a pull request before merging** *(optional, see below)*

Then create a label called **`multi-chapter-release`** under Issues → Labels.

**About "Require a pull request":** today you push straight to `main`. If you turn this on, every change to `main` goes through a PR, including your normal fixes. That's the strongest protection, but it changes your workflow. If you leave it off, direct pushes are still allowed, and the pre-push hook plus the push alarm are what protect you. Pick whichever you'll actually live with.

Optionally, add a second ruleset for `multi-chapter` with **Block force pushes** and **Restrict deletions**, so months of work can't be lost with one command.

### Why not "only these folders can be pushed to this branch"?

GitHub has no per-branch folder allow-list:

- **Path-restricting rules** are *push rulesets*, which apply to **private/internal repositories** and to the whole repo rather than to one branch.
- `Parliament-New` is public.
- **CODEOWNERS** can require *review* for paths, but it can't forbid them.

Multi-chapter work also touches nearly every folder (models, views, templates, migrations), so a folder rule wouldn't separate the two lines of work anyway. What does separate them is the branch plus the marker.

## Shipping it (someday)

1. Merge `main` into `multi-chapter` one last time. Get CI green on Postgres. Test on staging, which you'll want by then (see `AI/09-25-26-Ideas.md` #10).
2. Open a PR `multi-chapter → main`. In that PR, **delete `.multi-chapter`** and add the **`multi-chapter-release`** label.
3. The guard passes only when both are true. Merge, then deploy per the usual protocol.
