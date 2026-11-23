# Telegram notifications

Herdr can send the owner a private Telegram message when an agent is blocked,
a discussion needs a decision, a task is ready for review, or a job or
integration needs attention. An administrator configures one bot and one private
recipient under **Settings → Telegram notifications**.

## Connect your bot

1. Open [@BotFather](https://t.me/BotFather), send `/newbot`, and follow its
   instructions to create a dedicated bot. Copy the token it provides.
2. Open your new bot's conversation and send `/start` so it can message you.
3. Obtain your own numeric user ID from [@userinfobot](https://t.me/userinfobot).
   Use that positive number as the **Private chat ID**. Usernames, phone numbers,
   group IDs and channel IDs are not supported by this panel.
4. Enter the token and chat ID in Herdr, enable notifications, and save.
5. Choose **Send test notification** and confirm that it arrives in your bot's chat.

The token is write-only: Herdr shows whether it is configured without returning
its value. A blank token field preserves the saved token. Disable notifications
to pause automatic delivery while retaining settings. **Disconnect Telegram**
clears the token, recipient and delivery receipts. Tests work with automatic
alerts disabled.

## Delivery and decisions

The gateway polls current Herdr state every 15 seconds while running. Saving
configuration also wakes the worker. Messages contain short labels and
identifiers with a request to open Herdr. Terminal transcripts, prompts, bot
credentials and raw runtime errors are excluded. Messages do not approve
operations or send input to agents; review and respond through the authenticated
dashboard.

Herdr records acknowledged deliveries so unchanged issues are not sent again
after a gateway restart. Resolving an issue allows a later recurrence to notify
again; changing the bot or recipient resets receipts. The most recent 10,000
issue receipts are retained. Each message contains a
bounded digest of up to ten issues, so large backlogs take several passes.
Failed sends remain pending and retry after a delay, including Telegram's
reported rate limit. Delivery errors appear in settings.

Delivery is best effort. A message may repeat if Telegram accepts it but the
gateway stops before saving its receipt. Notifications describe observed current
state; they are not an audit log of every brief transition or a guarantee of
delivery while the service is stopped. Each scan considers up to 1,000 active
tasks, the latest 100 integration events, and returns up to 200 issues.

## Storage and connectivity

Configuration and receipts live in `telegram.json` beside the organization
database (normally `/home/herdr/.config/herdr-web/telegram.json`). The gateway
writes it atomically with mode `0600` on Linux. It is separate from organization
and task exports; configure Telegram again after migrating those exports.
Agents and the gateway share the `herdr` Unix user, so file permissions do not
isolate credentials from that account.

Only outbound HTTPS to `api.telegram.org` is needed. No inbound webhook or
publicly reachable dashboard is required. Check outbound access, the saved token
and chat ID, and whether your bot has been started or blocked if delivery fails.
The integration follows the [Telegram Bot API](https://core.telegram.org/bots/api#sendmessage).
