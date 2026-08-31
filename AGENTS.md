# Airfare Monitor project instructions

- This project is a personal, read-only airfare monitoring tool.
- Do not place SMTP passwords, cookies, browser tokens, or personal email addresses in tracked files.
- Use an isolated Chromium user-data directory under `data/browser-profile/`; do not reuse the user's daily Chrome profile.
- Treat airport IATA codes and airline carrier codes as different fields.
- Price alerts must use the parsed CNY total price, not the base fare alone.
- A collection run is complete only after the observed search response reports `result.ctrlInfo.completed == true`.
- Do not implement booking, payment, CAPTCHA bypass, device-challenge bypass, or automated account login.
- Do not send email or make live website requests in tests unless the user explicitly authorizes an integration run.
- Preserve raw secrets outside the repository and reference them only through environment-variable names.

