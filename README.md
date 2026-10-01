# Cylist

A project workspace that keeps a project's **board**, **files**, **vault**,
**people** and **docs** in one place, and lets your Claude Code agents work on
the same board you do.

| Area | What it holds |
| --- | --- |
| **Board** | Tasks as cards across named columns, with priorities, due dates, sub-tasks and checklists. A card put *on hold* or *blocked* has to say why, and the last column says how the work ended: Done, Cancelled, In prod. |
| **Goals** | The epics cards are written under. Each goal has a colour that its cards wear, and a page showing what is left on it. |
| **People** | Team members and clients, and what each is responsible for. Team members sign in as themselves; roles and permissions say what each may do on a project. |
| **Files** | Folders of uploaded files, with SharePoint and Google Drive links alongside them. |
| **Vault** | Logins, keys and links. Secrets are encrypted at rest and revealed only on request, and every reveal is logged. |
| **Docs** | A project's markdown, filed section → topic → doc. Agents read it before they start and write back what they learn. |
| **Agents** | Skills for a project's agents to follow, and the line that connects Claude Code to the board. |

Cylist is deployed at **<https://dnu-home-1.tail222f46.ts.net>**.

---

## Using the board

1. Open <https://dnu-home-1.tail222f46.ts.net> and sign in with the email and
   password from your invitation. If you don't have an account, ask a project's
   admin to invite you from its **People** tab.
2. Pick a project from the home screen. Its **Board**, **Goals**, **People**,
   **Agents** and **Docs** are in the sidebar.
3. Everything is addressed by reference: a project is `ATL`, a card on it is
   `ATL-41`, and a sub-task of that card is `ATL-41-2`.

Some things worth knowing:

- **Search** takes tags: `col:review`, `who:aditi`, `goal:search`, `blk:`
  (blocked), `hld:` (on hold) and `cnl:` (cancelled), combined with plain text.
- **Stopped work is out of the way.** Cancelled cards are off the board until
  you show them from **Filters**, and a column's on-hold cards sit behind a
  "+ N on hold" line at its foot. `cnl:` and `hld:` find them either way.
- **`@` tags a person and `>` tags a file**, in comments, descriptions,
  checklist items and goals.
- A pasted Jira or GitHub PR link shows as its number and stays clickable.
- A card's **history** shows every change to it, when it was made and who made
  it.
- The **day report** gives you what you did on a project today, ready to paste
  into a stand-up.

---

## Connecting Claude Code

There are two ways to connect. Start with the first. Use the second as well if
you want the board to show while an agent is working on a card.

### 1. The MCP tools: one line, nothing installed

This gives Claude Code the board's tools: read and create cards, move them,
comment, set goals, browse files, use the vault, and read and write docs.

1. Open any project's **Agents** page.
2. Under **Connect Claude Code**, name the machine (for example, "Claude Code on
   my laptop") and press **Get the line**.
3. Copy the line it shows. **It is shown once.** Run it in a terminal on that
   machine:

   ```bash
   claude mcp add --transport http --scope user cylist https://dnu-home-1.tail222f46.ts.net/mcp --header "Authorization: Bearer cyl_…"
   ```

   The line is the same in PowerShell.
4. Start a new Claude Code session. Run `/mcp` to check that `cylist` is
   connected.

The token acts as **you**, so the board shows whose agent did what. It can read
and change the board, and add, change and read vault secrets. Every reveal is
logged, and a project's roles decide whether you, and so your agent, may reveal
anything there. A line made before v0.1.1 only reaches the board; make a new
one for the vault. To reconnect a machine, first run
`claude mcp remove cylist --scope user`.

### 2. The CLI: put your sessions on the board

The CLI adds Claude Code hooks, so a session bound to a card shows on that card.
The border pulses teal while the agent works, turns amber when it is waiting on
you, and goes green when the session ends. It also installs the MCP server
locally, so you don't need step 1 on a machine where you do this.

No clone or `sudo` needed. It installs [uv](https://docs.astral.sh/uv/) if you
don't have it, then the `cylist` CLI and the `cylist-mcp` server, then runs
`cylist setup`:

```bash
# Linux and macOS
curl -fsSL https://raw.githubusercontent.com/dhruva-nu/cylist/main/scripts/install.sh \
  | sh -s -- --url https://dnu-home-1.tail222f46.ts.net
```

```powershell
# Windows PowerShell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/dhruva-nu/cylist/main/scripts/install.ps1))) `
  -Url https://dnu-home-1.tail222f46.ts.net
```

`cylist setup` asks for your email and password once. It then mints a token for
this machine, stores it in `~/.config/cylist/config.toml` (mode 0600), installs
the hooks and the `/work` command, and registers the MCP server. It is safe to
run again.

If you connected this machine with step 1 first, run
`claude mcp remove cylist --scope user` before `cylist setup`.

Open a **new** Claude Code session, then bind it to a card:

```
/work ATL-41             bind this session to ATL-41
/work off                unbind it
cylist work ATL-41       start a new session already bound to ATL-41
```

Only `/work` binds a session. Mentioning `ATL-41` in a prompt does not, and a
session that isn't bound sends nothing to the board.

If a card stays blank while you work on it, run `cylist whoami` to check this
machine is signed in. Setting `CYLIST_HOOK_DEBUG=1` makes the hook report
problems on stderr.

### What agents can do

Once connected, an agent has tools to:

- **Work the board:** `list_tasks`, `get_task`, `create_task`, `move_task`,
  `set_task_status`, `add_comment`, sub-tasks and checklists.
- **Track goals:** `list_goals`, `create_goal`, `set_task_goal`,
  `set_goal_status`.
- **Read the project:** `list_people`, `list_files`, `list_vault`,
  `read_activity`, `day_report`.
- **Use the vault:** `add_secret` files a credential at a path like
  `Logins/Staging/Admin`, `update_secret` changes or rotates one, and
  `reveal_secret` reads its value.
- **Use the docs:** `ask_docs` answers a question from the docs before the
  agent reads the code. When the docs can't answer, the agent writes what it
  found back with `place_doc` and `write_doc`.
- **Follow the project's skills:** `list_skills`, `read_skill`,
  `download_skill`. With the CLI, `cylist skills pull ATL` installs them into
  `.claude/skills/` in your repo, where Claude Code loads them as its own.

Every change an agent makes appears in the card's history under your name.

---

## More

- [`cli/README.md`](cli/README.md): every CLI command and hook event.
- [`mcp/README.md`](mcp/README.md): the MCP server in detail, including
  clients other than Claude Code.
- [`DEVELOPMENT.md`](DEVELOPMENT.md): running Cylist locally, how it is put
  together, auth, roles and permissions, logs and backups.
- [`DEPLOY.md`](DEPLOY.md): how the dev, staging and production deployments
  work.
- [Releases](https://github.com/dhruva-nu/cylist/releases): what's in each
  version.
