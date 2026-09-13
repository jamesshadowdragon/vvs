import json
import os
import sys
import time
from datetime import datetime, timezone

import requests


BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "PUT_BOT_TOKEN_HERE")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "PUT_WEBHOOK_URL_HERE")
FORUM_CHANNEL_ID = "1544952304015384596"

STATE_FILE = "sent_bugs.json"

API = "https://discord.com/api/v10"

REQUEST_DELAY = 0.35
WEBHOOK_DELAY = 0.75

MAX_REPORTS = None
DRY_RUN = False


session = requests.Session()
session.headers.update({
    "Authorization": f"Bot {BOT_TOKEN}",
    "User-Agent": "BugExporter/1.0"
})


def fail(message):
    print(f"\nERROR: {message}")
    sys.exit(1)


def load_state():
    if not os.path.exists(STATE_FILE):
        return set()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        return set(str(x) for x in data)

    except Exception:
        print("WARNING: Could not read sent_bugs.json. Starting fresh.")
        return set()


def save_state(sent_ids):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(
            sorted(sent_ids),
            file,
            indent=2
        )


def discord_get(url, params=None):
    while True:
        response = session.get(
            url,
            params=params,
            timeout=30
        )

        if response.status_code == 429:
            try:
                retry_after = float(
                    response.json().get("retry_after", 2)
                )
            except Exception:
                retry_after = 2

            print(f"Rate limited. Waiting {retry_after:.2f}s...")
            time.sleep(retry_after)
            continue

        if response.status_code == 401:
            fail("Invalid bot token.")

        if response.status_code == 403:
            fail(
                "Discord returned 403. Make sure the bot can "
                "View Channel and Read Message History."
            )

        if not response.ok:
            fail(
                f"Discord API error {response.status_code}: "
                f"{response.text[:500]}"
            )

        time.sleep(REQUEST_DELAY)

        return response.json()


def get_channel():
    return discord_get(
        f"{API}/channels/{FORUM_CHANNEL_ID}"
    )


def get_active_threads(guild_id):
    data = discord_get(
        f"{API}/guilds/{guild_id}/threads/active"
    )

    return [
        thread
        for thread in data.get("threads", [])
        if str(thread.get("parent_id")) == FORUM_CHANNEL_ID
    ]


def get_archived_threads():
    all_threads = []

    before = None

    while True:
        params = {
            "limit": 100
        }

        if before:
            params["before"] = before

        data = discord_get(
            f"{API}/channels/{FORUM_CHANNEL_ID}/threads/archived/public",
            params=params
        )

        threads = data.get("threads", [])

        if not threads:
            break

        all_threads.extend(threads)

        if not data.get("has_more"):
            break

        before = threads[-1].get("thread_metadata", {}).get(
            "archive_timestamp"
        )

        if not before:
            break

    return all_threads


def get_thread_message(thread_id):
    return discord_get(
        f"{API}/channels/{thread_id}/messages/{thread_id}"
    )


def get_thread_url(thread):
    guild_id = thread.get("guild_id", "")
    thread_id = thread.get("id", "")

    return (
        f"https://discord.com/channels/"
        f"{guild_id}/{thread_id}"
    )


def clean_text(text):
    if not text:
        return ""

    return text.strip()


def truncate(text, length):
    if len(text) <= length:
        return text

    return text[:length - 3] + "..."


def convert_embed(source):
    result = {}

    if source.get("title"):
        result["title"] = truncate(
            source["title"],
            256
        )

    if source.get("description"):
        result["description"] = truncate(
            source["description"],
            4096
        )

    if source.get("url"):
        result["url"] = source["url"]

    if source.get("color") is not None:
        result["color"] = source["color"]

    if source.get("timestamp"):
        result["timestamp"] = source["timestamp"]

    if source.get("footer"):
        footer = {}

        if source["footer"].get("text"):
            footer["text"] = truncate(
                source["footer"]["text"],
                2048
            )

        if source["footer"].get("icon_url"):
            footer["icon_url"] = source["footer"]["icon_url"]

        if footer:
            result["footer"] = footer

    if source.get("author"):
        author = {}

        if source["author"].get("name"):
            author["name"] = truncate(
                source["author"]["name"],
                256
            )

        if source["author"].get("url"):
            author["url"] = source["author"]["url"]

        if source["author"].get("icon_url"):
            author["icon_url"] = source["author"]["icon_url"]

        if author:
            result["author"] = author

    if source.get("thumbnail"):
        thumbnail = {}

        if source["thumbnail"].get("url"):
            thumbnail["url"] = source["thumbnail"]["url"]

        if thumbnail:
            result["thumbnail"] = thumbnail

    if source.get("image"):
        image = {}

        if source["image"].get("url"):
            image["url"] = source["image"]["url"]

        if image:
            result["image"] = image

    fields = []

    for field in source.get("fields", []):
        name = truncate(
            str(field.get("name", "\u200b")),
            256
        )

        value = truncate(
            str(field.get("value", "\u200b")),
            1024
        )

        fields.append({
            "name": name,
            "value": value,
            "inline": bool(field.get("inline", False))
        })

        if len(fields) >= 25:
            break

    if fields:
        result["fields"] = fields

    return result


def build_payload(thread, message):
    title = clean_text(thread.get("name")) or "Untitled Bug"

    author = message.get("author", {})
    username = author.get("username", "Unknown")
    author_id = author.get("id", "Unknown")

    content = clean_text(message.get("content"))

    created_at = message.get("timestamp")
    thread_url = get_thread_url(thread)

    attachments = message.get("attachments", [])

    embeds = []

    for source_embed in message.get("embeds", []):
        converted = convert_embed(source_embed)

        if converted:
            embeds.append(converted)

    report_lines = [
        f"**Reporter:** {username} (`{author_id}`)",
        f"**Report ID:** `{thread.get('id')}`",
        f"**Created:** {created_at or 'Unknown'}",
        f"**Original:** {thread_url}"
    ]

    if content:
        report_lines.append(
            "\n**Report:**\n" +
            truncate(content, 3500)
        )

    if attachments:
        attachment_lines = []

        for attachment in attachments:
            name = attachment.get(
                "filename",
                "attachment"
            )

            url = attachment.get("url")

            if url:
                attachment_lines.append(
                    f"[{name}]({url})"
                )

        if attachment_lines:
            report_lines.append(
                "\n**Attachments:**\n" +
                "\n".join(attachment_lines)
            )

    description = "\n".join(report_lines)

    payload = {
        "username": "Bug Archive",
        "allowed_mentions": {
            "parse": []
        },
        "embeds": [
            {
                "title": truncate(title, 256),
                "description": truncate(description, 4096),
                "url": thread_url
            }
        ]
    }

    for embed in embeds:
        if len(payload["embeds"]) >= 10:
            break

        payload["embeds"].append(embed)

    return payload


def send_webhook(payload):
    while True:
        response = requests.post(
            WEBHOOK_URL,
            json=payload,
            timeout=30
        )

        if response.status_code == 429:
            try:
                data = response.json()
                retry_after = float(
                    data.get("retry_after", 2)
                )
            except Exception:
                retry_after = 2

            print(
                f"Webhook rate limited. "
                f"Waiting {retry_after:.2f}s..."
            )

            time.sleep(retry_after)
            continue

        if response.status_code in (200, 204):
            time.sleep(WEBHOOK_DELAY)
            return True

        print(
            f"Webhook failed: "
            f"{response.status_code} "
            f"{response.text[:500]}"
        )

        return False


def main():
    if BOT_TOKEN == "PUT_BOT_TOKEN_HERE":
        fail(
            "Put your Discord bot token in DISCORD_BOT_TOKEN "
            "or directly in the script."
        )

    if WEBHOOK_URL == "PUT_WEBHOOK_URL_HERE":
        fail(
            "Put your Discord webhook URL in "
            "DISCORD_WEBHOOK_URL or directly in the script."
        )

    if not WEBHOOK_URL.startswith(
        "https://discord.com/api/webhooks/"
    ):
        fail("That does not look like a Discord webhook URL.")

    print("Checking forum channel...")

    channel = get_channel()

    channel_type = channel.get("type")

    if channel_type != 15:
        fail(
            f"Channel {FORUM_CHANNEL_ID} is not a Forum channel. "
            f"Discord returned channel type {channel_type}."
        )

    guild_id = channel.get("guild_id")

    if not guild_id:
        fail("Could not determine the server ID.")

    print(
        f"Forum: #{channel.get('name', 'unknown')}"
    )

    print(
        f"Server ID: {guild_id}"
    )

    print("\nFinding active posts...")

    active = get_active_threads(guild_id)

    print(
        f"Active posts found: {len(active)}"
    )

    print("\nFinding archived posts...")

    archived = get_archived_threads()

    print(
        f"Archived posts found: {len(archived)}"
    )

    threads_by_id = {}

    for thread in active + archived:
        thread_id = str(thread.get("id"))

        if thread_id:
            threads_by_id[thread_id] = thread

    threads = list(threads_by_id.values())

    threads.sort(
        key=lambda x: int(x["id"])
    )

    print(
        f"\nTotal unique bug posts: {len(threads)}"
    )

    sent_ids = load_state()

    remaining = [
        thread
        for thread in threads
        if str(thread["id"]) not in sent_ids
    ]

    print(
        f"Already exported: {len(threads) - len(remaining)}"
    )

    print(
        f"Remaining: {len(remaining)}"
    )

    if MAX_REPORTS is not None:
        remaining = remaining[:MAX_REPORTS]

    if not remaining:
        print("\nNothing new to export.")
        return

    print("\nStarting export...\n")

    successful = 0
    failed = 0

    for index, thread in enumerate(
        remaining,
        start=1
    ):
        thread_id = str(thread["id"])
        title = thread.get(
            "name",
            "Untitled"
        )

        print(
            f"[{index}/{len(remaining)}] "
            f"{title} ({thread_id})"
        )

        try:
            message = get_thread_message(
                thread_id
            )

            payload = build_payload(
                thread,
                message
            )

            if DRY_RUN:
                print("  DRY RUN - not sending.")
                successful += 1
                continue

            if send_webhook(payload):
                sent_ids.add(thread_id)
                save_state(sent_ids)

                successful += 1
                print("  Sent.")

            else:
                failed += 1
                print("  Failed.")

        except Exception as error:
            failed += 1

            print(
                f"  ERROR: {error}"
            )

    print("\n==============================")
    print("EXPORT COMPLETE")
    print("==============================")
    print(f"Total posts: {len(threads)}")
    print(f"Sent: {successful}")
    print(f"Failed: {failed}")
    print(
        f"Skipped: {len(threads) - len(remaining)}"
    )


if __name__ == "__main__":
    main()
