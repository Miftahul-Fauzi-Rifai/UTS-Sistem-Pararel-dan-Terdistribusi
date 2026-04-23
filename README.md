# UTS Sistem Terdistribusi dan Parallel

Pub-Sub Log Aggregator dengan Idempotent Consumer dan Deduplication.

## Ringkasan

Sistem ini menerima event log melalui API, memprosesnya secara asynchronous, dan mencegah pemrosesan ganda memakai dedup key `(topic, event_id)`.

- API: FastAPI
- Queue: `asyncio.Queue`
- Store: SQLite (`/app/data/dedup.db`)
- Endpoint utama: `/publish`, `/events`, `/stats`, `/health`

## Struktur Proyek

```text
src/
  main.py
  models.py
  service.py
  dedup_store.py
  publisher_sim.py
tests/
  test_app.py
requirements.txt
Dockerfile
docker-compose.yml
report.md
README.md
```

## Prasyarat

- Docker Desktop (Windows/Mac) atau Docker Engine (Linux)
- Opsional: Python 3.11 untuk run tanpa Docker

## Quick Start (Docker)

Catatan PowerShell:

- Gunakan `curl.exe` (bukan `curl`) agar tidak kena alias `Invoke-WebRequest`.
- Gunakan flag `-s` agar output ringkas berupa JSON body.

### 1. Build image

```bash
docker build -t uts-aggregator .
```

### 2. Run container (nama tetap `uts-agg`)

Mode background (disarankan):

```bash
docker rm -f uts-agg
docker run --name uts-agg -d -p 8080:8080 -v uts_aggregator_data:/app/data uts-aggregator
```

Lihat log realtime:

```bash
docker logs -f uts-agg
```

Mode foreground (log Uvicorn langsung di terminal):

```bash
docker rm -f uts-agg
docker run --name uts-agg -p 8080:8080 -v uts_aggregator_data:/app/data uts-aggregator
```

### 3. Verifikasi cepat

```bash
curl.exe -s http://localhost:8080/health
curl.exe -s http://localhost:8080/stats
```

## Simulasi 5000 Event (20 persen duplikasi)

```bash
python -m src.publisher_sim --url http://localhost:8080/publish --count 5000 --duplicate-rate 0.2
curl.exe -s http://localhost:8080/stats
```

Output awal yang normal:

- `received`: 5000
- `unique_processed`: 4000
- `duplicate_dropped`: 1000

## Perilaku Dedup Antar-Run

Dedup dihitung per pasangan `(topic, event_id)`. Karena data persisten di volume, hasil run berikutnya tergantung ID yang dikirim.

- Run ulang dengan `--id-prefix` sama: mayoritas jadi duplicate.
- Run dengan `--id-prefix` berbeda: akan menambah unique baru.

Contoh menambah data baru:

```bash
python -m src.publisher_sim --url http://localhost:8080/publish --count 5000 --duplicate-rate 0.2 --id-prefix run2
```

## Reset Total Data (stats + dedup)

Jika ingin benar-benar mulai dari nol:

```bash
docker ps -aq --filter "volume=uts_aggregator_data" | ForEach-Object { docker rm -f $_ }
docker volume rm -f uts_aggregator_data
docker run --name uts-agg -d -p 8080:8080 -v uts_aggregator_data:/app/data uts-aggregator
curl.exe -s http://localhost:8080/stats
```

Target setelah reset:

- `received`: 0
- `unique_processed`: 0
- `duplicate_dropped`: 0

## Troubleshooting

### Port 8080 sudah dipakai

Gejala:

- `Bind for 0.0.0.0:8080 failed: port is already allocated`

Solusi:

```bash
docker ps
docker rm -f uts-agg
```

Jika yang berjalan bernama acak (bukan `uts-agg`), hapus via ID atau nama dari `docker ps -a`.

### Error `No such container: uts-agg`

Berarti container aktif bukan bernama `uts-agg`.

```bash
docker ps -a
docker rm -f <container_name_or_id>
```

### `curl` menampilkan warning panjang di PowerShell

Gunakan:

```bash
curl.exe -s http://localhost:8080/stats
```

## Testing

Jalankan unit test:

```bash
pytest -q
```

## Docker Compose (Opsional)

Jalankan aggregator:

```bash
docker compose up --build aggregator
```

Jalankan demo aggregator + publisher:

```bash
docker compose --profile demo up --build
```

## Keputusan Desain

- Idempotency: UNIQUE key `(topic, event_id)` di SQLite.
- Dedup persistence: data event tersimpan di `/app/data/dedup.db`.
- Stats persistence: `received`, `unique_processed`, `duplicate_dropped` disimpan di SQLite (`service_counters`).
- Reliability: aman untuk model at-least-once delivery.
- Ordering: tidak menjamin total ordering global, memakai `processing_order` lokal.

## Video Demo

Tambahkan link video YouTube publik (5-8 menit):

- Link video: `ISI_LINK_YOUTUBE_DI_SINI`

## Referensi

Pembahasan teori lengkap ada di `report.md`.
