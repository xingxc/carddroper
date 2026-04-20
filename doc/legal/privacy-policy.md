# Privacy Policy — DRAFT

> **This is a non-lawyer starting draft.** It is modeled on common SaaS privacy-policy patterns but is not legal advice and has not been reviewed by counsel. Do not publish or link to this document from the live app until a lawyer has reviewed and revised it. Privacy law (GDPR, UK GDPR, CCPA/CPRA, PIPEDA, LGPD, etc.) is jurisdiction-specific and carries real penalties for incorrect disclosures.

**Effective Date:** _[TBD]_
**Last Updated:** _[TBD]_

carddroper ("**carddroper**", "**we**", "**us**", or "**our**") respects your privacy. This Privacy Policy explains what information we collect, how we use it, and the choices you have.

## 1. Information We Collect

### 1.1 Information you provide

- **Account information:** email address, password (stored as a salted hash), and optional profile information such as your name.
- **Billing information:** when you purchase credits or subscribe, our payment processor (Stripe) collects your payment details. carddroper itself does not store full card numbers or security codes. We receive a Stripe Customer ID and the last four digits of your card for display.
- **Content you create:** communications you compose, recipient email addresses, and any media or text you attach. See our Terms of Service for ownership and license terms.
- **Support communications:** if you contact us, we retain the message and any information you include.

### 1.2 Information collected automatically

- **Usage data:** pages viewed, features used, timestamps, device type, browser type, and referral source.
- **Log data:** IP address, HTTP headers, and error traces. Used for security, debugging, and rate limiting.
- **Cookies:** we use a small number of strictly necessary cookies to keep you signed in (`access_token`, `refresh_token`). We do not use tracking cookies for advertising.

### 1.3 Information from third parties

- **Stripe** sends us events about your payment methods and subscriptions (e.g. payment succeeded, subscription updated).
- **SendGrid** may send us delivery/bounce events for transactional emails we send on your behalf (e.g. verification, password reset).

## 2. How We Use Your Information

We use the information we collect to:

- operate, maintain, and improve the Service;
- authenticate you and secure your account;
- process payments and manage subscriptions;
- deliver communications you send through the Service to the recipients you specify;
- send you transactional emails (e.g. email verification, password reset, payment receipts, subscription notices);
- detect, investigate, and prevent fraud, abuse, and violations of our Terms;
- comply with legal obligations.

We do **not** sell your personal information, and we do not use your content to train machine-learning models.

## 3. How We Share Your Information

We share information only as described below:

- **Service providers.** Stripe (payments), SendGrid (email delivery), Google Cloud (hosting and database). These providers process data on our behalf under contracts that require appropriate safeguards.
- **Legal.** We may disclose information when required by law, subpoena, or court order, or when necessary to protect our rights, users, or the public.
- **Business transfers.** If carddroper is involved in a merger, acquisition, or asset sale, your information may be transferred to the successor entity, subject to this Privacy Policy.

We do not share your content with advertisers. We do not participate in data broker marketplaces.

## 4. Data Retention

- **Account data:** kept while your account is active. When you delete your account, we delete or anonymize your personal information within 30 days, except where we must retain it to comply with legal obligations, resolve disputes, or enforce our agreements.
- **Content you created:** deleted along with your account, subject to the same retention exceptions.
- **Billing records:** retained for the period required by applicable tax and accounting law (typically 7 years in the US).
- **Logs:** retained for up to 90 days.

## 5. Security

We use industry-standard measures to protect your information:

- passwords are hashed with bcrypt; we never store plaintext passwords;
- all data in transit is encrypted via TLS;
- access tokens are short-lived (15 minutes);
- refresh tokens are stored as SHA-256 hashes (raw tokens never leave your device);
- secrets (API keys, database credentials) are stored in Google Secret Manager;
- database backups are encrypted at rest.

No system is perfectly secure. If we learn of a data breach affecting your personal information, we will notify you as required by law.

## 6. Your Choices

- **Access and correction.** You can view and update your profile information from your account settings.
- **Deletion.** You can delete your account at any time. See §4 for retention exceptions.
- **Unsubscribe.** Transactional emails (verification, security, billing) cannot be unsubscribed from while your account is active because they are necessary for the Service. We do not send marketing email without your consent.
- **Cookies.** You can block or delete cookies via your browser; doing so may require you to sign in again.

## 7. Regional Rights

### 7.1 European Economic Area, United Kingdom, and Switzerland (GDPR / UK GDPR)

If you are located in the EEA, UK, or Switzerland, you have the right to:

- access the personal data we hold about you;
- request correction or deletion of your personal data;
- object to or restrict certain processing;
- receive your personal data in a portable format;
- lodge a complaint with your local supervisory authority.

Our legal bases for processing are: (i) **contract** — to provide the Service you requested; (ii) **legitimate interests** — to secure and improve the Service; (iii) **consent** — where required, and you may withdraw consent at any time; (iv) **legal obligation** — where required by law.

### 7.2 California (CCPA / CPRA)

If you are a California resident, you have the right to know, delete, correct, and limit the use of sensitive personal information, and not to be discriminated against for exercising your rights. We do not sell or share personal information as those terms are defined in California law.

### 7.3 Other regions

Applicable local privacy laws may give you additional rights. Contact us to exercise them.

## 8. International Transfers

carddroper is operated from the United States. If you access the Service from outside the US, your information will be transferred to, processed in, and stored in the US. Where required, we use appropriate safeguards (e.g. Standard Contractual Clauses) for cross-border transfers.

## 9. Children

The Service is not directed to children under 13 (or the minimum age of digital consent in your country). We do not knowingly collect personal information from children. If you believe a child has provided us with personal information, contact us and we will delete it.

## 10. Changes

We may update this Privacy Policy from time to time. Material changes will be notified by email or in-app notice. The "Last Updated" date at the top indicates the most recent revision.

## 11. Contact

Privacy questions or requests: _[privacy@carddroper.com — placeholder]_
Data Protection Officer (if and when appointed): _[TBD]_

---

### Drafting notes (remove before publication)

Items that must be finalized with a lawyer:
- Specific GDPR / UK GDPR disclosures (legal bases per processing purpose, retention per category, transfer safeguards).
- CCPA / CPRA notice-at-collection and the "Do Not Sell or Share" link (even if we don't "sell" — CPRA's definition is broad and includes some sharing with service providers).
- Children's-privacy compliance (COPPA / UK Children's Code) if the product might attract users under the local age of consent.
- Specific subprocessor list with links to their privacy policies.
- Whether we publish a Data Processing Addendum for business users.
- CASL / CAN-SPAM wording if we send any marketing email.
- Cookie-consent mechanics for EEA/UK visitors (the current strictly-necessary-only posture is simpler but must be reconfirmed if we add analytics).
