# Roften Wave: GitHub CI/CD

Target server: **199.189.254.39**. Code path: `/opt/roften-wave`.
AzuraCast is a separate service and is not managed by this pipeline.

## What happens

- Each push and pull request runs the existing `Tests` workflow (pytest on Python 3.11–3.13 and Docker build).
- After a **successful push to main**, `CD - Roften Wave` performs **source sync only** over SSH. It never runs Docker in this mode.
- `Actions → CD - Roften Wave → Run workflow → sync` manually synchronizes checked source.
- `Actions → CD - Roften Wave → Run workflow → release` verifies the selected main commit again, then runs the Roften Wave Compose service and checks Docker health. **Do not choose release until you are ready to start the application.**
- Missing SSH secrets cause automatic source sync to be skipped (with a notice); a manual invocation fails loudly.

## One-time server setup (no application launch)

Create a dedicated unprivileged account with SSH login (example `wave-deploy`) and give it ownership of the deployment directory:

```bash
sudo adduser --disabled-password --gecos "" wave-deploy
sudo mkdir -p /opt/roften-wave
sudo chown wave-deploy:wave-deploy /opt/roften-wave
sudo install -d -m 700 -o wave-deploy -g wave-deploy /home/wave-deploy/.ssh
```

Generate an **Ed25519 deploy key pair** on your own trusted computer:

```bash
ssh-keygen -t ed25519 -f ./roften-wave-cd -C roften-wave-cd
```

Add the contents of `roften-wave-cd.pub` as one line in `/home/wave-deploy/.ssh/authorized_keys`, owned by `wave-deploy`, mode `600`. Only this PUBLIC key goes to the server. Do not commit either key or share it in chat.

**Recommended: Base64 GitHub secret (works reliably from Windows PowerShell).** On your own Windows computer, run:

```powershell
# Check that the file is a readable, unencrypted *private* key.
ssh-keygen -y -P "" -f "$HOME\.ssh\roften-wave-cd" | Out-Null

# Convert the entire ORIGINAL file byte-for-byte and copy a single Base64 line.
[Convert]::ToBase64String([IO.File]::ReadAllBytes("$HOME\.ssh\roften-wave-cd")) | Set-Clipboard
```

Paste the clipboard as the GitHub Actions secret **`DEPLOY_SSH_KEY_B64`**. Do not copy the `.pub` file, public key, key fingerprint or Base64 of the displayed text. The pipeline checks the decoded key before attempting SSH. The old multiline secret `DEPLOY_SSH_KEY` remains supported as a fallback, but you can remove it after setting the Base64 secret. If the key has a passphrase, create a separate automation key without a passphrase: the noninteractive workflow cannot answer a passphrase prompt.

Verify server host fingerprint before trusting an SSH scan:

```bash
# On the actual VPS:
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
# On your trusted computer:
ssh-keyscan -t ed25519 199.189.254.39
```

Check that the public key from ssh-keyscan has the same fingerprint as the VPS's host key. Store the FULL `199.189.254.39 ssh-ed25519 AAAA...` line in GitHub secret `DEPLOY_SSH_KNOWN_HOSTS`. On non-default SSH ports, use the SSH known_hosts syntax `[199.189.254.39]:PORT ssh-ed25519 AAAA...`.

In GitHub repository **Settings → Secrets and variables → Actions**, configure:

| Type | Name | Value |
| --- | --- | --- |
| Secret | `DEPLOY_SSH_KEY_B64` (preferred) | Base64-encoded private Ed25519 key file from PowerShell |\n| `DEPLOY_SSH_KEY` (legacy alternative) | Complete multiline private Ed25519 key |
| Secret | `DEPLOY_SSH_KNOWN_HOSTS` | Verified host key line |
| Variable | `DEPLOY_SSH_USER` | `wave-deploy` |
| Variable (optional) | `DEPLOY_SSH_PORT` | `22` (default) |

Once these are set, the next successful push to main syncs the files into `/opt/roften-wave`. The script refuses to overwrite an unrelated directory, refuses dirty tracked files and refuses to move to a diverged or older Git history.

**No Docker permissions are needed for code sync.** Later, for manual `release`, the deploy account needs carefully controlled permission to invoke Docker Compose; membership in the `docker` group effectively grants root-equivalent access. Avoid enabling this until you are ready and have reviewed the host's access controls.

## Release prerequisites (not needed for source sync)

On the server prepare `/opt/roften-wave/.env` from `.env.example` and set its permissions to `600`. Fill in admin password/session secret, Yandex token, DJ credentials and optionally Telegram bot credentials **on the server only**. Confirm that port 8787 is unused, that FFmpeg DJ settings match your AzuraCast station, and that the existing AzuraCast AutoDJ works. Run `bash deploy/preflight.sh` locally before invoking `release`.

The CD script uses **`docker compose -p roften-wave up -d --build --no-deps wave`** on explicit release only. The project name is fixed, so its SQLite named volume does not change between releases. It does not edit Nginx, DNS, SSL or AzuraCast configuration. Health checks confirm the Wave container started; this does not prove that real Yandex streaming or DJ authentication works. There is no automated rollback in this initial CD pipeline.

If the VDS has already been cloned or configured locally, leave `.env` untracked. Do not manually edit tracked source files on the server; CD deliberately refuses to overwrite such changes.
