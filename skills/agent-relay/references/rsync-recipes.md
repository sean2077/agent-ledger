# rsync recipes

Detailed reference for `relay sync`. Read this when default mode warnings appear or when sync behaves unexpectedly.

## Default mode: fast and forgiving

```bash
rsync -avh --progress \
  --filter=':- .gitignore' \
  --exclude=.git \
  --exclude=.shared \
  "$PROJECT_ROOT/" "$RELAY_REMOTE_SSH:$RELAY_REMOTE_PATH/"
```

- `:- .gitignore` reads each directory's `.gitignore` as exclude patterns.
- Explicit `--exclude=.git` because `.gitignore` doesn't ignore the `.git/` metadata dir.
- Explicit `--exclude=.shared` because `.shared` is shared via the mount; rsync syncing it would fight the mount.

### Known footgun: `!path` reverse rules don't work

Verified against rsync 3.2.7:

```
.gitignore:
  *
  !keep/
  !keep/file.txt
```

Under `--filter=':- .gitignore'`, rsync transfers **zero files**. The `!` (re-include) is honored by git but the rsync filter parser treats `:- file` as exclude-only.

### When to use strict mode

If your `.gitignore` uses `!path` to re-include anything, default mode will skip those re-included files. Switch to strict:

```bash
relay sync push --strict-gitignore
```

Internally:

```bash
git ls-files --cached --others --exclude-standard -z > /tmp/relay-sync.lst
rsync -avh --progress \
  --files-from=/tmp/relay-sync.lst --from0 \
  --exclude=.git --exclude=.shared \
  "$PROJECT_ROOT/" "$RELAY_REMOTE_SSH:$RELAY_REMOTE_PATH/"
```

- `--from0` is **required** because `-z` produced NUL-delimited paths.
- This is exactly what git would consider tracked + untracked-but-not-ignored.
- Slower than default mode (one extra git pass + larger argv) but semantically exact.

### Default vs strict at a glance

| Aspect | Default | Strict |
|---|---|---|
| Speed | fast | slower (extra git pass) |
| Honors `!path` rules | no | yes |
| Honors per-dir nested `.gitignore` | yes (one file at a time) | yes (git resolves recursively) |
| Requires git | no | yes |
| Recommended for | most repos | repos with negation patterns |

## Shape A vs Shape B

The relay supports two project shapes:

### Shape A: entire project on the mount

`$PROJECT_ROOT` is a sshfs mount point. Both host and remote see the same underlying files. **No sync needed** — edits land everywhere immediately.

`relay sync` detects this via `stat -f -c %T $PROJECT_ROOT` returning `fuseblk` / `fuse.sshfs` and aborts with a message. This is correct behavior, not a failure.

### Shape B: separate copies + shared `.shared/` only

Host and remote each have a local project tree. Only `.shared/` is the sshfs mount. Code changes must rsync between them.

- `relay sync push` (host → remote) for changes you made on host.
- `relay sync pull` (remote → host) for changes the remote made.
- Remote CANNOT initiate sync — it cannot SSH back to host. So if the remote agent made changes, the remote-side handoff `prompt_for_next` should say "please pull on host before continuing" and set `sync_needed: true`.

## First sync is the riskiest

Always `--dry-run` first:

```bash
relay sync push --dry-run
```

Review the file list. Look for:
- Files you didn't expect to transfer (gitignore missing an entry?)
- Files that should transfer but don't (negation pattern?)
- `.shared` or `.git` (should never appear; if they do, that's a bug in `relay sync`)

Only then drop `--dry-run`.

## `--delete` and when to use it

Default: `--delete` is **off**. Both sides keep extra files.

Enable explicitly when you intend to mirror:

```bash
relay sync push --delete
```

Useful when:
- Cleaning up files that were renamed on host and the remote still has the old name.
- Preparing remote for a fresh build (drop stale artifacts).

Dangerous when:
- The remote has uncommitted edits you don't know about.
- The remote has local-only files (build cache, secrets in ignored paths) that aren't in `.gitignore`.

When in doubt: `--dry-run --delete` first to see what would be deleted.

## SSH troubleshooting

`relay sync` shells out to `rsync` which shells out to `ssh`. If sync hangs or fails on the SSH layer:

- Test base connectivity: `ssh $RELAY_REMOTE_SSH hostname`.
- Test passwordless auth: `ssh -o BatchMode=yes $RELAY_REMOTE_SSH true`.
- Check `~/.ssh/config` for the host alias and any `ControlMaster` issues.
- For ControlMaster: `ssh -O check $RELAY_REMOTE_SSH` should report "Master running".

If first SSH works but rsync hangs, suspect MTU / firewall / large initial transfer; try with `--bwlimit=8000` (~8 MB/s) to slow it down.

## When to NOT use `relay sync`

- You only changed `.shared/` content. That's already on the mount — no sync needed.
- You're on `RELAY_SYNC=none` (explicitly or by default). The CLI will refuse — this side does not own the rsync transport.
- The project is shape A. The CLI will refuse — there is only one project copy, nothing to rsync.
- You haven't committed your work and don't want partial state on remote. Either commit first, or accept that rsync will mirror your working tree as-is.
