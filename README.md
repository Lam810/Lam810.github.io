# Zeteng Lin personal homepage

Source for [https://lam810.github.io/](https://lam810.github.io/).

This is a lightweight static academic homepage hosted with GitHub Pages.

## Recent traffic

The homepage displays aggregate page views and visits for the last seven complete
UTC days. Visits are Cloudflare's visit metric, not uniquely identified people.
The dashboard never exports IP addresses, individual events, referrer URLs, or
account and site identifiers. No API token is sent to the browser.

Configure these repository Actions secrets:

- `CLOUDFLARE_API_TOKEN`: an API token with Account / Account Analytics / Read,
  restricted to the relevant account.
- `CLOUDFLARE_ACCOUNT_ID`: the account's 32-character ID.
- `CLOUDFLARE_SITE_TAG`: the Web Analytics property's 32-character Site Tag, from
  its dashboard URL. This is not necessarily the public JavaScript beacon token.

Use GitHub Actions as the repository's Pages publishing source. The Pages
workflow refreshes aggregate statistics every six hours and on manual dispatch.
It stages only the website files, not tests or export scripts.
Missing credentials show a connection-pending state rather than fabricated data.
An API failure stops the deployment so the previous published snapshot remains
available; snapshots older than 36 hours are labeled as delayed.

After setting secrets, run **Actions > Publish homepage and aggregate analytics >
Run workflow**. A successful live export is required to validate permissions and
the RUM query against this account. Local tests use synthetic responses only.

References: [Cloudflare analytics authentication](https://developers.cloudflare.com/analytics/graphql-api/getting-started/authentication/api-token-auth/)
and [GitHub Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
