# Setup — GitHub Actions + GitHub Pages

One-time setup, ~10 minutes. After this, the dashboard auto-refreshes daily.

## 1. Create a GitHub repo

- Go to https://github.com/new
- Name it (e.g. `social-pulse`). **Private** if you want it hidden (requires GitHub Pro for Pages, $4/mo). Public is free.
- Don't initialize with anything — we'll push from this folder.

## 2. Push this folder

From the `social-listening/` directory:

```sh
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

## 3. Add your Apify token as a repo secret

- Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
- Name: `APIFY_TOKEN`
- Value: (paste your Apify token from `.env`)
- (Optional) Add `ANTHROPIC_API_KEY` the same way to enable the AI daily brief.

## 4. Enable GitHub Pages

- Repo → **Settings** → **Pages**
- Source: **Deploy from a branch**
- Branch: `main` / `(root)` → **Save**
- Wait ~1 min. Your dashboard will be at `https://YOUR_USERNAME.github.io/YOUR_REPO/dashboard.html`

## 5. Update the "Trigger refresh" link

Edit `dashboard.html` — find the line:

```js
const REPO = "YOUR_GITHUB_USERNAME/YOUR_REPO_NAME";
```

Replace with your actual `owner/repo`, then commit and push.

## 6. Run the first scrape

- Repo → **Actions** tab → **Daily X Scrape** → **Run workflow** → **Run workflow**
- Wait ~90 seconds. It'll commit `data.json` to the repo.
- Reload your Pages dashboard. Tweets appear.

## Done

The scrape runs **only when you trigger it** (click the "Trigger refresh" button on the dashboard, or "Run workflow" on the GitHub Actions page). No automatic schedule.

To change keywords, edit `keywords.json` and push. The next run picks them up.

If you later want a daily auto-refresh, add this back to `.github/workflows/daily-scrape.yml` above the `workflow_dispatch:` line:
```yaml
  schedule:
    - cron: '0 11 * * *'  # 11:00 UTC = 7am EDT / 6am EST
```

## Cost

- GitHub Actions: free for public repos; well under quota for private.
- Apify: ~$0.10–0.15 per scrape — **you only pay when you click refresh**.
- Anthropic (optional summary): ~$0.02 per scrape if enabled.
