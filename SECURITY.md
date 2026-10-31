# Security Policy

## Supported versions

Security fixes are made on the latest release and the `main` branch.

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Report them privately through
[GitHub security advisories](https://github.com/SpectreMercury/friday/security/advisories/new).
Include the affected version or commit, the steps to reproduce, and the impact you expect.

You can expect an acknowledgement within a week. Once a fix is available, the advisory is published
with credit to the reporter unless you prefer to stay anonymous.

## Scope

Friday is designed to run on a single Mac owned by its operator. Of particular interest are:

- authentication bypasses in the PWA, passkey flow or native pairing;
- ways for a non-admin user, or content the model reads (web pages, documents), to trigger
  computer/terminal tools or other side effects without the admin's approval;
- exposure of `.env` secrets, chat data or files outside the requesting user's session.

Running the admin's approved terminal commands with the macOS account's permissions is documented
behaviour, not a vulnerability.
