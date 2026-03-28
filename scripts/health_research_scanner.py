#!/usr/bin/env python3
"""Health research scanner: rules-based paper discovery + LLM summarization.

Two modes:
  --scan     Daily. Pull PubMed RSS, keyword-filter, summarize with Haiku.
  --synthesize  Weekly. Read week's papers, compare to framework, Sonnet memo.

Cost model:
  - Discovery + filtering: $0 (RSS + keyword matching)
  - Summarization: Haiku (~$0.50/mo at 2-3 papers/day)
  - Synthesis: Sonnet (~$1/mo at 1x/week)
  - Opus: NEVER used in this pipeline
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError


# --- Configuration ---

DATA_DIR = Path(__file__).parent.parent / "data" / "research"
FRAMEWORK_PATH = Path(__file__).parent.parent.parent / "hub" / "research" / "2026-03-27-health-metrics-timescale-framework.md"

# PubMed RSS feeds for saved searches
# Each is a PubMed search query converted to RSS via the E-utilities API
PUBMED_SEARCHES = [
    {
        "name": "hrv_wearable",
        "query": "heart rate variability AND (wearable OR consumer device) AND (validation OR accuracy)",
        "max_results": 10,
    },
    {
        "name": "sleep_regularity",
        "query": "sleep regularity AND (mortality OR health outcomes OR circadian)",
        "max_results": 10,
    },
    {
        "name": "resting_heart_rate",
        "query": "resting heart rate AND (mortality OR cardiovascular risk) AND (wearable OR monitoring)",
        "max_results": 5,
    },
    {
        "name": "ai_health_coaching",
        "query": "(AI OR LLM OR chatbot) AND (health coaching OR behavior change) AND (RCT OR trial OR evaluation)",
        "max_results": 10,
    },
    {
        "name": "body_composition_deficit",
        "query": "(caloric deficit OR energy restriction) AND (muscle preservation OR body composition) AND (protein OR resistance training)",
        "max_results": 5,
    },
    {
        "name": "biomarker_screening",
        "query": "(ApoB OR Lp(a) OR fasting insulin) AND (screening OR prevention OR risk)",
        "max_results": 5,
    },
    {
        "name": "training_load_injury",
        "query": "(acute chronic workload ratio OR training load) AND (injury risk OR monitoring)",
        "max_results": 5,
    },
    {
        "name": "vo2max_longevity",
        "query": "(VO2 max OR cardiorespiratory fitness) AND (mortality OR longevity OR aging)",
        "max_results": 5,
    },
]

# Keywords for relevance scoring (rules-based)
# Higher weight = more relevant to our framework
RELEVANCE_KEYWORDS = {
    # Tier 1: directly changes our thresholds or recommendations
    "tier1": {
        "words": [
            "heart rate variability", "HRV", "RMSSD", "resting heart rate",
            "sleep regularity", "sleep duration", "circadian",
            "wearable accuracy", "wearable validation",
            "acute chronic workload", "ACWR", "training load",
            "ApoB", "Lp(a)", "fasting insulin",
            "body composition", "muscle preservation", "protein intake",
            "all-cause mortality", "cardiovascular risk",
        ],
        "weight": 3,
    },
    # Tier 2: relevant to our coaching approach
    "tier2": {
        "words": [
            "behavior change", "habit formation", "motivational interviewing",
            "health coaching", "digital health", "mHealth",
            "progressive overload", "periodization",
            "caloric deficit", "energy expenditure", "TDEE",
            "sleep hygiene", "cognitive behavioral",
        ],
        "weight": 2,
    },
    # Tier 3: background context
    "tier3": {
        "words": [
            "wearable", "Garmin", "Apple Watch", "Oura", "WHOOP",
            "biomarker", "screening", "prevention",
            "exercise", "physical activity", "steps",
            "nutrition", "dietary", "macronutrient",
        ],
        "weight": 1,
    },
}

# Minimum relevance score to pass the filter
RELEVANCE_THRESHOLD = 4


def fetch_pubmed_rss(query: str, max_results: int = 10) -> list[dict]:
    """Fetch papers from PubMed via E-utilities search + fetch."""
    import urllib.parse

    # Step 1: Search for IDs
    search_url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
        f"db=pubmed&retmax={max_results}&sort=date&"
        f"term={urllib.parse.quote(query)}&retmode=json"
        "&datetype=pdat&reldate=7"  # last 7 days
    )

    try:
        req = Request(search_url, headers={"User-Agent": "KisoResearchScanner/1.0"})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (URLError, json.JSONDecodeError) as e:
        print(f"  Search failed: {e}", file=sys.stderr)
        return []

    ids = data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    # Step 2: Fetch summaries
    ids_str = ",".join(ids)
    fetch_url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
        f"db=pubmed&id={ids_str}&retmode=json"
    )

    try:
        req = Request(fetch_url, headers={"User-Agent": "KisoResearchScanner/1.0"})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (URLError, json.JSONDecodeError) as e:
        print(f"  Fetch failed: {e}", file=sys.stderr)
        return []

    results = []
    for pmid in ids:
        article = data.get("result", {}).get(pmid, {})
        if not article or "error" in article:
            continue
        results.append({
            "pmid": pmid,
            "title": article.get("title", ""),
            "authors": ", ".join(
                a.get("name", "") for a in article.get("authors", [])[:3]
            ),
            "journal": article.get("fulljournalname", article.get("source", "")),
            "pubdate": article.get("pubdate", ""),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })

    return results


def fetch_abstract(pmid: str) -> str:
    """Fetch the abstract for a PubMed article."""
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
        f"db=pubmed&id={pmid}&rettype=abstract&retmode=text"
    )
    try:
        req = Request(url, headers={"User-Agent": "KisoResearchScanner/1.0"})
        with urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except URLError:
        return ""


def score_relevance(title: str, abstract: str) -> int:
    """Rules-based relevance scoring. No LLM needed."""
    text = (title + " " + abstract).lower()
    score = 0

    for tier, config in RELEVANCE_KEYWORDS.items():
        for keyword in config["words"]:
            if keyword.lower() in text:
                score += config["weight"]

    return score


def summarize_paper_haiku(title: str, abstract: str, api_key: str) -> str:
    """Summarize a paper using Haiku. Cheap, fast."""
    import urllib.request

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 300,
        "messages": [{
            "role": "user",
            "content": (
                "Summarize this research paper in 2-3 sentences. "
                "Focus on: what they found, sample size, and practical implication "
                "for health coaching or wearable-based monitoring.\n\n"
                f"Title: {title}\n\nAbstract: {abstract}"
            ),
        }],
    }).encode()

    req = Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return data["content"][0]["text"]
    except Exception as e:
        return f"(summarization failed: {e})"


def scan(api_key: str, dry_run: bool = False):
    """Daily scan: fetch papers, filter by relevance, summarize top hits."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    output_path = DATA_DIR / f"scan-{today}.json"

    print(f"Health research scan for {today}")
    print(f"Searching {len(PUBMED_SEARCHES)} PubMed queries...")

    all_papers = []
    seen_pmids = set()

    for search in PUBMED_SEARCHES:
        print(f"\n  {search['name']}:")
        papers = fetch_pubmed_rss(search["query"], search["max_results"])
        print(f"    Found {len(papers)} papers")

        for paper in papers:
            if paper["pmid"] in seen_pmids:
                continue
            seen_pmids.add(paper["pmid"])

            # Fetch abstract for relevance scoring
            abstract = fetch_abstract(paper["pmid"])
            import time
            time.sleep(0.4)  # PubMed rate limit: 3 req/sec without API key

            score = score_relevance(paper["title"], abstract)
            paper["relevance_score"] = score
            paper["abstract"] = abstract[:1000]  # truncate for storage

            if score >= RELEVANCE_THRESHOLD:
                print(f"    [RELEVANT score={score}] {paper['title'][:70]}...")
                all_papers.append(paper)
            else:
                print(f"    [skip score={score}] {paper['title'][:60]}...")

    print(f"\n{len(all_papers)} papers passed relevance filter (threshold={RELEVANCE_THRESHOLD})")

    if not all_papers:
        print("No relevant papers today.")
        result = {"date": today, "papers": [], "count": 0}
        output_path.write_text(json.dumps(result, indent=2))
        return result

    # Summarize with Haiku (only relevant papers)
    if not dry_run and api_key:
        print(f"\nSummarizing {len(all_papers)} papers with Haiku...")
        for paper in all_papers:
            summary = summarize_paper_haiku(paper["title"], paper["abstract"], api_key)
            paper["summary"] = summary
            print(f"  Summarized: {paper['title'][:50]}...")
            import time
            time.sleep(0.5)
    elif dry_run:
        print("\n[DRY RUN] Skipping Haiku summarization")

    # Sort by relevance score descending
    all_papers.sort(key=lambda p: p["relevance_score"], reverse=True)

    result = {
        "date": today,
        "papers": all_papers,
        "count": len(all_papers),
        "searches": len(PUBMED_SEARCHES),
    }

    output_path.write_text(json.dumps(result, indent=2))
    print(f"\nSaved to {output_path}")
    return result


def synthesize(api_key: str, days: int = 7):
    """Weekly synthesis: read the week's papers, compare to framework, Sonnet memo."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    # Collect papers from the past week's scans
    all_papers = []
    for i in range(days):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        scan_path = DATA_DIR / f"scan-{d}.json"
        if scan_path.exists():
            data = json.loads(scan_path.read_text())
            all_papers.extend(data.get("papers", []))

    if not all_papers:
        print("No papers found from the past week. Nothing to synthesize.")
        return

    print(f"Synthesizing {len(all_papers)} papers from the past {days} days...")

    # Load framework for comparison (if available)
    framework_excerpt = ""
    if FRAMEWORK_PATH.exists():
        content = FRAMEWORK_PATH.read_text()
        # Extract just the metric inventory section (the thresholds)
        start = content.find("## Complete Metric Inventory")
        end = content.find("## Coaching Decision Matrix")
        if start > 0 and end > start:
            framework_excerpt = content[start:end][:3000]

    # Build paper summaries for Sonnet
    paper_text = ""
    for i, paper in enumerate(all_papers[:20]):  # cap at 20
        summary = paper.get("summary", paper.get("abstract", "")[:200])
        paper_text += (
            f"\n{i+1}. [{paper.get('journal', '')}] {paper['title']}\n"
            f"   Score: {paper['relevance_score']} | {paper.get('pubdate', '')}\n"
            f"   {summary}\n"
        )

    # Sonnet synthesis
    import urllib.request

    prompt = (
        "You are a health data architect reviewing this week's research papers "
        "for a health coaching platform (Kiso). The platform tracks wearable metrics "
        "(HRV, RHR, sleep, steps, VO2 max), body composition, labs, and habits.\n\n"
        "Here are the current clinical thresholds and framework:\n"
        f"{framework_excerpt}\n\n"
        "Here are this week's relevant papers:\n"
        f"{paper_text}\n\n"
        "Write a short weekly memo (under 500 words) covering:\n"
        "1. Papers that VALIDATE our current approach (reinforcing)\n"
        "2. Papers that CHALLENGE our current thresholds or recommendations (needs review)\n"
        "3. New metrics or methods worth tracking (if any)\n"
        "4. Recommendation: any threshold changes needed? (yes/no with specifics)\n\n"
        "Be direct. No filler. If nothing changes, say so in one line."
    )

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 800,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        memo = data["content"][0]["text"]
    except Exception as e:
        memo = f"(synthesis failed: {e})"

    # Save memo
    memo_path = DATA_DIR / f"synthesis-{today}.md"
    memo_content = (
        f"---\ndate: {today}\npapers_reviewed: {len(all_papers)}\n---\n\n"
        f"# Weekly Health Research Synthesis\n\n{memo}\n\n"
        f"## Papers Reviewed\n\n"
    )
    for paper in all_papers:
        memo_content += f"- [{paper['title']}]({paper.get('url', '')})\n"

    memo_path.write_text(memo_content)
    print(f"\nMemo saved to {memo_path}")
    print(f"\n{memo}")
    return memo


def main():
    parser = argparse.ArgumentParser(description="Health research scanner")
    parser.add_argument("--scan", action="store_true", help="Daily scan (Haiku)")
    parser.add_argument("--synthesize", action="store_true", help="Weekly synthesis (Sonnet)")
    parser.add_argument("--dry-run", action="store_true", help="Skip LLM calls")
    parser.add_argument("--days", type=int, default=7, help="Days to look back for synthesis")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key and not args.dry_run:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    if args.scan:
        scan(api_key, dry_run=args.dry_run)
    elif args.synthesize:
        synthesize(api_key, days=args.days)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
