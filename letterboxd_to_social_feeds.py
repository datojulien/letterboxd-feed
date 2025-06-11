#!/usr/bin/env python3
"""
Letterboxd to Social Feeds

Fetches your Letterboxd RSS, summarizes new review entries to fit Twitter (≤280 chars) and Threads (≤500 chars),
and publishes two Atom feeds. Automatically commits and force-pushes to GitHub.
"""

import os
import json
import argparse
import logging
import subprocess
from datetime import datetime

import requests
import feedparser
from bs4 import BeautifulSoup
from transformers import pipeline
import torch
from feedgen.feed import FeedGenerator

# --- Configuration ---
RSS_URL = os.getenv(
    "LETTERBOXD_RSS_URL",
    "https://letterboxd.com/julienpierre/rss/"
)
CACHE_PATH = os.path.expanduser(os.getenv("CACHE_PATH", "~/processed_letterboxd.json"))
REPO_PATH = os.path.dirname(os.path.abspath(__file__))
TW_FEED = os.path.join(REPO_PATH, "cleaned_letterboxd_twitter.xml")
TH_FEED = os.path.join(REPO_PATH, "cleaned_letterboxd_threads.xml")

# Character budgets
TW_LIMIT = 280
TH_LIMIT = 500
LINK_LEN = 23       # Twitter wraps URLs to 23 chars
HASHTAG = " #FilmReview"
HASHTAG_LEN = len(HASHTAG)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)


def init_summarizer():
    """Initialize a Huggingface summarization pipeline."""
    try:
        # Device selection
        if torch.cuda.is_available():
            device = 0
            logging.info("Using CUDA for summarization")
        elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            device = 0
            logging.info("Using MPS for summarization")
        else:
            device = -1
            logging.info("Using CPU for summarization")
        return pipeline("summarization", model="facebook/bart-large-cnn", framework="pt", device=device)
    except Exception as e:
        logging.warning(f"Could not load summarizer ({e}), falling back to truncation")
        return None


def load_cache(path):
    """Load processed GUIDs from disk."""
    if os.path.exists(path):
        with open(path) as f:
            return set(json.load(f))
    return set()


def save_cache(path, processed):
    """Persist processed GUIDs to disk."""
    with open(path, 'w') as f:
        json.dump(list(processed), f)


def fetch_entries(limit=None):
    """Fetch and parse the Letterboxd RSS, optionally limiting results. Newest first."""
    data = requests.get(RSS_URL, headers={'Cache-Control':'no-cache'}).content
    feed = feedparser.parse(data)
    entries = feed.entries
    if limit is not None:
        entries = entries[:limit]
    # reverse Chrono so newest at start
    return list(reversed(entries))


def extract_text(html):
    """Strip HTML, remove images, join paragraphs."""
    soup = BeautifulSoup(html or "", 'html.parser')
    paras = [p.get_text().strip() for p in soup.find_all('p') if not p.find('img')]
    return ' '.join(paras)


def build_feed(entries, out_path, char_limit, processed, summarizer):
    """Construct an Atom feed of summarized reviews."""
    fg = FeedGenerator()
    feed_label = os.path.basename(out_path).split('_')[-1].split('.')[0].capitalize()
    fg.id(RSS_URL)
    fg.title(f"Julien’s Letterboxd → {feed_label}")
    fg.link(href=RSS_URL, rel='alternate')
    fg.link(href=out_path, rel='self')
    fg.updated(datetime.utcnow().isoformat() + 'Z')

    new_ids = set()
    for e in entries:
        guid = e.get('guid') or e.get('id')
        if not guid.startswith('letterboxd-review-') or guid in processed:
            continue

        # Parse meta from title: "Movie Title, YYYY - ★★★½"
        full_title = e.get('title', '')
        try:
            movie_part, star_part = full_title.rsplit(' - ', 1)
            title, year = movie_part.rsplit(', ', 1)
            stars = star_part
        except ValueError:
            title, year, stars = full_title, '', ''

        raw = extract_text(e.get('description'))
        if len(raw) < 20:
            processed.add(guid)
            continue

        prefix = f"🎥 {title}, {year}\n⭐️ {stars}\n"
        suffix = f"\n🔗 {e.get('link')}" + HASHTAG
        allowed = char_limit - len(prefix) - LINK_LEN - HASHTAG_LEN - 1

        if summarizer and len(raw) > allowed:
            prompt = f"Summarize in ≤{allowed} chars: {raw}"
            try:
                summary = summarizer(prompt, max_length=allowed, do_sample=False)[0]['summary_text'].replace('\n',' ')
            except Exception:
                summary = raw[:allowed].rstrip() + '...'
        else:
            summary = raw

        content = prefix + summary + suffix

        fe = fg.add_entry()
        fe.id(f"{guid}#{feed_label.lower()}")
        fe.title(full_title)
        fe.link(href=e.get('link'))
        fe.updated(e.get('updated') or e.get('published'))
        fe.content(content, type='text')

        new_ids.add(guid)
        logging.info(f"Added {guid} to {feed_label}")

    fg.atom_file(out_path)
    logging.info(f"Wrote feed: {out_path}")
    return new_ids


def git_force_push():
    """Force overwrite remote branch with local state."""
    subprocess.run(["git","-C",REPO_PATH,"fetch","origin","main"], check=True)
    subprocess.run(["git","-C",REPO_PATH,"reset","--hard","origin/main"], check=True)
    subprocess.run(["git","-C",REPO_PATH,"add",
                    os.path.basename(TW_FEED), os.path.basename(TH_FEED)
    ], check=True)
    subprocess.run(["git","-C",REPO_PATH,"commit","-m","Auto-update Letterboxd feeds"], check=False)
    subprocess.run(["git","-C",REPO_PATH,"push","--force","origin","main"], check=True)
    logging.info("Force-pushed to GitHub.")


def main():
    parser = argparse.ArgumentParser(description="Letterboxd → social feeds generator")
    parser.add_argument('--limit', type=int, help="Only process first N entries")
    parser.add_argument('--clear-cache', action='store_true', help="Reset processed history")
    args = parser.parse_args()

    if args.clear_cache and os.path.exists(CACHE_PATH):
        os.remove(CACHE_PATH)
        logging.info("Cache cleared.")

    processed = load_cache(CACHE_PATH)
    if args.limit:
        logging.info("Test mode: ignoring cache.")
        processed.clear()

    entries = fetch_entries(args.limit)
    summarizer = init_summarizer()

    new_tw = build_feed(entries, TW_FEED, TW_LIMIT, processed, summarizer)
    new_th = build_feed(entries, TH_FEED, TH_LIMIT, processed, summarizer)
    processed.update(new_tw | new_th)

    save_cache(CACHE_PATH, processed)
    git_force_push()

if __name__ == '__main__':
    main()
