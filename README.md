# CRM Ventas Alphasoft

> Agente de prospección comercial autohosted en Docker para [Alphasoft](https://www.alphasoft.cloud/). Encuentra negocios argentinos sin presencia web decente, extrae sus datos de contacto y arma mensajes de apertura listos para enviar.

El sistema combina **Scrapling** (scraping de Google Maps con browser anti-bot), **Groq** (LLM Llama 3.3 70B para redactar mensajes en español argentino, analizar sitios, clasificar respuestas y sugerir queries) y **Postgres + pgvector** sobre **FastAPI**. Todo corre 100% local en Docker, sin servicios externos pagos más allá de Groq (que tiene un tier free generoso).

**Workflow CRM B2B completo**: scraping de Maps → análisis del sitio con LLM → score 1-10 → mensaje WhatsApp+Email → cadencia automática de follow-ups (5/14/30 días) → clasificación de respuestas del cliente con LLM → dashboard con funnel, tasa de respuesta, breakdown por ciudad/rubro/score/intent → export Excel con 26 columnas.

> **⚠️ Importante — no es un auto-sender.**
> El sistema **NO envía mensajes automáticamente**. Solo prepara el WhatsApp y el Email; el operador los copia y los envía manualmente. Esto evita bloqueos de WhatsApp/Meta y cumple con la **Ley 25.326** (Datos Personales) en Argentina, que considera spam el envío masivo no opt-in. Es un *asistente de prospección*, no un *bot de spam*.

---

## ¿Qué te entrega por cada lead?

- **Nombre del negocio** (tal como aparece en Google Maps, con caracteres especiales preservados).
- **Teléfono** (extraído del listado y del detalle de Maps).
- **Email** (extraído del HTML del sitio del negocio cuando lo tiene; Maps en sí no expone email).
- **Dirección** real argentina (con código postal cuando está disponible).
- **Rating de Google** + sitio web si tiene.
- **Diagnóstico del sitio**: si no tiene, si está caído, si es Wix viejo, si es un link a Instagram/WhatsApp, si es lento, etc.
- **Razón por la que es lead calificado** (legible para humanos).
- **Score 1-10 del LLM** con razón en 1 línea: tan pronto como entendas el lead, sabés si vale la pena trabajarlo.
- **Análisis del sitio + pain points** detectados por el LLM (más allá de las heurísticas estáticas).
- **Servicio del catálogo Alphasoft sugerido** por el LLM para este lead específico.
- **Cadencia de follow-ups automática** (5/14/30 días) que el LLM genera con ángulos distintos al primer mensaje.
- **Clasificador de respuestas**: cuando el cliente responde, el LLM detecta intent (interested / pricing / not_interested / ...), sentiment, resumen, próximo paso y mensaje sugerido para responder.
- **Notas internas** + **tags** para que el equipo deje contexto y segmente.
- **Dos mensajes listos**:
  - WhatsApp corto (3-4 líneas, voseo, casual)
  - Email más largo (asunto + cuerpo, firma con datos de contacto de Alphasoft)
- **Link directo al lugar en Google Maps** (`source_id`).

Toda esta info se puede ver en la UI web, exportar a Excel, o consumir vía API JSON.

---

## Stack técnico

| Capa | Tecnología | Versión |
|---|---|---|
| Lenguaje | Python | 3.12 (en imagen Docker) |
| API web | FastAPI + Uvicorn | 0.136 / 0.32 |
| ORM | SQLAlchemy 2 (async) + Alembic | 2.0 / 1.18 |
| Base de datos | Postgres + pgvector | 16 / 0.8 |
| Scraping | Scrapling (StealthyFetcher) + patchright | 0.4.8 / 1.59 |
| Browser | Chromium con anti-detect | bundled by patchright |
| LLM | Groq SDK — `llama-3.3-70b-versatile` | 0.13 |
| Resilience | tenacity (retry exponencial) | 9.x |
| Scheduler | APScheduler (in-process) | 3.x |
| Templates | Jinja2 + HTMX + Tailwind (CDN) | 3.1 / 2.0 |
| Excel export | openpyxl | 3.1.5 |
| HTTP client | httpx (async) | 0.27 |
| Container | Docker Compose | v2 |
| Tests | pytest + pytest-asyncio | 9.x / 1.3 |

**No usamos** sentence-transformers ni vector embeddings reales: el catálogo de Alphasoft tiene ~7 servicios y entra en el contexto del LLM directamente. La columna `vector(384)` en `catalog_items` está reservada por si en el futuro se quiere similarity search.

---

## Arquitectura

```
┌────────────────────────────────────────────────────────────────────┐
│                       Docker Compose (local)                       │
│                                                                    │
│  ┌─────────────────────────┐         ┌────────────────────────┐    │
│  │   agent (FastAPI)       │  asyncpg│ postgres + pgvector    │    │
│  │   :8000                 │◄───────►│ :5432                  │    │
│  │                         │         │ pgdata (volume)        │    │
│  │  ┌──────────────────┐   │         └────────────────────────┘    │
│  │  │ UI Jinja2+HTMX   │   │                                       │
│  │  └──────────────────┘   │         ┌────────────────────────┐    │
│  │                         │ patchright playwright_cache vol   │    │
│  │  ┌──────────────────┐   │◄────────│ /root/.cache/ms-       │    │
│  │  │ jobs/            │   │         │ playwright/chromium    │    │
│  │  │  - discover      │───┼─────────►                        │    │
│  │  │  - enrich        │   │  HTTPS   ┌─────────────────────┐ │    │
│  │  │  - generate      │   │◄────────►│ Google Maps         │ │    │
│  │  └──────────────────┘   │  HTTPS   │ (scrape via         │ │    │
│  │                         │          │  Scrapling+patchright)│    │
│  │  ┌──────────────────┐   │          └─────────────────────┘ │    │
│  │  │ scrapers/        │   │                                  │    │
│  │  │  - google_maps   │   │          ┌──────────────────────┐│    │
│  │  │  - mock          │   │  HTTPS   │ Sitios de negocios   ││    │
│  │  └──────────────────┘   │◄────────►│ (enrich saca email)  ││    │
│  │                         │          └──────────────────────┘│    │
│  │  ┌──────────────────┐   │                                   │    │
│  │  │ llm/             │   │  HTTPS   ┌──────────────────────┐│    │
│  │  │  - groq_client   │───┼─────────►│ api.groq.com         ││    │
│  │  │  - mock          │   │          │ llama-3.3-70b-versatile│   │
│  │  └──────────────────┘   │          └──────────────────────┘│    │
│  └─────────────────────────┘                                       │
└────────────────────────────────────────────────────────────────────┘
```

**Flujo end-to-end:**

```
1. discover  →  Scrapling abre Maps, scrollea, extrae cards, abre detalles
                → guarda leads en `leads` (dedup por nombre+address)

2. enrich    →  Para cada lead: GET al sitio web → heurísticas (Wix/caído/
                 link-social/mobile/copyright) → marca qualified + extrae email
                 del mailto: o regex del HTML

3. generate  →  Para cada calificado sin mensaje: arma prompt con contexto +
                 catálogo Alphasoft → Groq genera WhatsApp + Email → guarda
                 en `messages`

4. UI/Excel  →  Operador filtra, copia mensaje, lo envía manual desde su
                 WhatsApp/mail → marca enviado o descarta
```

---

## Requisitos

- **Docker Desktop** (con Docker Compose v2). Probado en Windows 11 con Docker 29.
- Una **API key de Groq** (gratis en https://console.groq.com/keys — tier free: 30 RPM, 500K tokens/día).
- ~3 GB de disco para imágenes + volúmenes.

No necesitás Python instalado en el host; todo corre dentro de Docker.

---

## Setup desde cero

```bash
# 1. Clonar el repo
git clone https://github.com/UrielMaximiliano/crm-ventas-alphasoft.git
cd crm-ventas-alphasoft

# 2. Copiar el archivo de configuración
cp .env.example .env

# 3. Editar .env y poner tu GROQ_API_KEY
#    (sacarla gratis en https://console.groq.com/keys)
#    Setear MOCK_LLM=0 y MOCK_SCRAPER=0 para usar Groq + Maps reales

# 4. Levantar todo
docker compose up -d

# 5. (Primera vez si MOCK_SCRAPER=0) descargar Chromium para patchright
docker compose exec agent playwright install chromium
#    Esto baja ~112MB que quedan en un volume - no se repite en próximos arranques.

# 6. Abrir http://localhost:8000
#    Verás un listado vacío. Click "Buscar leads" para empezar.
```

El primer build de la imagen tarda ~3-5 minutos (instala Scrapling + sus deps de browser). A partir de ahí los reinicios son instantáneos.

---

## Cómo funciona (flujo del equipo)

El sistema arranca **vacío y sin scrapear** (modo `AUTOSTART_JOBS=0` por default). Cada operador decide cuándo y qué buscar. Si querés que corra en cron diario, ver "Modo automático" más abajo.

### Flujo diario típico (≈ 5 min)

1. **Levantar el sistema** (si no está corriendo):
   ```bash
   docker compose up -d
   ```
   Abrir `http://localhost:8000`.

2. **Click "Buscar leads"** (botón gris oscuro arriba a la izquierda)
   Corre 3 queries del archivo [`data/search_queries.yml`](data/search_queries.yml). Cada query toma ~30s y trae ~5 leads. El sistema scrapea Maps + abre el detalle de cada lugar para obtener teléfono y dirección confiables.

3. **Click "Verificar webs"** (botón amarillo)
   Para cada lead, va al sitio (si tiene), evalúa:
   - 4xx, 5xx, timeout → "sitio caído"
   - Meta `generator` con Wix/Joomla → "plataforma desactualizada"
   - Copyright > 3 años atrás → "sin actualizar desde YYYY"
   - Sin meta viewport → "no es mobile-friendly"
   - TTFB > 4s → "carga lenta"
   - Host = wa.me / instagram / linktr.ee / facebook → "sin sitio propio"

   **Además extrae el email** del HTML (busca `mailto:` y patrones de email, filtrando spam tipo `@wix.com`, `@example.com`).

4. **Click "Generar mensajes"** (botón verde)
   Para cada lead calificado sin mensaje, llama a Groq con:
   - Contexto del lead (nombre exacto, ciudad, rubro, rating, razón calificación).
   - Catálogo completo de servicios de Alphasoft inline.
   - System prompt con tono argentino + reglas (no inventar datos, no usar marketing-speak, voseo, etc.).

   Devuelve dos mensajes (WhatsApp + Email) que se persisten en la tabla `messages`.

5. **Trabajar los leads en la UI**
   Para cada lead calificado vas a ver una "card" con:
   - Header: nombre + ID + estado + rating
   - Datos: teléfono, email, sitio, dirección
   - Bloque del mensaje WhatsApp con botón **"copiar"**
   - Bloque del mensaje Email con botón **"copiar"**
   - Botones de acción: **Regenerar** / **Marcar enviado** / **Descartar**

   Flujo recomendado: copiar WhatsApp → pegar en tu chat → click "Marcar enviado" en la UI.

6. **Click "Exportar a Excel"** (botón verde oscuro)
   Descarga un `.xlsx` con todos los leads del filtro actual (respeta city + category + status + only_no_web). 19 columnas incluyendo los dos mensajes. Útil para repartir leads entre el equipo.

### Filtros disponibles

Arriba del listado hay un formulario para filtrar por:
- **Ciudad** (dropdown poblado con las ciudades que tienen leads)
- **Categoría** (rubro)
- **Estado** (`new`, `qualified`, `contacted`, `replied`, `discarded`)
- **Solo sin sitio web** (checkbox)

Los filtros se reflejan en el querystring y se respetan también al exportar a Excel.

### Empezar todo desde cero (vaciar DB)

```bash
docker compose exec postgres psql -U crm -d crm -c \
  "TRUNCATE TABLE messages, leads, job_runs RESTART IDENTITY CASCADE;"
```

Catálogo y configuración no se tocan. Si querés también vaciar el catálogo (raro):
```bash
docker compose exec postgres psql -U crm -d crm -c \
  "TRUNCATE TABLE catalog_items RESTART IDENTITY;"
docker compose restart agent
```

---

## Estructura del repositorio

```
crm-ventas-alphasoft/
├── docker-compose.yml             # 2 servicios: agent + postgres+pgvector
├── Dockerfile                     # python:3.12-slim + firefox/chromium libs
├── pyproject.toml                 # Deps del agent
├── alembic.ini + alembic/         # Migraciones de DB
├── .env.example                   # Plantilla de configuración
├── .gitignore
│
├── data/                          # Configuración editable sin tocar código
│   ├── alphasoft_catalog.yml      # Servicios que el agente ofrece
│   ├── search_queries.yml         # Rubros × ciudades a buscar
│   └── fixtures/                  # Datos sintéticos para modo mock
│
├── app/                           # Código de la app
│   ├── main.py                    # FastAPI + lifespan + endpoints
│   ├── config.py                  # pydantic-settings (lee .env)
│   ├── exports.py                 # Generación de XLSX (openpyxl)
│   ├── scheduler.py               # APScheduler (cron diario + intervalos)
│   │
│   ├── db/
│   │   ├── models.py              # SQLAlchemy: Lead, Message, CatalogItem, JobRun
│   │   └── session.py             # Async session factory
│   │
│   ├── scrapers/                  # Discovery de leads
│   │   ├── base.py                # Interfaz LeadProvider (Protocol)
│   │   ├── google_maps.py         # Scrapling + patchright
│   │   ├── mock.py                # Lee fixtures locales
│   │   └── factory.py             # Switch mock/real según MOCK_SCRAPER
│   │
│   ├── enrich/
│   │   └── website_check.py       # Heurísticas de calidad del sitio + email
│   │
│   ├── llm/
│   │   ├── base.py                # Interfaz LLMClient + dataclasses
│   │   ├── groq_client.py         # Cliente real con tenacity retry
│   │   ├── mock.py                # Respuestas hardcoded para CI
│   │   ├── prompts.py             # SYSTEM_PROMPT + builders WhatsApp/Email
│   │   └── factory.py             # Switch mock/real según MOCK_LLM
│   │
│   ├── rag/
│   │   ├── catalog.py             # Seed idempotente del catálogo (hash YAML)
│   │   └── retriever.py           # Keyword search sobre catálogo
│   │
│   ├── jobs/
│   │   ├── discover.py            # Iterar queries → scraper → DB
│   │   ├── enrich.py              # Website check + email para cada lead
│   │   └── generate.py            # LLM → WhatsApp + Email persistidos
│   │
│   └── web/templates/
│       ├── base.html              # Layout con Tailwind CDN
│       └── leads.html             # Listado con filtros y botones
│
└── tests/                         # pytest con mocks (no necesita red)
    ├── test_mock_llm.py
    ├── test_prompts.py
    └── test_website_check.py
```

---

## Configuración (`.env`)

| Variable | Default | Descripción |
|---|---|---|
| `POSTGRES_USER` / `_PASSWORD` / `_DB` / `_PORT` | crm / crm_local_dev / crm / 5432 | Credenciales de Postgres. No exponer en prod. |
| `DATABASE_URL` | `postgresql+asyncpg://...` | URL async para la app |
| `SYNC_DATABASE_URL` | `postgresql+psycopg2://...` | URL sync para Alembic |
| `GROQ_API_KEY` | vacío | API key de Groq (obligatoria si `MOCK_LLM=0`) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Modelo a usar. Cambiar a `openai/gpt-oss-120b` o `moonshotai/kimi-k2-instruct` si querés probar otros |
| `MOCK_LLM` | `0` | `1` = usa MockLLMClient (no consume tokens) |
| `MOCK_SCRAPER` | `0` | `1` = lee `data/fixtures/google_maps_sample.json` en vez de scrapear |
| `AUTOSTART_JOBS` | `0` | `1` = activa cron diario 09:00 AR + enrich/generate periódicos |
| `SCRAPER_MIN_DELAY_SEC` / `MAX_DELAY_SEC` | `2` / `5` | Delays randomizados entre scrolls de Maps (anti-detect) |
| `SCRAPER_DAILY_LIMIT` | `100` | Tope de leads scrapeados por día |
| `ALPHASOFT_EMAIL` | `alphasoftwebs@gmail.com` | Aparece en la firma de los emails generados |
| `ALPHASOFT_INSTAGRAM` | `@alphasoft__` | Idem |
| `ALPHASOFT_WEBSITE` | `https://www.alphasoft.cloud/` | Idem |
| `ALPHASOFT_WHATSAPP` | vacío | Número internacional sin `+` (ej: `5493511234567`) |
| `APP_HOST` / `APP_PORT` | `0.0.0.0` / `8000` | Bind del servidor uvicorn |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` |

---

## API endpoints

### UI (formularios con redirect 303)

| Método | Ruta | Acción |
|---|---|---|
| GET | `/` | Listado de leads con filtros |
| POST | `/ui/jobs/discover?max_queries=N` | Buscar leads (default 3 queries) |
| POST | `/ui/jobs/enrich` | Verificar webs + extraer emails |
| POST | `/ui/jobs/generate` | Generar mensajes pendientes |
| POST | `/ui/leads/{id}/generate` | Regenerar mensajes de un lead |
| POST | `/ui/leads/{id}/mark-sent` | Marcar mensajes como enviados → status `contacted` |
| POST | `/ui/leads/{id}/discard` | Marcar status `discarded` |

### API JSON

| Método | Ruta | Devuelve |
|---|---|---|
| GET | `/health` | `{"status":"ok"}` |
| GET | `/api/info` | Versión + flags mock/real + modelo Groq |
| GET | `/api/leads?city=&category=&status=&only_no_web=&limit=` | Lista de leads (filtros opcionales) |
| GET | `/api/leads/export.xlsx?...` | **Descarga xlsx** con los mismos filtros |
| GET | `/api/leads/{id}` | Lead individual con mensajes |
| GET | `/api/catalog` | Catálogo completo de servicios |
| GET | `/api/rag/search?q=...` | Items relevantes del catálogo por keyword |
| POST | `/api/generate-message` | Genera mensaje ad-hoc (lead inline en body, no toca DB) |
| POST | `/api/leads/{id}/generate-message` | Genera y persiste para un lead existente |
| POST | `/api/leads/{id}/mark-sent` | Marca enviado, devuelve JSON |
| POST | `/api/leads/{id}/discard` | Marca discarded, devuelve JSON |
| POST | `/api/jobs/discover?max_queries=` | Corre discover, devuelve stats |
| POST | `/api/jobs/enrich?max_leads=` | Corre enrich |
| POST | `/api/jobs/generate?max_leads=` | Corre generate |

---

## Personalización

### Cambiar rubros y ciudades de búsqueda

Editar [`data/search_queries.yml`](data/search_queries.yml):
```yaml
defaults:
  country: AR
  max_results: 5    # tope por query

queries:
  - { rubro: cafeteria de especialidad, city: Cordoba, province: Cordoba }
  - { rubro: estudio de tatuajes,       city: Rosario, province: Santa Fe }
  # ... agregar lo que necesites
```

El próximo `discover` los usa.

### Cambiar los servicios que el agente ofrece

Editar [`data/alphasoft_catalog.yml`](data/alphasoft_catalog.yml). Cada item es:
```yaml
- slug: nuevo-servicio
  name: Nombre del servicio
  category: web | ecommerce | ia | automatizacion | seo
  short_description: >
    Descripción corta (1-2 líneas).
  long_description: >
    Descripción más larga, contexto, ejemplos.
  target_audience: A quién apunta
  price_range: USD 500-2000
```

Al reiniciar el contenedor, el seed detecta el cambio (por hash del archivo) y actualiza la DB.

### Cambiar el tono / instrucciones del LLM

Editar [`app/llm/prompts.py`](app/llm/prompts.py):
- `SYSTEM_PROMPT` define la "personalidad" del agente (Uriel de Alphasoft, voseo, etc.).
- `build_whatsapp_prompt` arma el prompt específico para WhatsApp (máx 4-5 líneas).
- `build_email_prompt` arma el prompt para Email (asunto + cuerpo).

Con `--reload` activo, los cambios toman efecto al hacer "Regenerar" en un lead.

### Agregar otro proveedor de leads (alternativa a Scrapling)

1. Crear `app/scrapers/mi_provider.py` implementando la interfaz `LeadProvider` de [`app/scrapers/base.py`](app/scrapers/base.py):
   ```python
   class MiProvider:
       name = "mi_provider"
       async def search(self, query: SearchQuery, *, max_results: int = 20) -> list[ScrapedLead]: ...
   ```
2. Registrarlo en [`app/scrapers/factory.py`](app/scrapers/factory.py).

Candidatos lógicos: SerpAPI, Outscraper, Google Places API oficial, Páginas Amarillas, scraping de portales sectoriales (clarín clasificados, mercadolibre servicios, etc.).

---

## Modo automático (opcional)

Si querés que corra solo sin que nadie haga click:

1. En `.env`: `AUTOSTART_JOBS=1`
2. `docker compose restart agent`

Activa el scheduler:
- **discover** — cron diario a las **09:00 AR** (Argentina timezone)
- **enrich** — cada **2h** (solo leads sin enrich previo)
- **generate** — cada **1h** (solo leads calificados sin mensaje)
- Al arrancar, si el último discover exitoso fue hace > 24h, dispara la cadena completa inmediatamente.

Ajustar frecuencias en [`app/scheduler.py`](app/scheduler.py).

---

## Tests

```bash
docker compose exec agent pip install pytest pytest-asyncio  # primera vez
docker compose exec agent pytest tests/ -v
```

12 tests cubriendo:
- `MockLLMClient` genera mensajes válidos para todos los casos (sin web, Wix, etc.)
- Builders de prompts incluyen el contexto del lead y nombre exacto
- `assess_website` califica correctamente Wix, sitios caídos, link-social, sitios OK

No requieren red ni DB (todo mockeado). Útiles para CI.

---

## Troubleshooting

### El agent no arranca / `docker compose logs agent` muestra error de import
Probablemente falta una dep. Rebuild:
```bash
docker compose build agent
docker compose up -d --force-recreate agent
```

### `Buscar leads` no trae nada o tira timeout
- Verificar `MOCK_SCRAPER=0` en `.env`.
- Chromium puede no estar instalado:
  ```bash
  docker compose exec agent playwright install chromium
  ```
- Google Maps cambió el HTML: ver [`app/scrapers/google_maps.py:_parse_results`](app/scrapers/google_maps.py). Los selectores son frágiles por diseño (no hay API oficial).
- Si pasa seguido: bajar `SCRAPER_DAILY_LIMIT` o switchear temporalmente a `MOCK_SCRAPER=1`.

### Groq devuelve 401
- Verificar `GROQ_API_KEY` en `.env` (sin espacios, sin comillas).
- Regenerar la key en https://console.groq.com/keys

### Groq devuelve 429 — `Rate limit reached ... tokens per day`
El **tier free es 100K tokens/día**. Con `analyze_lead` activo (el LLM analiza el HTML del sitio + scoring), cada lead completamente procesado consume:
- ~4K tokens para `analyze_lead` (si el sitio carga; ~1K si está caído)
- ~2K tokens para WhatsApp
- ~3K tokens para Email
- **Total ~9K tokens × 25 leads/día = 225K** → excede el free.

**Soluciones**:
- Procesar ≤ 20 leads/día con el free → para validar.
- Upgradear a **Dev Tier** en https://console.groq.com/settings/billing (~$0.50 por millón de tokens entrada, te da para miles de leads/día).
- Los leads que fallaron quedan en DB con su intel; correr "Generar mensajes pendientes" al día siguiente completa los faltantes (idempotente).

### Los mensajes salen en otro idioma o sin voseo
- Revisar `SYSTEM_PROMPT` en [`app/llm/prompts.py`](app/llm/prompts.py).
- Probar otro modelo (algunos respetan más las instrucciones de tono).

### El email no se está extrayendo de los sitios
- Solo se extrae cuando el sitio carga (200 OK). Los sitios caídos / link-social no aplican.
- Si el email está detrás de JavaScript (SPA), el regex no lo va a ver. Solución: usar Scrapling para visitar el sitio del lead también (no implementado todavía).

### "OperationalError: connection to server failed"
Postgres no terminó de levantar todavía. Esperar 10s y reintentar. El healthcheck del compose suele esperar bien.

### La UI muestra el botón pero el Excel descarga vacío
Significa que el filtro actual no trae leads. Probar sin filtros (link "Limpiar").

---

## Limitaciones conocidas

- **Scrapling y los ToS de Google Maps**: el scraping de Maps viola los ToS de Google. Usar con criterio y volumen bajo. Para uso comercial serio considerar SerpAPI o Places API oficial.
- **Selectores frágiles**: el HTML de Google Maps cambia ocasionalmente. Si rompe, los nombres siguen apareciendo pero algunos campos pueden quedar vacíos.
- **Email solo desde sitios reales**: si el negocio usa solo Instagram/WhatsApp, no hay email para extraer.
- **Argentina-focus**: las heurísticas de "dirección" están afinadas para formato argentino (códigos postales tipo `X5000`, calles tipo `Av.`, `Bv.`, `Calle`). Para otros países hay que ajustar [`app/scrapers/base.py`](app/scrapers/base.py:_looks_like_address).
- **Sin envío automático**: por decisión de diseño (legal + bloqueos de Meta). Si necesitás auto-sender, eso es otro proyecto.

---

## Legal y responsabilidad

- **Scraping**: viola ToS de Google Maps. Uso bajo responsabilidad del operador.
- **Ley 25.326 (AR)** — Datos Personales: los datos de comercios son públicos, pero **el envío masivo no opt-in puede caer como spam regulado**. El sistema deja el envío manual a propósito: cada mensaje se envía con consciencia del operador.
- **RGPD**: si vas a contactar negocios europeos, tener en cuenta consentimiento previo (no aplicable a Argentina pero sí a UE).
- Las API keys (Groq) viajan en `.env`, que está en `.gitignore`. Nunca commitear secretos.

---

## Roadmap (mejoras posibles)

- [ ] Visitar el sitio del lead con Scrapling (no solo httpx) para sacar emails detrás de JS / SPA.
- [ ] Extraer `reviews_count` confiablemente del aria-label del rating (el formato cambia frecuente).
- [ ] Stats dashboard: leads por ciudad, tasa de respuesta, conversión.
- [ ] Auto-sync con Google Sheets o Notion para que el equipo trabaje desde donde está cómodo.
- [ ] Multi-tenant: que distintos equipos vean distintos leads (multi-empresa).
- [ ] Webhooks de respuesta (cuando un lead responde, marcar `replied` automáticamente).
- [ ] Modo "follow-up": si pasaron 7 días desde `contacted` sin `replied`, generar un mensaje de seguimiento.

---

## Créditos

Construido para [Alphasoft](https://www.alphasoft.cloud/) — desarrollo web, e-commerce, chatbots IA y automatizaciones para PyMEs y startups argentinas. Stack diseñado para correr 100% local con un único requisito de pago (Groq, que tiene tier free).
