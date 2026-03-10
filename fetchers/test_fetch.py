import feedparser
from datetime import datetime

feed_url = "https://feeds.feedburner.com/oreilly/radar"  # test feed
feed = feedparser.parse(feed_url)

print(f"Feed: {feed.feed.get('title', 'Unknown')}")
print(f"Entries: {len(feed.entries)}")
for entry in feed.entries[:3]:
    print(f"\n- {entry.get('title', 'No title')}")
    print(f"  {entry.get('link', '')}")
    print(f"  {entry.get('published', '')}")
