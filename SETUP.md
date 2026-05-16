# Setup Guide

## 1. Python Server (required for SMS)

### Install dependencies (one-time)
```
pip3 install flask flask-cors requests
```

### Run
```
cd /Users/edzhong/codeproj/helloworldproj
python3 server.py
```

Then open **http://localhost:5001** in Chrome. Do not open `contacts.html` as a local file — SMS will not work.

### Twilio credentials
Store these in your `.env` file:
```
TWILIO_SID=your_account_sid
TWILIO_TOKEN=your_auth_token
TWILIO_FROM=your_twilio_number
```

### Twilio trial account limitation
Trial accounts can only send SMS to **verified phone numbers**. To add a verified number:
1. Go to twilio.com/console
2. Phone Numbers → Manage → Verified Caller IDs
3. Add and verify each recipient number

To remove this restriction, upgrade your Twilio account (add a credit card and funds).

---

## 2. EmailJS (required for Send Email)

Email sending is not yet configured. To enable it:

1. Sign up at **emailjs.com** (free tier: 200 emails/month)
2. Connect your email account as a service (Yahoo, Gmail, etc.)
3. Create a template with these variables:
   - **To:** `{{to_email}}`
   - **Subject:** `{{subject}}`
   - **Body:** (leave empty)
4. Copy your credentials and fill them in `contacts.html`:

```js
const EMAILJS_PUBLIC_KEY  = 'YOUR_EMAILJS_PUBLIC_KEY';   // Account → API Keys
const EMAILJS_SERVICE_ID  = 'YOUR_EMAILJS_SERVICE_ID';   // Email Services tab
const EMAILJS_TEMPLATE_ID = 'YOUR_EMAILJS_TEMPLATE_ID';  // Email Templates tab
```

---

## 3. File Save (write-back to original .vcf)

Requires **Chrome** (File System Access API). When you load a file and click Save, Chrome will write the edits directly back to the original `.vcf` file on disk.

Safari does not support this API — it falls back to downloading `contacts_edited.vcf` instead.
