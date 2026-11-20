# ComChat

ComChat is an original dark-themed community chat app. Accounts, communities,
memberships, DMs, messages, and reactions are stored on one Python/SQLite
server, so accounts on separate devices can share conversations when they
connect to the same reachable server.

## Run the server

Requires Python 3.9 or newer. The backend uses only the Python standard
library; no Node installation or package install is required. Username and
password accounts work without any external service.

```sh
python3 app_server.py
```

Open [http://127.0.0.1:4173](http://127.0.0.1:4173) on the same computer. The
server binds to loopback by default. To choose another port or database path:

```sh
PORT=8080 COMCHAT_DATABASE=/path/to/comchat.sqlite3 python3 app_server.py
```

The database contains account and conversation data. Back it up securely and
restrict access to it.

## Create a shareable hosted URL

The repository includes a Render Blueprint in `render.yaml`. To deploy:

1. Push this repository to GitHub.
2. In Render, choose **New → Blueprint**, connect the GitHub repository, and
   apply the blueprint.
3. Wait for the service to finish deploying. Render will show its assigned
   HTTPS URL; share that URL with friends so everyone uses the same server.
4. Open the hosted URL and create your account first. On an empty database,
   the first account becomes the server administrator.

The blueprint uses a paid Starter web service with a 1 GB persistent disk
because this app stores accounts and messages in SQLite. Check Render's current
pricing before applying it. Keep a single service instance: SQLite data on a
local persistent disk is not shared across multiple instances. The health
check endpoint is `/healthz`. For Google sign-in, add `GOOGLE_CLIENT_ID` in
Render's environment settings and authorize the assigned HTTPS origin in
Google Cloud.

This is a small prototype, not a hardened public chat service. Before sharing
it broadly—especially with minors—add real moderation, abuse reporting,
account-recovery and safeguarding processes, and have the deployment reviewed
for security and privacy. Do not commit credentials or database files.

## Optional Google sign-in

The sign-in portal supports Google Identity Services when the server owner
configures a Google OAuth **Web application client ID**. In Google Cloud,
register the exact HTTPS site origin under Authorized JavaScript origins, then
start the server with `GOOGLE_CLIENT_ID` set to that client ID:

```sh
GOOGLE_CLIENT_ID=your-web-client-id.apps.googleusercontent.com python3 app_server.py
```

The client ID is public configuration, not a secret; no client secret is
needed. The Google button appears only when configured. Google verifies the
sign-in credential and ComChat checks its audience and expiry on the server.
ComChat stores the Google account identifier and display name, not the email
address. Google sign-in cannot work on an unregistered origin or until this
setup is done.

## Connect with friends

1. The first account created on an empty server becomes its sole administrator.
   Existing databases without an administrator are assigned to the oldest
   account. This is a one-time bootstrap rule; later accounts cannot claim or
   take over the administrator role.
2. The administrator creates communities, edits their names, manages spaces,
   and shares invite codes with friends.
3. Other accounts can join communities with an invite code and start DMs by
   entering another member's username.
4. Members can chat and react; community management APIs reject non-admin
   accounts even if they bypass the interface.

The host must be reachable by every device; running this command on one
computer does not automatically publish it to the internet. **Do not send
account passwords or chat messages over plain HTTP on an untrusted network.**
For friends on separate networks, deploy the app on a trusted host behind
HTTPS and a maintained reverse proxy, set `COMCHAT_SECURE_COOKIES=1`, and give
everyone that HTTPS address. Do not expose the raw development server directly
to the public internet.

## Included

- Server-backed registration, sign-in, display names, and per-device sessions.
- Optional Google sign-in after the site owner configures a Google OAuth client ID.
- A single server administrator, claimed by the first account on a fresh database.
- Shared text communities, invite-code membership, and member lists.
- Username-addressed one-to-one DMs.
- Persistent text messages, replies, reactions, and near-real-time updates
  (checks about every 1.5 seconds while the page is visible and refreshes
  immediately when the browser tab regains focus).
- A dark responsive interface and a blank initial state with no demo accounts
  or seeded conversations.

## Not included yet

ComChat is text-only; voice and video chat are not included. There is no
password reset, age verification, blocking, robust moderation/report handling,
end-to-end encryption, file upload, or account recovery. The server stores
readable messages. This prototype does not include analytics or advertising;
Google sign-in contacts Google when used, but that is **not** anonymity. Do not
use it for sensitive conversations or deploy it for young people without
appropriate privacy review, abuse prevention, moderation, and safeguarding
measures.

## Tests

Run the backend/API suite with:

```sh
python3 -m unittest discover -s tests -v
```

If Node.js 18 or newer is installed, the focused chat-state tests can also be
run with `npm test`.
