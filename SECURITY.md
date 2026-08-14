# Security Policy

## Reporting a vulnerability

Please report security issues **privately** using GitHub's
[private vulnerability reporting](https://github.com/mobius29er/halligan/security/advisories/new)
rather than opening a public issue. We aim to acknowledge reports within 7 days.

## Scope

Halligan is a testing harness. The most likely security issues are:

- **Credential leakage** — keys ending up in logs, run artifacts, or commits.
- **Prompt/response artifacts** — run output containing sensitive conversation text.
- **Supply chain** — a compromised dependency reaching CI.

## How this project handles credentials

Halligan **never** takes an API key as a command-line argument, and never
writes one to disk.

1. Keys are read from the environment only (`os.environ`), loaded from `.env`
   via `python-dotenv` at startup.
2. `.env`, `*.key`, `*.pem`, and friends are gitignored — see [`.gitignore`](.gitignore).
3. Every run artifact is passed through a redaction filter
   (`halligan.report.redact`) that masks anything matching known key formats
   (`sk-...`, `sk-ant-...`, `AIza...`, bearer tokens, and generic 32+ char
   high-entropy strings) before it is written or printed.
4. [gitleaks](https://github.com/gitleaks/gitleaks) runs in CI on every push and
   pull request, scanning the **full history**, and fails the build on a hit.
5. A [pre-commit](https://pre-commit.com/) hook runs `detect-secrets` and
   `gitleaks` locally so leaks are caught before they ever reach the remote.

### Enabling the local hooks

```bash
pip install pre-commit
pre-commit install
```

## If you leak a key

Order matters — **revoke first, scrub second**. A key in a public repo should be
assumed compromised the moment it is pushed; rewriting history does not unpublish it.

1. **Revoke/rotate the key at the provider immediately.**
   - Anthropic: <https://console.anthropic.com/settings/keys>
   - OpenAI: <https://platform.openai.com/api-keys>
   - Google: <https://aistudio.google.com/app/apikey>
2. Remove it from history with
   [git-filter-repo](https://github.com/newren/git-filter-repo):
   ```bash
   git filter-repo --replace-text <(echo 'YOUR_LEAKED_KEY==>REDACTED')
   ```
3. Force-push, and ask collaborators to re-clone (rebasing on rewritten history
   can silently reintroduce the blob).
4. Confirm the leak is gone: `gitleaks detect --source . --log-opts="--all"`

## Note on test content

The probe suites in `suites/` deliberately contain adversarial prompts —
jailbreak attempts, emotional-coercion framings, and arguments for positions the
project rejects. They are **test inputs**, not endorsements. See
[`training/README.md`](training/README.md) for the ethics of how they are used.
