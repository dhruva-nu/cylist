---
name: deploy-dev
description: Put a card's branch on one of Cylist's five dev slots, move the card to Dev, and leave the "Deployed to dev_N" comment on it. Use whenever a CYLIST card's work is to be deployed to dev or moved to the Dev column.
---

# Deploy a card's branch to dev

Dev is five slots, `dev_1` to `dev_5`, each served at
`https://dnu-home-1.tail222f46.ts.net:9443/dev_N/` (tailnet only). The root,
`https://dnu-home-1.tail222f46.ts.net:9443/`, lists which branch and commit is
in each slot. A card goes to **Dev** only through a dev deploy, and never without its pull
request: **a card must not enter Dev without the PR link on it** (step 2). Every
dev deploy leaves one comment on the card, exactly:

```
Deployed to dev_N — https://dnu-home-1.tail222f46.ts.net:9443/dev_N/ — branch <branch> @ <sha12>
```

`<sha12>` is the first 12 characters of the deployed commit. The separators
are em dashes (`—`). Keep the wording exact: the Deploy dev workflow posts the
same line itself, and the check in step 7 relies on the two matching.

Never touch a server, a GitHub secret, or a slot you were not asked to use.

## 1. Push the branch

The workflow deploys what is on GitHub, not what is in your checkout.

```bash
git push -u origin <branch>
```

## 2. Put the PR on the card

Do this before the deploy, so the card never reaches Dev without it. Make sure
a pull request exists for the branch:

```bash
gh pr view --json url -q .url || gh pr create
```

`gh pr create` needs a title and body (`--fill` takes them from the commits);
the body should show the work running, as `/work` asks. Then read the card
(`get_task <CARD>`). Unless a comment already contains that PR URL, add it:

```
add_comment(task="<CARD>", author="Agent", body="Pull request: <url>")
```

If no PR can be raised, stop: don't deploy and don't move the card. Say why.

## 3. Pick a slot

Read the index:

```bash
curl -fsS https://dnu-home-1.tail222f46.ts.net:9443/
```

- The slot already holding **this branch**: use it. Redeploying replaces it in place.
- Otherwise an **empty** slot.
- Otherwise **ask the user** which slot to replace. Never evict another
  branch on your own judgement: someone may be testing it.

If the user named a slot, use that one.

## 4. Run the deploy

```bash
gh workflow run deploy-dev.yml --ref <branch> -f slot=<N> -f card=<CARD>
```

`<CARD>` is the card's reference, such as `CYLIST-79`. The workflow refuses
anything that isn't `KEY-number` before it deploys.

## 5. Watch it

`gh workflow run` doesn't print the run it started. Wait a few seconds, then
find the run and watch it:

```bash
gh run list --workflow deploy-dev.yml --branch <branch> --event workflow_dispatch \
  --limit 1 --json databaseId,status,createdAt,headSha
gh run watch <databaseId> --exit-status
```

Check that `createdAt` is the run you just started, not an earlier one. Runs
for the same slot queue behind each other, so it may sit in `queued` for a
while.

If the run fails, stop. Don't move the card. Tell the user, with the run's
link (`gh run view <databaseId> --json url -q .url`) and the failing step.

## 6. Move the card to Dev

Only once the PR comment from step 2 is on the card.

```
move_task(task="<CARD>", column="Dev")
```

On CYLIST the column is actually named `"Dev "`, with a trailing space. `Dev`
still resolves to it, because MCP matches exact names first, then prefixes, and
no other column starts with "Dev". If the move comes back `ambiguous` or
`not_found`, read the columns with `get_project CYLIST` and pass the Dev
column's id.

## 7. Comment, unless the workflow already did, and verify

When the repository has the `CYLIST_AGENT_TOKEN` secret, the workflow's last
step ("Comment on the card") has already posted the line as Agent. Read the
card (`get_task <CARD>`) and look for a comment from this deploy, one that
starts `Deployed to dev_<N>` and ends `@ <sha12>`.

- **If it's there**, don't post it twice.
- **If it isn't there**, post it yourself, as Agent:

```
add_comment(task="<CARD>", author="Agent",
  body="Deployed to dev_<N> — https://dnu-home-1.tail222f46.ts.net:9443/dev_<N>/ — branch <branch> @ <sha12>")
```

Finally, verify the card as a whole. In the same `get_task <CARD>` read, check
for **both** comments: the PR comment (a comment containing the PR URL) and the
`Deployed to dev_<N>` comment. If the PR comment is missing, add it now and say
so. Your final summary lists both: the PR link and the deploy line.

There are two ways to get `<sha12>`: the run's `headSha` from step 5 (first 12
characters), or the "Recording what is in slot N" line of the deploy log.

## Redeploying

Every deploy gets its own comment, on the card whose branch was deployed. That
holds for redeploying the same branch after new commits (new sha) and for
putting a different card's branch into a slot that held another one. The new
card gets the comment. The old card keeps its old comment, which the index now
contradicts. That's expected: the index is what says what's in a slot now.
