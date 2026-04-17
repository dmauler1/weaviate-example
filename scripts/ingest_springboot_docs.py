"""
Spring Boot Antora Built Site → Weaviate Ingestion Script

Ingests into three Weaviate classes:
  - SpringBootDoc    prose documentation (Java/Kotlin code blocks removed)
  - SpringBootJava   Java code samples with surrounding context
  - SpringBootKotlin Kotlin code samples with surrounding context

Requirements:
    pip install weaviate-client beautifulsoup4 lxml

Usage:
    python ingest_springboot_docs.py \
        --site-path ./documentation/spring-boot-docs/build/site \
        --weaviate-url http://localhost:8080

Note:
    Vectorization is handled by Weaviate's text2vec-transformers module automatically
    on insert — no external embedding model or API key is required.

Schema setup — run these curl commands before ingesting:

  # SpringBootDoc (already created)
  # SpringBootJava:
  curl -X POST http://localhost:8080/v1/schema -H "Content-Type: application/json" -d '{
    "class": "SpringBootJava",
    "description": "Java code samples from Spring Boot documentation",
    "vectorizer": "text2vec-transformers",
    "moduleConfig": { "text2vec-transformers": { "vectorizeClassName": false } },
    "properties": [
      { "name": "content",  "dataType": ["text"],    "description": "The Java code sample" },
      { "name": "context",  "dataType": ["text"],    "description": "Surrounding prose describing what the code does", "moduleConfig": { "text2vec-transformers": { "skip": true } } },
      { "name": "filePath", "dataType": ["text"],    "description": "Source HTML file path",   "moduleConfig": { "text2vec-transformers": { "skip": true } } },
      { "name": "version",  "dataType": ["text"],    "description": "Spring Boot version",     "moduleConfig": { "text2vec-transformers": { "skip": true } } },
      { "name": "module",   "dataType": ["text"],    "description": "Doc module e.g. reference, how-to", "moduleConfig": { "text2vec-transformers": { "skip": true } } }
    ]
  }'

  # SpringBootKotlin:
  curl -X POST http://localhost:8080/v1/schema -H "Content-Type: application/json" -d '{
    "class": "SpringBootKotlin",
    "description": "Kotlin code samples from Spring Boot documentation",
    "vectorizer": "text2vec-transformers",
    "moduleConfig": { "text2vec-transformers": { "vectorizeClassName": false } },
    "properties": [
      { "name": "content",  "dataType": ["text"],    "description": "The Kotlin code sample" },
      { "name": "context",  "dataType": ["text"],    "description": "Surrounding prose describing what the code does", "moduleConfig": { "text2vec-transformers": { "skip": true } } },
      { "name": "filePath", "dataType": ["text"],    "description": "Source HTML file path",   "moduleConfig": { "text2vec-transformers": { "skip": true } } },
      { "name": "version",  "dataType": ["text"],    "description": "Spring Boot version",     "moduleConfig": { "text2vec-transformers": { "skip": true } } },
      { "name": "module",   "dataType": ["text"],    "description": "Doc module e.g. reference, how-to", "moduleConfig": { "text2vec-transformers": { "skip": true } } }
    ]
  }'
"""

import argparse
import re
from pathlib import Path

import weaviate
from bs4 import BeautifulSoup, Tag


# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

DOC_CLASS    = "SpringBootDoc"
JAVA_CLASS   = "SpringBootJava"
KOTLIN_CLASS = "SpringBootKotlin"

# Languages to extract into their own classes
CODE_CLASSES = {
    "language-java":   JAVA_CLASS,
    "language-kotlin": KOTLIN_CLASS,
}

# Languages to keep inline in prose (config, shell commands, etc.)
INLINE_LANGUAGES = {"language-xml", "language-shell", "language-gradle", "language-none"}

# Top-level site files to skip
EXCLUDED_FILENAMES = {
    "index.html", "search.html", "redirect.html",
    "community.html", "spring-projects.html",
}

# Directories to skip
EXCLUDED_DIRS = {"_", "api"}

# Chunking
CHUNK_SIZE    = 512
CHUNK_OVERLAP = 64


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def collect_html_files(site_path: Path) -> list[Path]:
    files = []
    for html_file in site_path.rglob("*.html"):
        if html_file.name in EXCLUDED_FILENAMES:
            continue
        if any(part in EXCLUDED_DIRS for part in html_file.relative_to(site_path).parts):
            continue
        files.append(html_file)
    print(f"✅ Found {len(files)} content HTML files to index")
    return files


def get_module_from_path(file_path: Path, site_path: Path) -> str:
    try:
        relative = file_path.relative_to(site_path)
        return relative.parts[0] if relative.parts else "unknown"
    except ValueError:
        return "unknown"


def extract_version(site_path: Path) -> str:
    index = site_path / "index.html"
    if not index.exists():
        return "unknown"
    soup = BeautifulSoup(index.read_text(encoding="utf-8"), "lxml")
    version_el = soup.select_one(".version, .component-version, [data-version]")
    if version_el:
        return version_el.get_text(strip=True)
    title = soup.title.get_text() if soup.title else ""
    match = re.search(r"\d+\.\d+[\.\d]*", title)
    return match.group(0) if match else "unknown"


def get_context_for_block(block: Tag, max_words: int = 60) -> str:
    """
    Walk backwards from a code block to find the nearest preceding
    paragraph or heading — this becomes the 'context' field so the
    LLM knows what the code sample is demonstrating.
    """
    context_parts = []
    for sibling in block.previous_siblings:
        if isinstance(sibling, Tag):
            if sibling.name in ("p", "h1", "h2", "h3", "h4", "h5", "div"):
                text = sibling.get_text(separator=" ", strip=True)
                if text:
                    context_parts.insert(0, text)
                    # Stop once we have enough context words
                    if sum(len(p.split()) for p in context_parts) >= max_words:
                        break
    return " ".join(context_parts)[:500]  # cap at 500 chars


def parse_html_file(html_file: Path, site_path: Path, version: str) -> tuple[
    list[dict],  # prose chunks
    list[dict],  # java chunks
    list[dict],  # kotlin chunks
]:
    """
    Parse a single HTML file and return three lists of chunk dicts:
    prose, java code samples, and kotlin code samples.
    """
    soup = BeautifulSoup(html_file.read_text(encoding="utf-8"), "lxml")
    module   = get_module_from_path(html_file, site_path)
    filepath = str(html_file)

    content_el = soup.select_one("article.doc") or soup.select_one("main")
    if not content_el:
        return [], [], []

    java_chunks   = []
    kotlin_chunks = []

    # ── Extract Java and Kotlin code blocks ──────────────────────────
    for listing in content_el.select("div.listingblock"):
        code_el = listing.select_one("code[class]")
        if not code_el:
            continue

        # Determine language from CSS class
        lang_class = next(
            (c for c in code_el.get("class", []) if c in CODE_CLASSES),
            None
        )
        if not lang_class:
            continue

        code_text = code_el.get_text(strip=True)
        if not code_text:
            continue

        context = get_context_for_block(listing)
        target_class = CODE_CLASSES[lang_class]

        chunk = {
            "content":  code_text,
            "context":  context,
            "filePath": filepath,
            "version":  version,
            "module":   module,
        }

        if target_class == JAVA_CLASS:
            java_chunks.append(chunk)
        else:
            kotlin_chunks.append(chunk)

        # Remove extracted code blocks from the soup so they don't
        # appear in the prose text
        listing.decompose()

    # ── Extract prose (Java/Kotlin blocks now removed) ────────────────
    prose_text = content_el.get_text(separator="\n", strip=True)
    prose_chunks = [
        {
            "content":   chunk,
            "filePath":  filepath,
            "version":   version,
            "component": "spring-boot",
            "module":    module,
            "hasCode":   False,  # Java/Kotlin removed; may still have xml/shell
        }
        for chunk in chunk_text(prose_text, CHUNK_SIZE, CHUNK_OVERLAP)
    ]

    # Flag prose chunks that still contain inline config/shell code
    for chunk in prose_chunks:
        if re.search(r"(spring\.|server\.|management\.|logging\.|\$\s)", chunk["content"]):
            chunk["hasCode"] = True

    return prose_chunks, java_chunks, kotlin_chunks


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += chunk_size - overlap
    return chunks


def upsert_batch(client, collection: str, objects: list[dict], label: str):
    """Upsert a list of objects into a Weaviate collection in batches."""
    if not objects:
        print(f"  ℹ️  No {label} chunks to upsert")
        return
    with client.batch.fixed_size(batch_size=100) as batch:
        for i, obj in enumerate(objects):
            batch.add_object(collection=collection, properties=obj)
            if (i + 1) % 100 == 0:
                print(f"  ... {label}: upserted {i + 1}/{len(objects)}")
    print(f"  ✅ {label}: {len(objects)} chunks indexed")


# ─────────────────────────────────────────────
# Main ingestion
# ─────────────────────────────────────────────

def ingest(site_path: Path, weaviate_url: str):
    # 1. Connect
    host = weaviate_url.replace("http://", "").replace("https://", "").split(":")[0]
    port = int(weaviate_url.split(":")[-1]) if ":" in weaviate_url else 8080
    client = weaviate.connect_to_local(host=host, port=port)
    print(f"✅ Connected to Weaviate at {weaviate_url}")

    # 2. Detect version
    version = extract_version(site_path)
    print(f"ℹ️  Detected version: {version}")

    # 3. Collect files
    html_files = collect_html_files(site_path)
    if not html_files:
        print("❌ No HTML files found. Check your --site-path.")
        return

    # 4. Parse all files
    print("📖 Parsing HTML — extracting prose, Java and Kotlin separately...")
    all_prose  = []
    all_java   = []
    all_kotlin = []
    skipped    = 0

    for html_file in html_files:
        prose, java, kotlin = parse_html_file(html_file, site_path, version)
        if not prose and not java and not kotlin:
            skipped += 1
            continue
        all_prose.extend(prose)
        all_java.extend(java)
        all_kotlin.extend(kotlin)

    print(f"✅ Parsed {len(html_files) - skipped} files ({skipped} skipped)")
    print(f"   Prose chunks  : {len(all_prose)}")
    print(f"   Java samples  : {len(all_java)}")
    print(f"   Kotlin samples: {len(all_kotlin)}")

    # 5. Upsert all three collections
    print("\n🚀 Upserting to Weaviate...")
    upsert_batch(client, DOC_CLASS,    all_prose,  "SpringBootDoc")
    upsert_batch(client, JAVA_CLASS,   all_java,   "SpringBootJava")
    upsert_batch(client, KOTLIN_CLASS, all_kotlin, "SpringBootKotlin")

    total = len(all_prose) + len(all_java) + len(all_kotlin)
    print(f"\n✅ Done! {total} total chunks indexed across 3 collections")
    client.close()


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest Spring Boot Antora built site into Weaviate"
    )
    parser.add_argument(
        "--site-path",
        required=True,
        help="Path to the Antora built site (contains reference/, how-to/, etc.)",
    )
    parser.add_argument(
        "--weaviate-url",
        default="http://localhost:8080",
        help="Weaviate instance URL (default: http://localhost:8080)",
    )
    args = parser.parse_args()

    ingest(
        site_path=Path(args.site_path),
        weaviate_url=args.weaviate_url,
    )
