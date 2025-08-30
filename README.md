# Letterboxd → Social Feeds

Generate short, social-ready summaries from your **Letterboxd** RSS and publish two **Atom feeds**:

- **Twitter/X** (≤ 280 chars) → `cleaned_letterboxd_twitter.xml`  
- **Threads** (≤ 500 chars) → `cleaned_letterboxd_threads.xml`

The script can also **commit and force-push** those XML feeds to this repo.

---

## Features

- Fetches your Letterboxd RSS (`LETTERBOXD_RSS_URL`)
- Keeps **review** entries only (GUID starts with `letterboxd-review-`)
- Strips HTML and images; summarizes text to fit platform budgets  
  - Uses Hugging Face `transformers` (`facebook/bart-large-cnn`) if available (CUDA / Apple Silicon MPS / CPU)  
  - Falls back to truncation if the model can’t load
- Adds compact header and footer:
  ```
  🎥 Title, Year
  ⭐️ ★★★½
  <summary…>
  🔗 https://… #FilmReview
  ```
- Writes two Atom feeds at the repo root
- Caches processed GUIDs at `~/processed_letterboxd.json`
- Git: fetch → hard-reset to `origin/main` → commit new feeds → **force-push** to `main`

> ℹ️ URL length is fixed at **23** to mimic Twitter/X `t.co` wrapping.

---

## Requirements

- Python **3.9+**
- Git (if you want auto-push)
- Python deps:
  ```bash
  pip install requests feedparser beautifulsoup4 transformers torch feedgen
  ```
  *Install the Torch build that matches your platform (CPU/CUDA/MPS).*

---

## Quick Start

```bash
# 1) Clone and enter the repo
git clone <this-repo-url>
cd <repo>

# 2) (Optional) Point to your own Letterboxd RSS
export LETTERBOXD_RSS_URL="https://letterboxd.com/<your-username>/rss/"

# 3) (Optional) Customize cache path
export CACHE_PATH="~/processed_letterboxd.json"

# 4) Run
python3 letterboxd_to_social.py
```

Outputs:
- `cleaned_letterboxd_twitter.xml`
- `cleaned_letterboxd_threads.xml`

---

## Configuration

**Environment variables**

| Name                 | Default                                    | Purpose                           |
|----------------------|--------------------------------------------|-----------------------------------|
| `LETTERBOXD_RSS_URL` | `https://letterboxd.com/julienpierre/rss/` | Your Letterboxd RSS feed          |
| `CACHE_PATH`         | `~/processed_letterboxd.json`              | Processed GUID cache (JSON list)  |

**In-script constants** (edit if needed)

| Variable     | Value                     | Notes                                  |
|--------------|---------------------------|----------------------------------------|
| Model        | `facebook/bart-large-cnn` | Summarization via `transformers`       |
| Hashtag      | `#FilmReview`             | Appended to every entry                |
| Link length  | `23`                      | Twitter/X URL wrapping assumption      |

---

## CLI

```bash
python3 letterboxd_to_social.py [--limit N] [--clear-cache]
```

- `--limit N` : process only the newest N entries (**ignores cache**, good for testing)
- `--clear-cache` : delete the cache file before running

---

## Publishing

### A) GitHub Pages (public)
Enable **Pages** for the repo. The XML files at the repo root will be served as static files.

### B) Auto-push (default)
The script executes:
```
git fetch origin main
git reset --hard origin/main
git add cleaned_letterboxd_*.xml
git commit -m "Auto-update Letterboxd feeds"
git push --force origin main
```

> ⚠️ **History rewrite**: this force-pushes to `main`. Use a dedicated repo/branch or remove `--force` if you don’t want history rewritten.

---

## GitHub Actions (automation)

Create `.github/workflows/feeds.yml`:

```yaml
name: Build & Push Letterboxd Feeds

on:
  schedule:
    - cron: "17 * * * *"   # hourly, at minute 17
  workflow_dispatch:

permissions:
  contents: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install requests feedparser beautifulsoup4 transformers torch feedgen
      - name: Configure git identity
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
      - name: Generate feeds
        env:
          LETTERBOXD_RSS_URL: ${{ secrets.LETTERBOXD_RSS_URL }}
          # Optional:
          # CACHE_PATH: ${{ vars.CACHE_PATH }}
        run: |
          python letterboxd_to_social.py
```

Set **Actions → Secrets**:
- `LETTERBOXD_RSS_URL` = your Letterboxd RSS

---

## Cron (self-hosted)

```bash
# every hour at :17
17 * * * * cd /path/to/repo && /usr/bin/python3 letterboxd_to_social.py >> /var/log/letterboxd_feeds.log 2>&1
```

Ensure `origin` is authenticated (SSH key or HTTPS token) if relying on auto-push.

---

## Troubleshooting

- **Model won’t load / OOM** → The script logs a warning and uses truncation. You can switch to CPU (install CPU-only Torch) or choose a smaller model.
- **No entries appear** → Only GUIDs beginning with `letterboxd-review-` are processed; make sure your RSS has real **reviews**, not just likes/ratings.
- **Duplicates** → Run with `--clear-cache` to reset processed GUIDs (re-emits entries).
- **Force-push risk** → Remove `--force` or push to a separate branch.

---

## Notes

- Feeds are valid **Atom** (generated via `feedgen`).
- Apple Silicon support via PyTorch **MPS** if available.
- Summaries **aim** to fit the budget; on failure, content is truncated.

---

## License

MIT (or your preferred license).
