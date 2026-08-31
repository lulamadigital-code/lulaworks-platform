# WhatsApp Marketing — Setup Guide

LulaWorks sends WhatsApp campaigns through the **Meta WhatsApp Cloud API**. The
golden rule of the design: **every company sends from its own WhatsApp number** —
there is no shared LulaWorks number. So setup has two distinct parts:

1. **Platform setup** — done **once** by the LulaWorks admin. Registers one Meta
   "Tech Provider" app so companies can connect with one click (Embedded Signup).
2. **Per-company setup** — done by **each company owner**. Connects their own
   WhatsApp Business number and gets their message templates approved.

---

## Part 1 — Platform setup (LulaWorks admin, once)

You are creating a **Meta developer App**, not a phone number.

### 1. Create the Meta App
- Go to <https://developers.facebook.com/apps> → **Create App** → type **Business**.
- Note the **App ID** and **App Secret** (Settings → Basic).

### 2. Add WhatsApp + Facebook Login
- In the app, **Add product → WhatsApp**.
- **Add product → Facebook Login for Business** (this powers Embedded Signup).

### 3. Configure Embedded Signup
- In **WhatsApp → Embedded Signup** (or Facebook Login for Business →
  *Configurations*), create a **configuration** with the permissions:
  - `whatsapp_business_management`
  - `whatsapp_business_messaging`
- Copy the **Configuration ID** — this is `WHATSAPP_CONFIG_ID`.

### 4. Allow the LulaWorks domain
- Facebook Login → **Settings**: add `https://www.lulaworks.com/` to **Valid OAuth
  Redirect URIs** and the app domain to **Allowed Domains**.

### 5. Set the ENV variables (on the server)
Add to `.env.prod` (and restart `web`):

```
META_APP_ID=<your app id>
META_APP_SECRET=<your app secret>
WHATSAPP_CONFIG_ID=<your embedded-signup configuration id>
WHATSAPP_WEBHOOK_VERIFY_TOKEN=<any secret string you choose>
# optional; defaults to v21.0
WHATSAPP_API_VERSION=v21.0
```

> If these are **not** set, LulaWorks quietly falls back to **manual** connection
> (a company pastes their phone-number-id + token). Setting them turns on the
> one-click **"Connect with Facebook"** button.

### 6. Register the delivery webhook
- In **WhatsApp → Configuration → Webhook**, set:
  - **Callback URL:** `https://www.lulaworks.com/m/wa/webhook/`
  - **Verify token:** the same string you put in `WHATSAPP_WEBHOOK_VERIFY_TOKEN`.
- **Subscribe** to the **`messages`** field.
- This lights up **delivered / read / replied** on the analytics (without it, WhatsApp
  only shows “sent”). It's authenticated by the verify token (on the handshake) and
  your app-secret signature (on every event).

### 7. Take the app Live
- Complete **Business Verification** for the Meta app.
- Switch the app from **Development** to **Live** (top toggle).
- Submit for **App Review** if Meta asks for the two WhatsApp permissions above.

That's it for the platform. You never handle any tenant's number or token.

---

## Part 2 — Per-company setup (each company owner)

In LulaWorks: **Marketing → WhatsApp**. (Needs the *company admin* /
`company.manage` permission.)

### Option A — Connect with Facebook (recommended, ~2 minutes)
1. Click **Connect with Facebook**.
2. Log in to **your own** Meta/Facebook account in the popup.
3. Select or create your **WhatsApp Business Account** and **phone number**.
4. Finish — LulaWorks stores the connection and shows **Connected**.

### Option B — Manual
Under *"Enter connection details manually"*, paste your **Phone number ID** and a
**permanent access token** from Meta → WhatsApp → API Setup, and **Save**.

### Requirements (either option)
- **A dedicated phone number** that is **not** already active on the normal
  WhatsApp or WhatsApp Business **app**. (Free one up, or use a new number.)
- **A verified Meta Business** account for your company.
- **Approved message templates** — Meta reviews every marketing template before you
  can message a new contact. Create them in Meta → WhatsApp → **Message Templates**.

### Send a campaign
1. **Marketing → Campaigns → New**, set **Channel = WhatsApp**.
2. Set **WhatsApp template name** to your approved template's exact name.
3. Choose an **audience segment**, then **Send test** / **Send WhatsApp now**.

---

## Good to know

- **Templates vs free text.** A *first* message to a contact **must** use an
  approved template. Free text only works inside a **24-hour** window after the
  contact messages you. LulaWorks sends the template when `wa_template_name` is
  set; otherwise it sends `content` as text (only useful for replies/testing).
- **Opt-in.** WhatsApp requires the contact to have opted in to hear from *your*
  business. Only message people who agreed.
- **Billing.** Meta charges per conversation, billed to the number's owner (your
  company's Meta payment method). One company's usage never affects another's.
- **Quality rating.** Meta rates each number; spammy sending lowers *your* number's
  rating only — never other companies', because numbers are per-tenant.
- **Tracking.** Each recipient is recorded as a `CampaignSend` (channel=whatsapp)
  with the Meta message id. Delivered/read/replied status requires a webhook —
  planned as a follow-up.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| No "Connect with Facebook" button | `META_APP_ID` / `WHATSAPP_CONFIG_ID` not set in ENV, or `web` not restarted. |
| Popup opens then "Connection cancelled" | The Meta app is still in **Development**, or the domain isn't in **Valid OAuth Redirect URIs**. |
| "Could not obtain an access token" | Wrong `META_APP_SECRET`, or the app lacks the two `whatsapp_business_*` permissions. |
| Send fails with a template error | The template name doesn't match an **approved** template exactly (name + language). |
| "not on WhatsApp app" errors | The number is still registered on the consumer WhatsApp/Business app — remove it there first. |
