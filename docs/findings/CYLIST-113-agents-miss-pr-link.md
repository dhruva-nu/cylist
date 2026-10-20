# CYLIST-113: agents move cards to Dev without the PR on the card

Status: findings only. Nothing deployed, no instruction files changed yet.

## Where the instructions live

Two places tell an agent what to do when work is finished. They disagree.

1. `~/.claude/commands/work.md`, the `/work` command. It is machine-local
   (not in this repo). Its last paragraph says: raise a PR, `add_comment` the
   PR link on the card, then `move_task` to the Dev column.
2. `docs/skills/deploy-dev/SKILL.md`, the project skill for "move a card to
   Dev". It says "A card goes to **Dev** only through a dev deploy" and walks
   push, pick slot, run workflow, `move_task`, comment. It never mentions a
   pull request.

The repo has no `CLAUDE.md`. The block `cylist repo setup` writes
(`cli/cylist_cli/commands/repo.py`, `_column_lines`/`_skill_lines`) lists
columns and skills only, so it carries no PR step either.

## Why PRs get missed

- The PR rule is only in `/work`, as the last sentence of a long paragraph
  about docs. An agent that does not start through `/work`, or that goes
  straight to the `deploy-dev` skill when told "move to dev", never sees it.
- `deploy-dev` is the more specific instruction at the moment of moving to Dev,
  and it defines "done" as the `Deployed to dev_N` comment. Following it
  exactly gives a card in Dev with no PR link.
- Ordering conflict: `/work` says PR first, then move. `deploy-dev` says
  deploy, then move. Nothing says a PR must exist before the deploy.
- The PR link is only a free-text comment. Nothing checks it (`move_task` does
  not require one), so a miss is silent. `add_link` is for project files, not
  task links, so a comment is the only place to put it today.

## Proposed fix (not applied)

1. Add a step to `docs/skills/deploy-dev/SKILL.md` before "Move the card to
   Dev": ensure a PR exists for the branch (`gh pr view --json url`, else
   `gh pr create`), then `add_comment` its URL on the card, unless a comment
   with that URL is already there. Say that a card must not enter Dev without it.
2. Make step 6's check also read the PR comment, so the final summary lists
   both comments.
3. Move the PR sentence in `work.md` into its own short, numbered "Finishing"
   list so it is not buried, and point it at `deploy-dev` for the order.
   This file is outside the repo, so the user has to approve the edit.
4. Optional: have `cylist repo setup` emit a "Finishing a card" line in the
   CLAUDE.md block, so sessions that skip `/work` still get the rule.
5. Optional, stronger: make `move_task` into the last-but-one column warn or
   refuse when the card has no comment containing a GitHub PR URL.

Recommended: 1, 3 and 4. Item 5 changes server behaviour and needs a decision.

## Implemented

- `docs/skills/deploy-dev/SKILL.md`: new step 2 "Put the PR on the card"
  (`gh pr view`, else `gh pr create`; `add_comment` the URL unless already
  there), before the deploy and the move. Later steps renumbered; the final
  step now verifies both the PR comment and the `Deployed to dev_N` comment and
  the summary lists both. The rule that a card must not enter Dev without the
  PR link is stated at the top and in step 6.
- `~/.claude/commands/work.md` (outside the repo, edited in place): the PR
  sentence is now a numbered "Finishing" list pointing at `deploy-dev`.
- `cylist repo setup`: the CLAUDE.md block gains a "Finishing a card" line
  (`_finishing_lines` in `cli/cylist_cli/commands/repo.py`), with a test in
  `cli/tests/test_repo.py`. CLI suite: 544 passed.
- Item 5 (server-side `move_task` check) was left for a decision.

