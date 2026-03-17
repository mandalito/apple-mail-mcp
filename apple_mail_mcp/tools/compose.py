"""Composition tools: sending, replying, forwarding, and drafts."""

import os
import subprocess
import tempfile
from typing import Optional, List, Tuple


from apple_mail_mcp.server import mcp
from apple_mail_mcp.core import inject_preferences, escape_applescript, run_applescript, inbox_mailbox_script, build_mailbox_ref, validate_input


@mcp.tool()
@inject_preferences
def list_signatures() -> str:
    """
    List all available Mail signatures.

    Returns:
        List of signature names that can be used with the signature parameter
        in reply_to_email, compose_email, and forward_email.
    """
    script = '''
    tell application "Mail"
        set sigNames to name of every signature
        if (count of sigNames) is 0 then
            return "No signatures found."
        end if
        set outputText to "AVAILABLE SIGNATURES" & return & return
        repeat with i from 1 to count of sigNames
            set outputText to outputText & i & ". " & (item i of sigNames) & return
        end repeat
        return outputText
    end tell
    '''
    return run_applescript(script)


def _signature_script(signature: Optional[str], msg_var: str = "replyMessage") -> str:
    """Return AppleScript snippet to set a signature on an outgoing message."""
    if not signature:
        return f"-- Using default signature for {msg_var}"
    safe_sig = escape_applescript(signature)
    return f'''try
                    set message signature of {msg_var} to signature "{safe_sig}"
                on error
                    -- Signature not found, keep default
                end try'''


def _send_html_email(
    account: str,
    to: str,
    subject: str,
    body_plain: str,
    body_html: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    attachments_script: str = "",
    mode: str = "send",
) -> str:
    """Send an HTML-formatted email via NSPasteboard clipboard injection.

    Uses AppleScriptObjC to place HTML on the clipboard with the proper
    pasteboard type, creates a compose window, tabs into the body, and
    pastes.  Then sends, saves as draft, or leaves open for review.
    """
    safe_account = escape_applescript(account)
    escaped_subject = escape_applescript(subject)

    # Build recipient scripts
    to_lines = ""
    for addr in [a.strip() for a in to.split(",") if a.strip()]:
        to_lines += f'make new to recipient at end of to recipients with properties {{address:"{escape_applescript(addr)}"}}\n'

    cc_lines = ""
    if cc:
        for addr in [a.strip() for a in cc.split(",") if a.strip()]:
            cc_lines += f'make new cc recipient at end of cc recipients with properties {{address:"{escape_applescript(addr)}"}}\n'

    bcc_lines = ""
    if bcc:
        for addr in [a.strip() for a in bcc.split(",") if a.strip()]:
            bcc_lines += f'make new bcc recipient at end of bcc recipients with properties {{address:"{escape_applescript(addr)}"}}\n'

    # Mode-specific behaviour after paste
    if mode == "send":
        post_paste_script = '''
            -- Send via keyboard shortcut
            keystroke "d" using {command down, shift down}
        '''
        success_text = "Email sent successfully (HTML)"
    elif mode == "draft":
        post_paste_script = '''
            -- Save as draft: Cmd+S then close
            keystroke "s" using command down
            delay 0.5
        '''
        success_text = "Email saved as draft (HTML)"
    else:  # open
        post_paste_script = "-- Leaving open for review"
        success_text = "Email opened in Mail for review (HTML). Edit and send when ready."

    # Write HTML to temp file so the AppleScript can read it without
    # worrying about escaping quotes/special chars in the HTML string.
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", prefix="mail_html_",
        delete=False, encoding="utf-8",
    )
    tmp.write(body_html)
    tmp.close()
    html_temp_path = tmp.name
    os.chmod(html_temp_path, 0o600)

    script = f'''
use framework "Foundation"
use framework "AppKit"
use scripting additions

-- Step 1: Read HTML from temp file and place on clipboard
set htmlString to do shell script "cat " & quoted form of "{html_temp_path}"
set pb to current application's NSPasteboard's generalPasteboard()

-- Save current clipboard for restoration
set oldClip to pb's stringForType:(current application's NSPasteboardTypeString)

pb's clearContents()
set htmlData to (current application's NSString's stringWithString:htmlString)'s dataUsingEncoding:(current application's NSUTF8StringEncoding)
pb's setData:htmlData forType:(current application's NSPasteboardTypeHTML)

-- Step 2: Create compose window (empty body so signature doesn't interfere)
tell application "Mail"
    set newMsg to make new outgoing message with properties {{subject:"{escaped_subject}", content:"", visible:true}}
    set emailAddrs to email addresses of account "{safe_account}"
    set senderAddress to item 1 of emailAddrs
    set sender of newMsg to senderAddress
    tell newMsg
        {to_lines}
        {cc_lines}
        {bcc_lines}
        {attachments_script}
    end tell
    activate
end tell

-- Step 3: Wait for compose window to render
delay 2.5

-- Step 4: Tab from header fields into body, then paste
tell application "System Events"
    set frontmost of process "Mail" to true
    delay 0.5
    tell process "Mail"
        -- Tab through: To -> Cc -> Bcc -> Subject -> Body
        -- 7 tabs covers all combinations of visible/hidden CC/BCC fields
        repeat 7 times
            key code 48
            delay 0.1
        end repeat
        delay 0.3

        -- Select all in body and paste HTML
        keystroke "a" using command down
        delay 0.2
        keystroke "v" using command down
        delay 0.5

        {post_paste_script}
    end tell
end tell

-- Step 5: Clean up temp file
do shell script "rm -f " & quoted form of "{html_temp_path}"

-- Step 6: Restore clipboard
if oldClip is not missing value then
    pb's clearContents()
    pb's setString:oldClip forType:(current application's NSPasteboardTypeString)
end if

return "{success_text}"
'''

    try:
        result = subprocess.run(
            ["osascript", "-"],
            input=script.encode("utf-8"),
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            return f"Error sending HTML email: {stderr}"
        output = result.stdout.decode("utf-8", errors="replace").strip()
        # Build confirmation message
        confirm = f"{output}\n\nFrom: {account}\nTo: {to}\nSubject: {subject}"
        if cc:
            confirm += f"\nCC: {cc}"
        if bcc:
            confirm += f"\nBCC: {bcc}"
        return confirm
    except subprocess.TimeoutExpired:
        return "Error: HTML email script timed out"
    finally:
        if os.path.exists(html_temp_path):
            os.unlink(html_temp_path)


def _validate_attachment_paths(attachments: str) -> Tuple[List[str], Optional[str]]:
    """Validate and resolve attachment file paths.

    Splits comma-separated paths, expands tildes, resolves symlinks,
    and enforces security constraints (home-dir-only, no sensitive dirs,
    file must exist).

    Returns:
        A tuple of (resolved_paths, error_message).
        If error_message is not None, resolved_paths should be ignored.
    """
    home_dir = os.path.expanduser('~')
    sensitive_dirs = [
        os.path.join(home_dir, '.ssh'),
        os.path.join(home_dir, '.gnupg'),
        os.path.join(home_dir, '.config'),
        os.path.join(home_dir, '.aws'),
        os.path.join(home_dir, '.claude'),
        os.path.join(home_dir, 'Library', 'LaunchAgents'),
        os.path.join(home_dir, 'Library', 'LaunchDaemons'),
        os.path.join(home_dir, 'Library', 'Keychains'),
    ]

    resolved_paths: List[str] = []
    raw_paths = [p.strip() for p in attachments.split(',')]

    for raw_path in raw_paths:
        if not raw_path:
            continue

        # Expand tilde and resolve symlinks
        expanded = os.path.expanduser(raw_path)
        resolved = os.path.realpath(expanded)

        # Must be under the user's home directory
        if not resolved.startswith(home_dir + os.sep) and resolved != home_dir:
            return [], f"Error: Attachment path must be under your home directory ({home_dir}). Got: {resolved}"

        # Block sensitive directories
        for sensitive_dir in sensitive_dirs:
            if resolved.startswith(sensitive_dir + os.sep) or resolved == sensitive_dir:
                return [], f"Error: Cannot attach files from sensitive directory: {sensitive_dir}"

        # File must exist
        if not os.path.isfile(resolved):
            return [], f"Error: Attachment file does not exist: {resolved}"

        resolved_paths.append(resolved)

    if not resolved_paths:
        return [], "Error: No valid attachment paths provided."

    return resolved_paths, None


@mcp.tool()
@inject_preferences
def reply_to_email(
    account: str,
    subject_keyword: str,
    reply_body: str,
    reply_to_all: bool = False,
    mailbox: str = "INBOX",
    sender: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    send: bool = False,
    mode: Optional[str] = None,
    attachments: Optional[str] = None,
    signature: Optional[str] = None
) -> str:
    """
    Reply to an email matching a subject keyword.

    IMPORTANT: Defaults to saving as draft. Set mode="send" to send immediately,
    or mode="open" to open a compose window for review.

    Args:
        account: Account name (e.g., "Gmail", "Work")
        subject_keyword: Keyword to search for in email subjects
        reply_body: The body text of the reply
        reply_to_all: If True, reply to all recipients; if False, reply only to sender (default: False)
        mailbox: Mailbox to search in (default: "INBOX"). Use specific folder names like "Archive" to reply to archived emails.
        sender: Optional sender name or email to filter by (case-insensitive). Helps target a specific email in a thread.
        date_from: Optional start date filter, format "YYYY-MM-DD". Only match emails on or after this date.
        date_to: Optional end date filter, format "YYYY-MM-DD". Only match emails on or before this date.
        cc: Optional CC recipients, comma-separated for multiple
        bcc: Optional BCC recipients, comma-separated for multiple
        send: If False (default), save as draft; if True, send immediately. Ignored if mode is set.
        mode: Delivery mode — "draft" (save silently, default when send=False), "open" (open compose window for review), or "send" (send immediately — use with caution). Overrides send parameter when set.
        attachments: Optional file paths to attach, comma-separated for multiple (e.g., "/path/to/file1.png,/path/to/file2.pdf")
        signature: Optional signature name to use (from list_signatures). If not provided, uses account default.

    Returns:
        Confirmation message with details of the reply sent, saved draft, or opened draft
    """

    # Validate inputs
    try:
        validate_input(account, "account", max_length=200)
        validate_input(subject_keyword, "subject_keyword", max_length=1000)
        validate_input(reply_body, "reply_body", max_length=100000)
        if cc:
            validate_input(cc, "cc", max_length=1000)
        if bcc:
            validate_input(bcc, "bcc", max_length=1000)
    except ValueError as e:
        return f"Error: {e}"

    # Build date filter: convert YYYY-MM-DD to AppleScript date via short date string
    date_filter_setup = ""
    date_from_check = ""
    date_to_check = ""
    if date_from:
        # Parse YYYY-MM-DD to components and build AppleScript date
        parts = date_from.split("-")
        if len(parts) == 3:
            y, m, d = parts
            date_filter_setup += f'''
            set dateFrom to current date
            set year of dateFrom to {y}
            set month of dateFrom to {m}
            set day of dateFrom to {d}
            set hours of dateFrom to 0
            set minutes of dateFrom to 0
            set seconds of dateFrom to 0'''
            date_from_check = " and msgDate >= dateFrom"
    if date_to:
        parts = date_to.split("-")
        if len(parts) == 3:
            y, m, d = parts
            date_filter_setup += f'''
            set dateTo to current date
            set year of dateTo to {y}
            set month of dateTo to {m}
            set day of dateTo to {d}
            set hours of dateTo to 23
            set minutes of dateTo to 59
            set seconds of dateTo to 59'''
            date_to_check = " and msgDate <= dateTo"

    # Build sender filter (AppleScript `contains` is case-insensitive by default)
    sender_check = ""
    if sender:
        safe_sender_filter = escape_applescript(sender)
        sender_check = f' and msgSender contains "{safe_sender_filter}"'

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    safe_subject_keyword = escape_applescript(subject_keyword)

    # Write reply body to a temp file to avoid AppleScript string escaping
    # issues with special characters (em dashes, curly quotes, colons, etc.)
    body_tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", prefix="mail_reply_",
        delete=False, encoding="utf-8",
    )
    body_tmp.write(reply_body)
    body_tmp.close()
    body_temp_path = body_tmp.name
    os.chmod(body_temp_path, 0o600)

    # Build the reply command based on reply_to_all flag
    if reply_to_all:
        reply_command = 'set replyMessage to reply foundMessage with opening window and reply to all'
    else:
        reply_command = 'set replyMessage to reply foundMessage with opening window'

    # Build CC recipients if provided
    cc_script = ''
    if cc:
        cc_addresses = [addr.strip() for addr in cc.split(',')]
        for addr in cc_addresses:
            safe_addr = escape_applescript(addr)
            cc_script += f'''
            make new cc recipient at end of cc recipients of replyMessage with properties {{address:"{safe_addr}"}}
            '''

    # Build BCC recipients if provided
    bcc_script = ''
    if bcc:
        bcc_addresses = [addr.strip() for addr in bcc.split(',')]
        for addr in bcc_addresses:
            safe_addr = escape_applescript(addr)
            bcc_script += f'''
            make new bcc recipient at end of bcc recipients of replyMessage with properties {{address:"{safe_addr}"}}
            '''

    # Build attachment script if provided
    attachment_script = ''
    attachment_info = ''
    if attachments:
        validated_paths, error = _validate_attachment_paths(attachments)
        if error:
            return error
        for path in validated_paths:
            safe_path = escape_applescript(path)
            attachment_script += f'''
                set theFile to POSIX file "{safe_path}"
                make new attachment with properties {{file name:theFile}} at after the last paragraph
                delay 1
            '''
            attachment_info += f'  {path}\n'

    safe_cc = escape_applescript(cc) if cc else ""
    safe_bcc = escape_applescript(bcc) if bcc else ""
    safe_attachment_info = escape_applescript(attachment_info) if attachment_info else ""

    # Resolve delivery mode: mode parameter takes precedence over send boolean
    if mode is not None:
        if mode not in ("send", "draft", "open"):
            return f"Error: Invalid mode '{mode}'. Use: send, draft, open"
        effective_mode = mode
    else:
        effective_mode = "send" if send else "draft"

    # Read body from temp file in AppleScript (avoids all string escaping issues)
    read_body_script = f'set replyBodyText to do shell script "cat " & quoted form of "{body_temp_path}"'

    # Determine behavior per mode
    if effective_mode == "send":
        header_text = "SENDING REPLY"
        send_or_draft_command = "send replyMessage"
        success_text = "Reply sent successfully!"
        # For send, Mail handles the quoted original via the HTML layer
        set_content_script = 'set content of replyMessage to replyBodyText'
    elif effective_mode == "open":
        header_text = "OPENING REPLY FOR REVIEW"
        # For open, we use the clipboard to paste the reply body.
        # This preserves Mail.app's native quoted original
        # (setting content via AppleScript overwrites the async HTML layer).
        send_or_draft_command = f'''
                set visible of replyMessage to true
                activate
                delay 1.5
                -- Use clipboard to paste reply body (preserves quoted original)
                set the clipboard to replyBodyText
                tell application "System Events"
                    tell process "Mail"
                        keystroke "v" using command down
                    end tell
                end tell
                delay 0.5'''
        success_text = "Reply opened in Mail for review. Edit and send when ready."
        set_content_script = '-- content set via clipboard paste'
    else:  # draft
        header_text = "SAVING REPLY AS DRAFT"
        send_or_draft_command = "close window 1 saving yes"
        success_text = "Reply saved as draft!"
        # Set content to reply body only (no manual thread).
        # Mail places the signature right after the body in correct position.
        # The quoted thread is omitted here but visible in Mail's conversation
        # view when the user opens the draft to review and send.
        set_content_script = 'set content of replyMessage to replyBodyText'

    cleanup_script = f'do shell script "rm -f " & quoted form of "{body_temp_path}"'

    script = f'''
    tell application "Mail"
        set outputText to "{header_text}" & return & return

        try
            -- Read reply body from temp file (avoids AppleScript escaping issues)
            {read_body_script}

            set targetAccount to account "{safe_account}"
            {build_mailbox_ref(mailbox, "targetAccount", "targetMailbox")}
            set inboxMessages to every message of targetMailbox
            set foundMessage to missing value
            {date_filter_setup}

            -- Find the first matching message
            repeat with aMessage in inboxMessages
                try
                    set messageSubject to subject of aMessage
                    set msgDate to date received of aMessage
                    set msgSender to sender of aMessage

                    if messageSubject contains "{safe_subject_keyword}"{sender_check}{date_from_check}{date_to_check} then
                        set foundMessage to aMessage
                        exit repeat
                    end if
                end try
            end repeat

            if foundMessage is not missing value then
                set messageSubject to subject of foundMessage
                set messageSender to sender of foundMessage
                set messageDate to date received of foundMessage

                -- Create reply
                {reply_command}
                delay 0.5

                -- Ensure the reply is from the correct account
                set emailAddrs to email addresses of targetAccount
                set senderAddress to item 1 of emailAddrs
                set sender of replyMessage to senderAddress

                -- Set signature
                {_signature_script(signature, "replyMessage")}

                -- Set reply content
                {set_content_script}
                delay 0.5

                -- Add CC/BCC recipients
                {cc_script}
                {bcc_script}

                -- Add attachments
                {attachment_script}

                -- Send or save as draft
                {send_or_draft_command}

                set outputText to outputText & "{success_text}" & return & return
                set outputText to outputText & "Original email:" & return
                set outputText to outputText & "  Subject: " & messageSubject & return
                set outputText to outputText & "  From: " & messageSender & return
                set outputText to outputText & "  Date: " & (messageDate as string) & return & return
                set outputText to outputText & "Reply body:" & return
                set outputText to outputText & "  " & replyBodyText & return
    '''

    if cc:
        script += f'''
                set outputText to outputText & "CC: {safe_cc}" & return
    '''

    if bcc:
        script += f'''
                set outputText to outputText & "BCC: {safe_bcc}" & return
    '''

    if attachments:
        script += f'''
                set outputText to outputText & "Attachments:" & return & "{safe_attachment_info}" & return
    '''

    script += f'''
            else
                set outputText to outputText & "No email found matching: {safe_subject_keyword}" & return
            end if

            -- Clean up temp file
            {cleanup_script}

        on error errMsg
            -- Clean up temp file even on error
            try
                {cleanup_script}
            end try
            return "Error: " & errMsg & return & "Please check that the account name is correct and the email exists."
        end try

        return outputText
    end tell
    '''

    try:
        result = run_applescript(script)
        return result
    finally:
        # Belt-and-suspenders cleanup in case AppleScript didn't run
        if os.path.exists(body_temp_path):
            os.unlink(body_temp_path)


@mcp.tool()
@inject_preferences
def compose_email(
    account: str,
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    attachments: Optional[str] = None,
    mode: str = "draft",
    body_html: Optional[str] = None,
    signature: Optional[str] = None
) -> str:
    """
    Compose a new email from a specific account.

    IMPORTANT: Defaults to saving as draft. Set mode="send" to send immediately,
    or mode="open" to open a compose window for review.

    Args:
        account: Account name to send from (e.g., "Gmail", "Work", "Personal")
        to: Recipient email address(es), comma-separated for multiple
        subject: Email subject line
        body: Email body text (used as plain-text fallback when body_html is provided)
        cc: Optional CC recipients, comma-separated for multiple
        bcc: Optional BCC recipients, comma-separated for multiple
        attachments: Optional file paths to attach, comma-separated for multiple (e.g., "/path/to/file1.png,/path/to/file2.pdf")
        mode: Delivery mode — "draft" (save to Drafts, default), "open" (open compose window for review), or "send" (send immediately — use with caution)
        body_html: Optional HTML body for rich formatting (bold, headings, links, colors). When provided, the email is sent as HTML. The plain 'body' field is still required as fallback text.
        signature: Optional signature name to use (from list_signatures). If not provided, uses account default.

    Returns:
        Confirmation message with details of the email
    """

    # Validate mode
    if mode not in ("send", "draft", "open"):
        return f"Error: Invalid mode '{mode}'. Use: send, draft, open"

    # Validate inputs to prevent oversized or malformed payloads
    try:
        validate_input(account, "account", max_length=200)
        validate_input(to, "to", max_length=1000)
        validate_input(subject, "subject", max_length=1000)
        validate_input(body, "body", max_length=100000)
        if cc:
            validate_input(cc, "cc", max_length=1000)
        if bcc:
            validate_input(bcc, "bcc", max_length=1000)
    except ValueError as e:
        return f"Error: {e}"

    # Validate and resolve attachments early
    attachment_script = ''
    attachment_info = ''
    if attachments:
        validated_paths, error = _validate_attachment_paths(attachments)
        if error:
            return error
        for path in validated_paths:
            safe_path = escape_applescript(path)
            attachment_script += f'''
                set theFile to POSIX file "{safe_path}"
                make new attachment with properties {{file name:theFile}} at after the last paragraph
                delay 1
            '''
            attachment_info += f'  {path}\n'

    # --- HTML path: use NSPasteboard clipboard injection ---
    if body_html:
        return _send_html_email(
            account=account,
            to=to,
            subject=subject,
            body_plain=body,
            body_html=body_html,
            cc=cc,
            bcc=bcc,
            attachments_script=attachment_script,
            mode=mode,
        )

    # --- Plain-text path: existing AppleScript approach ---
    safe_account = escape_applescript(account)
    escaped_subject = escape_applescript(subject)
    escaped_body = escape_applescript(body)

    # Build TO recipients (split comma-separated addresses)
    to_script = ''
    to_addresses = [addr.strip() for addr in to.split(',')]
    for addr in to_addresses:
        safe_addr = escape_applescript(addr)
        to_script += f'''
                make new to recipient at end of to recipients with properties {{address:"{safe_addr}"}}
        '''

    # Build CC recipients if provided
    cc_script = ''
    if cc:
        cc_addresses = [addr.strip() for addr in cc.split(',')]
        for addr in cc_addresses:
            safe_addr = escape_applescript(addr)
            cc_script += f'''
                make new cc recipient at end of cc recipients with properties {{address:"{safe_addr}"}}
            '''

    # Build BCC recipients if provided
    bcc_script = ''
    if bcc:
        bcc_addresses = [addr.strip() for addr in bcc.split(',')]
        for addr in bcc_addresses:
            safe_addr = escape_applescript(addr)
            bcc_script += f'''
                make new bcc recipient at end of bcc recipients with properties {{address:"{safe_addr}"}}
            '''

    safe_to = escape_applescript(to)
    safe_cc = escape_applescript(cc) if cc else ""
    safe_bcc = escape_applescript(bcc) if bcc else ""
    safe_attachment_info = escape_applescript(attachment_info) if attachment_info else ""

    # Determine behavior per mode
    if mode == "send":
        header_text = "COMPOSING EMAIL"
        visible = "false"
        send_command = "send newMessage"
        success_text = "✓ Email sent successfully!"
    elif mode == "open":
        header_text = "OPENING EMAIL FOR REVIEW"
        visible = "true"
        send_command = "activate"
        success_text = "✓ Email opened in Mail for review. Edit and send when ready."
    else:  # draft
        header_text = "SAVING EMAIL AS DRAFT"
        visible = "false"
        send_command = "close window 1 saving yes"
        success_text = "✓ Email saved as draft!"

    script = f'''
    tell application "Mail"
        set outputText to "{header_text}" & return & return

        try
            set targetAccount to account "{safe_account}"

            -- Create new outgoing message
            set newMessage to make new outgoing message with properties {{subject:"{escaped_subject}", content:"{escaped_body}", visible:{visible}}}

            -- Set the sender account
            set emailAddrs to email addresses of targetAccount
            set senderAddress to item 1 of emailAddrs
            set sender of newMessage to senderAddress

            -- Set signature
            {_signature_script(signature, "newMessage")}

            -- Add TO/CC/BCC recipients
            tell newMessage
                {to_script}
                {cc_script}
                {bcc_script}
            end tell

            -- Add attachments
            tell newMessage
                {attachment_script}
            end tell

            -- Send, save as draft, or leave open for review
            {send_command}

            set outputText to outputText & "{success_text}" & return & return
            set outputText to outputText & "From: " & name of targetAccount & return
            set outputText to outputText & "To: {safe_to}" & return
    '''

    if cc:
        script += f'''
            set outputText to outputText & "CC: {safe_cc}" & return
    '''

    if bcc:
        script += f'''
            set outputText to outputText & "BCC: {safe_bcc}" & return
    '''

    if attachments:
        script += f'''
            set outputText to outputText & "Attachments:" & return & "{safe_attachment_info}" & return
    '''

    script += f'''
            set outputText to outputText & "Subject: {escaped_subject}" & return
            set outputText to outputText & "Body: " & "{escaped_body}" & return

        on error errMsg
            return "Error: " & errMsg & return & "Please check that the account name and email addresses are correct."
        end try

        return outputText
    end tell
    '''

    result = run_applescript(script)
    return result


@mcp.tool()
@inject_preferences
def forward_email(
    account: str,
    subject_keyword: str,
    to: str,
    message: Optional[str] = None,
    mailbox: str = "INBOX",
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    mode: str = "draft",
    signature: Optional[str] = None,
) -> str:
    """
    Forward an email to one or more recipients.

    IMPORTANT: Defaults to saving as draft. Set mode="send" to send immediately,
    or mode="open" to open a compose window for review.

    Args:
        account: Account name (e.g., "Gmail", "Work")
        subject_keyword: Keyword to search for in email subjects
        to: Recipient email address(es), comma-separated for multiple
        message: Optional message to add before forwarded content
        mailbox: Mailbox to search in (default: "INBOX")
        cc: Optional CC recipients, comma-separated for multiple
        bcc: Optional BCC recipients, comma-separated for multiple
        mode: Delivery mode — "draft" (save to Drafts, default), "open" (open compose window for review), or "send" (send immediately — use with caution)
        signature: Optional signature name to use (from list_signatures). If not provided, uses account default.

    Returns:
        Confirmation message with details of forwarded email
    """

    # Validate mode
    if mode not in ("send", "draft", "open"):
        return f"Error: Invalid mode '{mode}'. Use: draft, open, send"

    # Validate inputs
    try:
        validate_input(account, "account", max_length=200)
        validate_input(subject_keyword, "subject_keyword", max_length=1000)
        validate_input(to, "to", max_length=1000)
        validate_input(mailbox, "mailbox", max_length=500)
        if message:
            validate_input(message, "message", max_length=100000)
        if cc:
            validate_input(cc, "cc", max_length=1000)
        if bcc:
            validate_input(bcc, "bcc", max_length=1000)
    except ValueError as e:
        return f"Error: {e}"

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    safe_subject_keyword = escape_applescript(subject_keyword)
    safe_to = escape_applescript(to)
    safe_mailbox = escape_applescript(mailbox)
    escaped_message = escape_applescript(message) if message else ""

    # Build CC recipients if provided
    cc_script = ''
    if cc:
        cc_addresses = [addr.strip() for addr in cc.split(',')]
        for addr in cc_addresses:
            safe_addr = escape_applescript(addr)
            cc_script += f'''
            make new cc recipient at end of cc recipients of forwardMessage with properties {{address:"{safe_addr}"}}
            '''

    # Build BCC recipients if provided
    bcc_script = ''
    if bcc:
        bcc_addresses = [addr.strip() for addr in bcc.split(',')]
        for addr in bcc_addresses:
            safe_addr = escape_applescript(addr)
            bcc_script += f'''
            make new bcc recipient at end of bcc recipients of forwardMessage with properties {{address:"{safe_addr}"}}
            '''

    safe_cc = escape_applescript(cc) if cc else ""
    safe_bcc = escape_applescript(bcc) if bcc else ""

    # Build TO recipients (split comma-separated)
    to_script = ''
    to_addresses = [addr.strip() for addr in to.split(',')]
    for addr in to_addresses:
        safe_addr = escape_applescript(addr)
        to_script += f'''
                make new to recipient at end of to recipients of forwardMessage with properties {{address:"{safe_addr}"}}
        '''

    # Determine behavior per mode
    if mode == "send":
        header_text = "FORWARDING EMAIL"
        send_or_draft_command = "send forwardMessage"
        success_text = "Email forwarded successfully!"
    elif mode == "open":
        header_text = "OPENING FORWARD FOR REVIEW"
        send_or_draft_command = """set visible of forwardMessage to true
                activate"""
        success_text = "Forward opened in Mail for review. Edit and send when ready."
    else:  # draft
        header_text = "SAVING FORWARD AS DRAFT"
        send_or_draft_command = "close window 1 saving yes"
        success_text = "Forward saved as draft!"

    script = f'''
    tell application "Mail"
        set outputText to "{header_text}" & return & return

        try
            set targetAccount to account "{safe_account}"
            -- Get mailbox (locale-aware inbox fallback)
            {build_mailbox_ref(mailbox, "targetAccount", "targetMailbox")}

            set mailboxMessages to every message of targetMailbox
            set foundMessage to missing value

            -- Find the first matching message
            repeat with aMessage in mailboxMessages
                try
                    set messageSubject to subject of aMessage

                    if messageSubject contains "{safe_subject_keyword}" then
                        set foundMessage to aMessage
                        exit repeat
                    end if
                end try
            end repeat

            if foundMessage is not missing value then
                set messageSubject to subject of foundMessage
                set messageSender to sender of foundMessage
                set messageDate to date received of foundMessage

                -- Create forward
                set forwardMessage to forward foundMessage with opening window

                -- Set sender account
                set emailAddrs to email addresses of targetAccount
                set senderAddress to item 1 of emailAddrs
                set sender of forwardMessage to senderAddress

                -- Set signature
                {_signature_script(signature, "forwardMessage")}

                -- Add recipients
                {to_script}

                -- Add CC/BCC recipients
                {cc_script}
                {bcc_script}

                -- Add optional message
                if "{escaped_message}" is not "" then
                    set content of forwardMessage to "{escaped_message}" & return & return & content of forwardMessage
                end if

                -- Send, save as draft, or leave open for review
                {send_or_draft_command}

                set outputText to outputText & "{success_text}" & return & return
                set outputText to outputText & "Original email:" & return
                set outputText to outputText & "  Subject: " & messageSubject & return
                set outputText to outputText & "  From: " & messageSender & return
                set outputText to outputText & "  Date: " & (messageDate as string) & return & return
                set outputText to outputText & "Forwarded to: {safe_to}" & return
    '''

    if cc:
        script += f'''
                set outputText to outputText & "CC: {safe_cc}" & return
    '''

    if bcc:
        script += f'''
                set outputText to outputText & "BCC: {safe_bcc}" & return
    '''

    script += f'''
            else
                set outputText to outputText & "⚠ No email found matching: {safe_subject_keyword}" & return
            end if

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''

    result = run_applescript(script)
    return result


@mcp.tool()
@inject_preferences
def manage_drafts(
    account: str,
    action: str,
    subject: Optional[str] = None,
    to: Optional[str] = None,
    body: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    draft_subject: Optional[str] = None,
    confirm_send: bool = False,
) -> str:
    """
    Manage draft emails - list, create, send, open, or delete drafts.

    IMPORTANT: The "send" action requires confirm_send=True as a safety gate.
    Prefer "open" to review drafts before sending manually.

    Args:
        account: Account name (e.g., "Gmail", "Work")
        action: Action to perform: "list", "create", "send", "open", "delete". Use "open" to open a draft in a visible compose window for review before sending.
        subject: Email subject (required for create)
        to: Recipient email(s) for create (comma-separated)
        body: Email body (required for create)
        cc: Optional CC recipients for create
        bcc: Optional BCC recipients for create
        draft_subject: Subject keyword to find draft (required for send/open/delete)
        confirm_send: Must be True to execute the "send" action (safety confirmation)

    Returns:
        Formatted output based on action
    """

    # Escape account for all paths
    safe_account = escape_applescript(account)

    if action == "list":
        script = f'''
        tell application "Mail"
            set outputText to "DRAFT EMAILS - {safe_account}" & return & return

            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                set draftMessages to every message of draftsMailbox
                set draftCount to count of draftMessages

                set outputText to outputText & "Found " & draftCount & " draft(s)" & return & return

                repeat with aDraft in draftMessages
                    try
                        set draftSubject to subject of aDraft
                        set draftDate to date sent of aDraft

                        set outputText to outputText & "✉ " & draftSubject & return
                        set outputText to outputText & "   Created: " & (draftDate as string) & return & return
                    end try
                end repeat

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif action == "create":
        if not subject or not to or not body:
            return "Error: 'subject', 'to', and 'body' are required for creating drafts"

        escaped_subject = escape_applescript(subject)
        escaped_body = escape_applescript(body)
        safe_to = escape_applescript(to)

        # Build TO recipients (split comma-separated)
        to_script = ''
        to_addresses = [addr.strip() for addr in to.split(',')]
        for addr in to_addresses:
            safe_addr = escape_applescript(addr)
            to_script += f'''
                    make new to recipient at end of to recipients with properties {{address:"{safe_addr}"}}
            '''

        # Build CC recipients if provided
        cc_script = ''
        if cc:
            cc_addresses = [addr.strip() for addr in cc.split(',')]
            for addr in cc_addresses:
                safe_addr = escape_applescript(addr)
                cc_script += f'''
                    make new cc recipient at end of cc recipients with properties {{address:"{safe_addr}"}}
                '''

        # Build BCC recipients if provided
        bcc_script = ''
        if bcc:
            bcc_addresses = [addr.strip() for addr in bcc.split(',')]
            for addr in bcc_addresses:
                safe_addr = escape_applescript(addr)
                bcc_script += f'''
                    make new bcc recipient at end of bcc recipients with properties {{address:"{safe_addr}"}}
                '''

        script = f'''
        tell application "Mail"
            set outputText to "CREATING DRAFT" & return & return

            try
                set targetAccount to account "{safe_account}"

                -- Create new outgoing message (draft)
                set newDraft to make new outgoing message with properties {{subject:"{escaped_subject}", content:"{escaped_body}", visible:false}}

                -- Set the sender account
                set emailAddrs to email addresses of targetAccount
                set senderAddress to item 1 of emailAddrs
                set sender of newDraft to senderAddress

                -- Add recipients
                tell newDraft
                    {to_script}
                    {cc_script}
                    {bcc_script}
                end tell

                -- Save to drafts (don't send)
                -- The draft is automatically saved to Drafts folder

                set outputText to outputText & "✓ Draft created successfully!" & return & return
                set outputText to outputText & "Subject: {escaped_subject}" & return
                set outputText to outputText & "To: {safe_to}" & return

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif action == "send":
        if not draft_subject:
            return "Error: 'draft_subject' is required for sending drafts"
        if not confirm_send:
            return (
                "Safety gate: sending a draft requires confirm_send=True. "
                "Consider using action='open' to review the draft in Mail first."
            )

        safe_draft_subject = escape_applescript(draft_subject)

        script = f'''
        tell application "Mail"
            set outputText to "SENDING DRAFT" & return & return

            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                set draftMessages to every message of draftsMailbox
                set foundDraft to missing value

                -- Find the draft
                repeat with aDraft in draftMessages
                    try
                        set draftSubject to subject of aDraft

                        if draftSubject contains "{safe_draft_subject}" then
                            set foundDraft to aDraft
                            exit repeat
                        end if
                    end try
                end repeat

                if foundDraft is not missing value then
                    set draftSubject to subject of foundDraft

                    -- Send the draft
                    send foundDraft

                    set outputText to outputText & "✓ Draft sent successfully!" & return
                    set outputText to outputText & "Subject: " & draftSubject & return

                else
                    set outputText to outputText & "⚠ No draft found matching: {safe_draft_subject}" & return
                end if

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif action == "open":
        if not draft_subject:
            return "Error: 'draft_subject' is required for opening drafts"

        safe_draft_subject = escape_applescript(draft_subject)

        script = f'''
        tell application "Mail"
            set outputText to "OPENING DRAFT FOR REVIEW" & return & return

            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                set draftMessages to every message of draftsMailbox
                set foundDraft to missing value

                -- Find the draft
                repeat with aDraft in draftMessages
                    try
                        set draftSubject to subject of aDraft

                        if draftSubject contains "{safe_draft_subject}" then
                            set foundDraft to aDraft
                            exit repeat
                        end if
                    end try
                end repeat

                if foundDraft is not missing value then
                    set draftSubject to subject of foundDraft

                    -- Open the draft in a visible compose window
                    set draftWindow to open foundDraft
                    activate

                    set outputText to outputText & "✓ Draft opened in Mail for review!" & return
                    set outputText to outputText & "Subject: " & draftSubject & return
                    set outputText to outputText & return & "Edit and send when ready." & return

                else
                    set outputText to outputText & "⚠ No draft found matching: {safe_draft_subject}" & return
                end if

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif action == "delete":
        if not draft_subject:
            return "Error: 'draft_subject' is required for deleting drafts"

        safe_draft_subject = escape_applescript(draft_subject)

        script = f'''
        tell application "Mail"
            set outputText to "DELETING DRAFT" & return & return

            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                set draftMessages to every message of draftsMailbox
                set foundDraft to missing value

                -- Find the draft
                repeat with aDraft in draftMessages
                    try
                        set draftSubject to subject of aDraft

                        if draftSubject contains "{safe_draft_subject}" then
                            set foundDraft to aDraft
                            exit repeat
                        end if
                    end try
                end repeat

                if foundDraft is not missing value then
                    set draftSubject to subject of foundDraft

                    -- Delete the draft
                    delete foundDraft

                    set outputText to outputText & "✓ Draft deleted successfully!" & return
                    set outputText to outputText & "Subject: " & draftSubject & return

                else
                    set outputText to outputText & "⚠ No draft found matching: {safe_draft_subject}" & return
                end if

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    else:
        return f"Error: Invalid action '{action}'. Use: list, create, send, open, delete"

    result = run_applescript(script)
    return result
