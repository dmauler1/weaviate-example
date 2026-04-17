# Spring Boot 4 Documentation → Weaviate
## RAG Vector Database Ingestion Runbook

> Step-by-step guide to ingesting Spring Boot 4 docs into a Weaviate vector database, enabling semantic search and MCP-powered code validation via Claude Code CLI or a local LLM.

---

## Overview

The final system provides three Weaviate collections:

| Collection | Contents |
|---|---|
| `SpringBootDoc` | Prose documentation chunks (config/shell examples included) |
| `SpringBootJava` | Java code samples with surrounding context |
| `SpringBootKotlin` | Kotlin code samples with surrounding context |

These are exposed via an MCP server connected to Claude Code, allowing real-time querying of the official Spring Boot 4 docs during development.

---

## Prerequisites

### System Dependencies

| Dependency | Install | Notes |
|---|---|---|
| Python 3.11+ | system package manager | Required for ingestion script |
| Java 17+ | system package manager or sdkman | Required to build Spring Boot docs |
| Node.js + npm | system package manager | Required for Antora build |
| asciidoctor | `pacman -S asciidoctor` (Arch) | Ruby-based doc processor |
| Docker + Compose | docs.docker.com | To run Weaviate |
| uv / uvx | `pip install uv` | To run the MCP server |
| Claude Code CLI | `npm install -g @anthropic-ai/claude-code` | For MCP integration |

### Python Dependencies

```bash
python -m venv venv && source venv/bin/activate
pip install weaviate-client beautifulsoup4 lxml pyyaml packaging
```

---

## Step 1: Run Weaviate with text2vec-transformers

Create a `docker-compose.yml`:

```yaml
version: '3.4'
services:
  weaviate:
    image: semitechnologies/weaviate:latest
    ports:
      - '8080:8080'
      - '50051:50051'
    environment:
      QUERY_DEFAULTS_LIMIT: 25
      AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED: 'true'
      PERSISTENCE_DATA_PATH: '/var/lib/weaviate'
      DEFAULT_VECTORIZER_MODULE: 'text2vec-transformers'
      ENABLE_MODULES: 'text2vec-transformers'
      TRANSFORMERS_INFERENCE_API: 'http://t2v-transformers:8080'
  t2v-transformers:
    image: semitechnologies/transformers-inference:sentence-transformers-multi-qa-MiniLM-L6-cos-v1
    environment:
      ENABLE_CUDA: '0'
```

Start Weaviate:

```bash
docker compose up -d
```

> ℹ️ Wait 30-60 seconds for the transformer model to load before creating schemas.

---

## Step 2: Create Weaviate Schema

Run the following three curl commands to create the collections. All use `text2vec-transformers` for vectorization, with metadata fields set to `skip: true` so only `content` is vectorized.

### SpringBootDoc

```bash
curl -X POST http://localhost:8080/v1/schema -H "Content-Type: application/json" -d '{"class":"SpringBootDoc","description":"Spring Boot documentation prose","vectorizer":"text2vec-transformers","moduleConfig":{"text2vec-transformers":{"vectorizeClassName":false}},"properties":[{"name":"content","dataType":["text"]},{"name":"filePath","dataType":["text"],"moduleConfig":{"text2vec-transformers":{"skip":true}}},{"name":"version","dataType":["text"],"moduleConfig":{"text2vec-transformers":{"skip":true}}},{"name":"component","dataType":["text"],"moduleConfig":{"text2vec-transformers":{"skip":true}}},{"name":"module","dataType":["text"],"moduleConfig":{"text2vec-transformers":{"skip":true}}},{"name":"hasCode","dataType":["boolean"],"moduleConfig":{"text2vec-transformers":{"skip":true}}}]}'
```

### SpringBootJava

```bash
curl -X POST http://localhost:8080/v1/schema -H "Content-Type: application/json" -d '{"class":"SpringBootJava","description":"Java code samples","vectorizer":"text2vec-transformers","moduleConfig":{"text2vec-transformers":{"vectorizeClassName":false}},"properties":[{"name":"content","dataType":["text"]},{"name":"context","dataType":["text"],"moduleConfig":{"text2vec-transformers":{"skip":true}}},{"name":"filePath","dataType":["text"],"moduleConfig":{"text2vec-transformers":{"skip":true}}},{"name":"version","dataType":["text"],"moduleConfig":{"text2vec-transformers":{"skip":true}}},{"name":"module","dataType":["text"],"moduleConfig":{"text2vec-transformers":{"skip":true}}}]}'
```

### SpringBootKotlin

```bash
curl -X POST http://localhost:8080/v1/schema -H "Content-Type: application/json" -d '{"class":"SpringBootKotlin","description":"Kotlin code samples","vectorizer":"text2vec-transformers","moduleConfig":{"text2vec-transformers":{"vectorizeClassName":false}},"properties":[{"name":"content","dataType":["text"]},{"name":"context","dataType":["text"],"moduleConfig":{"text2vec-transformers":{"skip":true}}},{"name":"filePath","dataType":["text"],"moduleConfig":{"text2vec-transformers":{"skip":true}}},{"name":"version","dataType":["text"],"moduleConfig":{"text2vec-transformers":{"skip":true}}},{"name":"module","dataType":["text"],"moduleConfig":{"text2vec-transformers":{"skip":true}}}]}'
```

---

## Step 3: Build the Spring Boot 4 Antora Docs

The Spring Boot documentation uses Antora (Node.js based) with custom JavaScript extensions including the `include-code::` macro. These extensions are not available in plain `asciidoctor`, so the full Antora build is required to get resolved code samples.

### Clone the Repository

```bash
git clone https://github.com/spring-projects/spring-boot.git
cd spring-boot
```

### Build the Documentation

> ⚠️ The project structure in Spring Boot 4 uses `:documentation:spring-boot-docs`, not `spring-boot-project` as shown in older wiki entries.

```bash
./gradlew :documentation:spring-boot-docs:antora
```

> ℹ️ This build requires Java 17+, Node.js, and npm. The first run downloads all Antora npm packages and may take 5-10 minutes. Subsequent runs are faster due to caching.

### Verify the Output

The built site will be at `documentation/spring-boot-docs/build/site/` with this structure:

```
build/site/
  reference/    how-to/    tutorial/
  appendix/     cli/       gradle-plugin/
  maven-plugin/ specification/
```

---

## Step 4: Run the Ingestion Script

The ingestion script reads the built HTML site and indexes it into the three Weaviate collections. It uses BeautifulSoup to parse Antora's HTML structure, extracting Java (`language-java hljs`) and Kotlin (`language-kotlin hljs`) code blocks into their own collections while keeping prose clean in `SpringBootDoc`.

### Key Design Decisions

- Java/Kotlin code blocks extracted via `div.listingblock → code[class]` selectors
- Each code chunk stores a `context` field — the nearest preceding paragraph — so the LLM knows what the code demonstrates
- XML, shell, and Gradle blocks stay inline in prose as they are config examples, not code samples
- `article.doc` selector targets only content, stripping nav/sidebar/footer chrome
- Version detected automatically from the built site `index.html`

### Run the Script

```bash
python ingest_springboot_docs.py \
  --site-path ./spring-boot/documentation/spring-boot-docs/build/site \
  --weaviate-url http://localhost:8080
```

Expected output:

```
✅ Connected to Weaviate at http://localhost:8080
ℹ️  Detected version: 4.0.x
✅ Found N content HTML files to index
📖 Parsing HTML — extracting prose, Java and Kotlin separately...
✅ Parsed N files
   Prose chunks  : XXXX
   Java samples  : XXXX
   Kotlin samples: XXXX
🚀 Upserting to Weaviate...
✅ Done! XXXX total chunks indexed across 3 collections
```

---

## Step 5: Verify the Index

### Check chunk counts per module

```bash
curl -X POST http://localhost:8080/v1/graphql -H "Content-Type: application/json" -d '{"query":"{Aggregate{SpringBootDoc{meta{count}module{topOccurrences{value occurs}}}}}"}'
```

### Test semantic search

```bash
curl -X POST http://localhost:8080/v1/graphql -H "Content-Type: application/json" -d '{"query":"{Get{SpringBootDoc(nearText:{concepts:[\"RestController RequestMapping\"]},limit:3){content filePath module}}}"}'
```

### Test Java code search

```bash
curl -X POST http://localhost:8080/v1/graphql -H "Content-Type: application/json" -d '{"query":"{Get{SpringBootJava(nearText:{concepts:[\"RestController example\"]},limit:3){content context module}}}"}'
```

---

## Step 6: Connect MCP Server to Claude Code

The `mcp-weaviate` package exposes your Weaviate instance as an MCP server that Claude Code can query during sessions.

### Install uvx

```bash
pip install uv
# Find the path with:
which uvx
```

### Register the MCP Server

```bash
claude mcp add-json --scope user "spring-boot-docs" \
    '{"command":"/home/coder/.pyenv/shims/uvx","args":["mcp-weaviate","--connection-type","local","--host","localhost","--port","8080","--grpc-port","50051"]}'
```

> ⚠️ The `uvx` path will differ per system — find yours with `which uvx`. The `--scope user` flag ensures the server is available in all projects, not just the current directory. The config is stored in `~/.claude.json` under the top-level `mcpServers` key. If you accidentally add it without `--scope global` it will be project-scoped (under `projects[path].mcpServers`) and may conflict — edit `~/.claude.json` directly to remove any project-scoped duplicate.

### Verify Connection

```bash
claude mcp list
# Expected:
# spring-boot-docs: /path/to/uvx mcp-weaviate ... - ✓ Connected
```

### Test in Claude Code

```bash
claude
```

Then ask:
```
Search the spring boot docs for how to configure a RestController
```

Claude Code will query Weaviate via the MCP server and return results grounded in your indexed documentation.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Gradle build fails: project not found | Spring Boot 4 uses `:documentation:spring-boot-docs` not `spring-boot-project:spring-boot-docs`. Run `./gradlew projects` to confirm. |
| `include-code::` still appearing in results | Plain `asciidoctor` was used instead of the full Antora build. Always use `./gradlew :documentation:spring-boot-docs:antora`. |
| MCP shows `✗ Failed to connect` | Check `~/.claude.json` — the project-scoped entry may be using `uv` instead of `uvx`. Remove it, keep only the top-level `mcpServers` entry. |
| Weaviate vectorization slow/failing | The `t2v-transformers` container needs 30-60s to load the model. Check logs: `docker compose logs t2v-transformers` |
| Zero chunks in a collection | Check that the Antora build completed and `build/site/` contains HTML. Re-run `./gradlew :documentation:spring-boot-docs:antora`. |
| `gem install` fails with permission error | On Arch Linux do not run `sudo gem update --system` — it breaks pacman-managed Ruby. Use `gem install --user-install` for user-level gems only. |

---

## Architecture Notes

### Why text2vec-transformers (not OpenAI)

Weaviate is configured with the `text2vec-transformers` module, which runs a local sentence-transformers model (`multi-qa-MiniLM-L6-cos-v1`) in a sidecar container. Vectorization happens automatically on insert — no external API key is needed and the system works fully offline.

### Why metadata fields use `skip: true`

Only the `content` field is vectorized. Metadata fields like `filePath`, `version`, `module`, and `hasCode` have `skip: true` set in `moduleConfig`. This prevents them from polluting the semantic vector — their values would pull the embedding away from the actual meaning of the content. They are used as filters in queries instead.

### Why three collections instead of one

Mixing prose, Java code, and Kotlin code in the same chunks produces lower quality vectors because the embedding model treats code syntax very differently from natural language prose. Separating them means semantic searches against `SpringBootDoc` return conceptual explanations, while searches against `SpringBootJava` return concrete implementations. The `context` field on each code chunk bridges the two — it stores the nearest preceding paragraph so the LLM always knows what the code is demonstrating.

### Why the full Antora build is required

Spring Boot 4 uses custom Antora JavaScript extensions including `@springio/asciidoctor-extensions/include-code-extension`. This macro resolves code examples from the Java source tree at build time. Plain `asciidoctor` (Ruby) cannot resolve these — only the full Antora Node.js build pipeline can. Attempting to use `asciidoctor` directly leaves `include-code::ClassName[]` placeholders in the indexed content instead of actual code.

---

## File Reference

The ingestion script (`ingest_springboot_docs.py`) key functions:

| Function | Purpose |
|---|---|
| `collect_html_files()` | Finds all content HTML, skips site chrome and `api/` directory |
| `extract_version()` | Detects Spring Boot version from built site `index.html` |
| `parse_html_file()` | Extracts prose, Java, and Kotlin separately using BeautifulSoup |
| `get_context_for_block()` | Walks backwards from a code block to find nearest prose context |
| `chunk_text()` | Splits text into overlapping word-count based chunks (512 words, 64 overlap) |
| `upsert_batch()` | Batched upsert to Weaviate in groups of 100 |

> ℹ️ The ingestion script requires `beautifulsoup4` and `lxml`. It does **not** require llama-index, asciidoctor, or any embedding model — all vectorization is handled by Weaviate's `text2vec-transformers` module.
