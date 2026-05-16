# Contacts Search

A single-file browser app for searching, editing, and messaging contacts from a `.vcf` (vCard) file.

## Features

| Feature | Description |
|---|---|
| Load contacts | Opens a `.vcf` file via the browser File System Access API (Chrome) |
| Search | Real-time search by name, phone, or email |
| Edit contact | Click any contact to edit name, phones, emails, organization |
| Save to file | Edits write back directly to the original `.vcf` file (Chrome only) |
| Download `.vcf` | Downloads a copy of all contacts as `contacts_edited.vcf` |
| Send Text | Sends an SMS ("test from EZ") to the contact's first phone number via Twilio |
| Send Email | Sends an email (subject: "test from EZ") to the contact's first email via EmailJS |
| Background cycle | Cycles the page background color through the rainbow |
| Home button | Returns to `index.html` |

## Files

```
contacts.html   — the app (open via http://localhost:5001)
server.py       — local Python proxy server required for SMS sending
README.md       — this file
SETUP.md        — credential setup and server instructions
```

## Quick Start

1. Install dependencies (one-time): see `SETUP.md`
2. Run the server:
   ```
   python3 server.py
   ```
3. Open **http://localhost:5001** in Chrome
4. Click **Load Contacts (.vcf)** and select your vCard file
5. Search, click a contact to edit or send

## Exporting contacts from macOS

In the Contacts app: **File → Export → Export vCard…**

## Notes

- **Chrome required** for write-back save (File System Access API). Safari falls back to download.
- **SMS** requires the local server to be running (`python3 server.py`).
- **Twilio trial accounts** can only send SMS to verified phone numbers. See `SETUP.md`.
- **EmailJS** credentials must be filled in `contacts.html` before email sending works.
