# Apple Mail MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io)
[![GitHub stars](https://img.shields.io/github/stars/patrickfreyer/apple-mail-mcp?style=social)](https://github.com/patrickfreyer/apple-mail-mcp/stargazers)

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=patrickfreyer/apple-mail-mcp&type=Date)](https://star-history.com/#patrickfreyer/apple-mail-mcp&Date)

An MCP server that gives AI assistants full access to Apple Mail -- read, search, compose, organize, and analyze emails via natural language. Built with [FastMCP](https://github.com/jlowin/fastmcp).

**Locale-aware:** Works with localized mailbox names (English, French, German, Spanish, Italian, and more).

## Quick Start

**Prerequisites:** macOS with Apple Mail configured, Python 3.10+

```bash
git clone https://github.com/patrickfreyer/apple-mail-mcp.git
cd apple-mail-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/path/to/apple-mail-mcp/.venv/bin/python3",
      "args": ["-m", "apple_mail_mcp"]
    }
  }
}
```

Restart Claude Desktop and grant Mail.app permissions when prompted.

> **Tip:** An `.mcpb` bundle is also available on the [Releases](https://github.com/patrickfreyer/apple-mail-mcp/releases) page for one-click install in Claude Desktop.

## Tools (28)

### Reading & Search
| Tool | Description |
|------|-------------|
| `list_accounts` | List all configured Mail accounts |
| `list_inbox_emails` | List emails with account/read-status filtering |
| `get_inbox_overview` | Dashboard with unread counts, folders, and recent emails |
| `get_unread_count` | Unread count per account |
| `get_recent_emails` | Recent emails from a specific account |
| `search_emails` | Unified search with filters: subject, sender, dates, body, attachments, flagged, newsletters, threads |

### Composition
| Tool | Description |
|------|-------------|
| `compose_email` | Compose new emails (TO, CC, BCC) with plain text or HTML body. Defaults to draft mode. |
| `reply_to_email` | Reply or reply-all with optional CC/BCC, signature selection, and precise email targeting via sender/date filters |
| `forward_email` | Forward with optional message, CC/BCC, and signature selection |
| `manage_drafts` | Create, list, send, open, and delete drafts |
| `list_signatures` | List all available Mail signatures for use in compose/reply/forward |

### Organization
| Tool | Description |
|------|-------------|
| `list_mailboxes` | Folder hierarchy with message counts |
| `create_mailbox` | Create new mailboxes/folders |
| `move_email` | Move emails between folders (supports nested paths like `Projects/2024`) |
| `mark_emails` | Batch mark read/unread, flagged/unflagged with filters |
| `update_email_status` | Update read/flag status for individual emails |
| `archive_emails` | Archive emails matching filters |
| `delete_emails` | Soft delete (move to Trash) with dry-run preview |
| `manage_trash` | Soft delete, permanent delete, empty trash |

### Bulk Operations
| Tool | Description |
|------|-------------|
| `bulk_move_emails` | Move multiple emails matching filters to a destination mailbox |

### Smart Inbox
| Tool | Description |
|------|-------------|
| `get_awaiting_reply` | Find emails you're waiting for a reply on |
| `get_needs_response` | Prioritized list of emails that need your response |
| `get_top_senders` | Top senders by email volume |

### Attachments
| Tool | Description |
|------|-------------|
| `list_email_attachments` | List attachments with names and sizes |
| `save_email_attachment` | Save attachments to disk |

### Analytics & Export
| Tool | Description |
|------|-------------|
| `get_statistics` | Email analytics (volume, top senders, read ratios) |
| `export_emails` | Export single emails or mailboxes to TXT/HTML |
| `inbox_dashboard` | Interactive UI dashboard (requires mcp-ui-server) |

## Key Features

### Signature Selection

List available signatures and use them in any compose/reply/forward operation:

```
List my signatures
Reply to the LNS email with my ALESI signature
```

### Precise Email Targeting

Reply to specific emails in a thread using sender and date/time filters:

```
Reply to Christophe's email from March 13 about the data request
```

The `reply_to_email` tool supports:
- **`mailbox`** -- search in any folder (Inbox, Archive, Sent, etc.)
- **`sender`** -- filter by sender name or email (case-insensitive)
- **`date_from` / `date_to`** -- filter by date (`YYYY-MM-DD`) or date+time (`YYYY-MM-DD HH:MM`)

### Delivery Modes

All composition tools support three modes:
- **`draft`** (default) -- silently saves to Drafts
- **`open`** -- opens a compose window in Mail for review before sending (preserves full thread and signature)
- **`send`** -- sends immediately (use with caution)

### Safety Features

- Destructive operations (`delete_emails`, `bulk_move_emails`) default to **dry-run mode**
- `manage_drafts` send action requires explicit `confirm_send=True`
- Input validation prevents oversized or malformed payloads
- Conservative batch limits (configurable per call)

## Configuration

### User Preferences (Optional)

Set the `USER_EMAIL_PREFERENCES` environment variable to give the assistant context about your workflow:

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/path/to/.venv/bin/python3",
      "args": ["-m", "apple_mail_mcp"],
      "env": {
        "USER_EMAIL_PREFERENCES": "Default to Exchange account, show max 50 emails, prefer Archive and Projects folders"
      }
    }
  }
}
```

## Usage Examples

```
Show me an overview of my inbox
Search for emails about "project update" in my Exchange account
Reply to the latest email from Christophe about the LNS challenge
Forward the invoice email to my accountant with a note
List my signatures
Show me emails I haven't replied to yet
Archive all read newsletters older than 7 days
```

## Requirements

- macOS with Apple Mail configured
- Python 3.10+
- `fastmcp` (+ optional `mcp-ui-server` for dashboard)
- Claude Desktop, Claude Code, or any MCP-compatible client
- Mail.app permissions: Automation + Mail Data Access (grant in **System Settings > Privacy & Security > Automation**)

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `No module named apple_mail_mcp` | Run `pip install -e .` in the project directory |
| Mail.app not responding | Ensure Mail.app is running; check Automation permissions in System Settings |
| Inbox mailbox not found | Locale-aware resolution handles most cases; if your language is missing, open an issue |
| Slow searches | Set `include_content: false` and lower `max_results` |
| Mailbox not found | Use exact folder names; nested folders use `/` separator (e.g., `Projects/Alpha`) |
| Permission errors | Grant access in **System Settings > Privacy & Security > Automation** |

## Project Structure

```
apple-mail-mcp/
├── apple_mail_mcp/
│   ├── __init__.py
│   ├── __main__.py            # Entry point for python -m
│   ├── server.py              # FastMCP server setup
│   ├── core.py                # AppleScript helpers, escaping, validation
│   ├── constants.py           # Shared constants
│   └── tools/
│       ├── inbox.py           # Inbox listing and overview
│       ├── search.py          # Unified email search
│       ├── compose.py         # Compose, reply, forward, signatures
│       ├── manage.py          # Move, archive, trash, mailbox management
│       ├── bulk.py            # Bulk operations (mark, delete, move)
│       ├── analytics.py       # Statistics and export
│       └── smart_inbox.py     # Awaiting reply, needs response, top senders
├── ui/
│   └── dashboard.py           # Interactive HTML dashboard
├── pyproject.toml             # Package configuration
├── requirements.txt
├── CHANGELOG.md
├── LICENSE
└── README.md
```

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit and push
4. Open a Pull Request

## License

MIT -- see [LICENSE](LICENSE).

## Links

- [Changelog](CHANGELOG.md)
- [Issues](https://github.com/patrickfreyer/apple-mail-mcp/issues)
- [Discussions](https://github.com/patrickfreyer/apple-mail-mcp/discussions)
- [FastMCP](https://github.com/jlowin/fastmcp)
- [Model Context Protocol](https://modelcontextprotocol.io)
