#!/usr/bin/env python3
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
RSS_URL = os.getenv("LETTERBOXD_RSS_URL", "https://letterboxd.com/julienpierre/rss/")
CACHE_PATH = os.getenv("CACHE_PATH", os.path.expanduser("~/processed_letterboxd.json"))
LOCAL_REPO_PATH = os.path.dirname(os.path.abspath(__file__))
TWITTER_OUTPUT_PATH = os.getenv(
    "TWITTER_OUTPUT_PATH",
    os.path.join(LOCAL_REPO_PATH, "cleaned_letterboxd_twitter.xml")
)
THREADS_OUTPUT_PATH = os.getenv(
    "THREADS_OUTPUT_PATH",
    os.path.join(LOCAL_REPO_PATH, "cleaned_letterboxd_threads.xml")
)

# Limits & hashtags
TW_LIMIT = 280
LINK_LEN = 23  # Twitter wraps URLs to 23 chars
HASHTAG = " #FilmReview"
HASHTAG_LEN = len(HASHTAG)
TH_LIMIT = 500

# Setup logging
def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s"
    )

# Initialize summarization pipeline
def init_summarizer():
    try:
        if torch.cuda.is_available():
            device = 0
            logging.info("Using CUDA for summarization")
        elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            device = 0
            logging.info("Using MPS for summarization")
        else:
            device = -1
            logging.info("Using CPU for summarization")
        return pipeline(
            "summarization",
            model="facebook/bart-large-cnn",
            framework="pt",
            device=device
        )
    except Exception as e:
        logging.warning(f"Summarizer init failed ({e}), falling back to truncation")
        return None

# Cache utilities
def load_cache(path):
    if os.path.exists(path):
        with open(path) as f:
            return set(json.load(f))
    return set()

def save_cache(path, processed):
    with open(path, 'w') as f:
        json.dump(list(processed), f)

# Extract review text without images
def extract_review(html):
    soup = BeautifulSoup(html, 'html.parser')
    paras = [p.get_text().strip() for p in soup.find_all('p') if not p.find('img')]
    return ' '.join(paras)

# Build Atom feed
def build_feed(items, output_path, limit_chars, processed_ids, summarizer):
    fg = FeedGenerator()
    tag = os.path.basename(output_path).split('_')[-1].split('.')[0].capitalize()
    fg.id(RSS_URL)
    fg.title(f"Julien’s Letterboxd → {tag}")
    fg.link(href=RSS_URL, rel='alternate')
    fg.link(href=output_path, rel='self')
    fg.updated(datetime.utcnow().isoformat() + 'Z')

    new_ids = set()
    for it in items:
        guid = it.get('guid')
        if not guid or not guid.startswith('letterboxd-review-') or guid in processed_ids:
            continue

        film = it.get('filmTitle') or 'Unknown'
        year = it.get('filmYear') or ''
        rating = it.get('memberRating')
        try:
            val = float(rating)
            full = int(val)
            half = '½' if val % 1 >= 0.5 else ''
            stars = '★' * full + half
        except Exception:
            logging.warning(f"Skipping {guid}, invalid rating")
            processed_ids.add(guid)
            continue

        link = it.get('link')
        raw = extract_review(it.get('description', ''))
        if len(raw) < 20:
            logging.info(f"Skipping {guid}, review too short")
            processed_ids.add(guid)
            continue

        prefix = f"🎥 {film}, {year}\n⭐️ {stars}\n"
        suffix = f"\n🔗 {link}{HASHTAG}"
        allowed = limit_chars - len(prefix) - LINK_LEN - HASHTAG_LEN - 1

        if len(raw) <= allowed or summarizer is None:
            summary = raw
        else:
            prompt = f"Summarize in ≤{allowed} chars: {raw}"
            try:
                summary = summarizer(prompt, max_length=allowed, do_sample=False)[0]['summary_text'].strip().replace('\n',' ')
            except Exception as e:
                logging.warning(f"Summarization failed for {guid}: {e}")
                summary = raw[:allowed].rstrip() + '...'

        content = prefix + summary + suffix
        entry = fg.add_entry()
        entry.id(f"{guid}#{tag.lower()}")
        entry.title(f"{film}, {year} - {stars}")
        entry.link(href=link)
        entry.updated(it.get('updated'))
        entry.content(content, type='text')

        new_ids.add(guid)
        logging.info(f"Added {guid} to {tag}")

    fg.atom_file(output_path)
    logging.info(f"Wrote feed to {output_path}")
    return new_ids

# Git automation: force overwrite

def git_push():
    subprocess.run(["git", "-C", LOCAL_REPO_PATH, "fetch", "origin", "main"], check=True)
    subprocess.run(["git", "-C", LOCAL_REPO_PATH, "reset", "--hard", "origin/main"], check=True)
    subprocess.run(["git", "-C", LOCAL_REPO_PATH, "add",
                    os.path.basename(TWITTER_OUTPUT_PATH),
                    os.path.basename(THREADS_OUTPUT_PATH)
    ], check=True)
    subprocess.run(["git", "-C", LOCAL_REPO_PATH, "commit", "-m", "Auto-update Letterboxd feeds"], check=False)
    subprocess.run(["git", "-C", LOCAL_REPO_PATH, "push", "--force", "origin", "main"], check=True)
    logging.info("GitHub force-push successful")

# Main entry point
if __name__ == '__main__':
    setup_logging()
    parser = argparse.ArgumentParser(description="Generate Letterboxd social feeds")
    parser.add_argument('--limit', type=int, help='limit entries for testing')
    parser.add_argument('--clear-cache', action='store_true', help='clear processed cache')
    args = parser.parse_args()

    if args.clear_cache and os.path.exists(CACHE_PATH):
        os.remove(CACHE_PATH)
        logging.info(f"Cleared cache at {CACHE_PATH}")

    processed = load_cache(CACHE_PATH)
    content = requests.get(RSS_URL, headers={'Cache-Control': 'no-cache'}).content
    feed = feedparser.parse(content)
    entries = feed.entries[:args.limit] if args.limit else feed.entries
    entries = list(reversed(entries))
    logging.info(f"Fetched {len(entries)} entries from RSS")

    items = []
    for e in entries:
        items.append({
            'guid': e.get('guid'),
            'filmTitle': e.get('letterboxd_filmTitle') or e.get('letterboxd_filmtitle'),
            'filmYear': e.get('letterboxd_filmYear') or e.get('letterboxd_filmyear'),
            'memberRating': e.get('letterboxd_memberRating') or e.get('letterboxd_memberrating'),
            'link': e.get('link'),
            'description': e.get('description',''),
            'updated': e.get('updated') or e.get('published')
        })

    summarizer = init_summarizer()
    new_tw = build_feed(items, TWITTER_OUTPUT_PATH, TW_LIMIT, processed, summarizer)
    new_th = build_feed(items, THREADS_OUTPUT_PATH, TH_LIMIT, processed, summarizer)
    processed.update(new_tw | new_th)

    save_cache(CACHE_PATH, processed)
    git_push()
