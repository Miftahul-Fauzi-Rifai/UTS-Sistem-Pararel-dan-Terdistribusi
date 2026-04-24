# Laporan UTS Sistem Terdistribusi dan Parallel

## Tema

Pub-Sub Log Aggregator dengan Idempotent Consumer dan Deduplication.

## 1. Ringkasan Sistem

Sistem ini merupakan layanan log aggregator lokal berbasis pola publish-subscribe menggunakan FastAPI, asyncio.Queue, dan SQLite. Publisher mengirim event ke endpoint POST /publish dalam bentuk single event atau batch. Event divalidasi, dimasukkan ke antrean internal, lalu diproses oleh consumer secara asynchronous.

Consumer melakukan deduplication berdasarkan pasangan kunci (topic, event_id). Jika pasangan tersebut belum pernah diproses, event disimpan ke SQLite sebagai event unik. Jika sudah ada, event ditandai sebagai duplikasi dan tidak diproses ulang. Pendekatan ini membuat consumer bersifat idempotent.

Sistem menyediakan endpoint GET /events?topic=... untuk mengambil event unik, serta GET /stats untuk observability minimum: received, unique_processed, duplicate_dropped, topics, queue_size, dan uptime. Seluruh komponen berjalan lokal dalam container Docker tanpa layanan eksternal. Persistensi dedup dijaga melalui file SQLite pada volume container sehingga tetap efektif setelah restart. Desain ini menargetkan model at-least-once delivery dengan state akhir yang konsisten melalui deduplication.

## 2. Arsitektur Sistem

```
flowchart LR
	P[Publisher] --> API[POST /publish\nFastAPI Ingress + Validasi]
	API --> Q[asyncio.Queue\nInternal Buffer]
	Q --> C[Idempotent Consumer]
	C --> D[(SQLite Dedup Store\nUNIQUE(topic, event_id))]
	D --> E[GET /events]
	D --> S[GET /stats]
```

Penjelasan singkat:

1. Jalur tulis (publish) dan jalur baca (events/stats) dipisahkan agar alur mudah dilacak.
2. Ingestion dan processing dipisahkan lewat queue supaya API tetap responsif saat burst.
3. Consumer idempotent memastikan event dengan kunci (topic, event_id) tidak diproses dua kali.
4. SQLite menyimpan dedup key dan event sehingga state tetap konsisten setelah restart.

## 3. Keputusan Desain Implementasi

1. Framework API: FastAPI, karena validasi schema dan performa I/O baik untuk event ingestion.
2. Pipeline internal: asyncio.Queue untuk pola publish-subscribe in-process yang sederhana.
3. Idempotency dan dedup: SQLite dengan UNIQUE(topic, event_id).
4. Persistensi restart: dedup store disimpan di volume Docker (`/app/data/dedup.db`).
5. Stats persistence: counter received, unique_processed, duplicate_dropped juga disimpan di SQLite.
6. Ordering: tidak menjamin total ordering global; memakai event timestamp dan processing order lokal.
7. Observability: endpoint stats dan log warning saat duplicate event terdeteksi.

## 4. Jawaban Teori T1-T8

### T1 (Bab 1): Karakteristik sistem terdistribusi dan trade-off pada Pub-Sub log aggregator

Sistem terdistribusi memiliki komponen yang berjalan independen, berkomunikasi melalui jaringan, dan tidak berbagi global clock tunggal. Konsekuensinya, sistem rentan terhadap partial failure, ketidakpastian latency, serta perbedaan persepsi waktu antar node. Dalam Pub-Sub log aggregator, karakteristik ini tampak pada pemisahan peran publisher dan consumer. Publisher fokus mengirim event, sedangkan consumer memproses event secara asynchronous. Pemisahan ini meningkatkan loose coupling dan scalability karena publisher tidak perlu menunggu pemrosesan selesai untuk melanjutkan kerja.

Trade-off utama muncul pada konsistensi versus ketersediaan. Untuk menjaga throughput tinggi, sistem cenderung menerima event secepat mungkin, tetapi risiko duplicate dan out-of-order meningkat. Karena itu, mekanisme idempotency dan dedup menjadi kontrol penting agar state akhir tetap benar meskipun event dikirim ulang. Trade-off tambahan ada pada biaya penyimpanan dedup: semakin lama horizon dedup yang diinginkan, semakin besar data kunci yang harus disimpan dan dicek. Pada implementasi ini, SQLite dipilih karena sederhana, durable, dan mudah direproduksi di Docker, walau tidak seelastis distributed database. Pilihan tersebut sesuai prinsip dasar sistem terdistribusi tentang transparansi, scalability, dan penanganan kegagalan parsial (Tanenbaum & Van Steen, 2007, Bab 1).

### T2 (Bab 2): Perbandingan client-server dan publish-subscribe

Arsitektur client-server cocok untuk skenario sederhana ketika klien mengirim request sinkron dan server langsung memproses lalu merespons. Model ini mudah dipahami, tetapi coupling antara pengirim dan pemroses tinggi. Pada beban log burst, publisher dapat terdampak oleh lambatnya pemrosesan server sehingga throughput menurun. Di sisi lain, publish-subscribe memisahkan producer dan consumer melalui event channel. Pemisahan ini memberi asynchronous decoupling: publisher cukup publish, consumer memproses sesuai kapasitasnya.

Untuk use case aggregator, pub-sub lebih tepat karena aliran data bersifat event stream, volume dapat melonjak, dan duplicate akibat retry adalah kondisi normal. Dengan adanya antrean internal, sistem bisa melakukan buffering singkat dan isolasi antar komponen. Ini juga mempermudah evolusi arsitektur ke banyak subscriber tanpa mengubah cara publisher mengirim event. Client-server masih layak jika volume kecil dan kebutuhan reliability rendah, tetapi untuk kebutuhan idempotency, deduplication, dan observability operasional, pub-sub menawarkan struktur yang lebih robust. Pemilihan ini sejalan dengan konsep pemisahan tanggung jawab komponen dan fleksibilitas arsitektur terdistribusi (Tanenbaum & Van Steen, 2007, Bab 2).

### T3 (Bab 3): At-least-once versus exactly-once dan peran idempotent consumer

At-least-once delivery menjamin event pada akhirnya terkirim, tetapi event yang sama bisa sampai lebih dari sekali akibat retry atau ketidakjelasan status acknowledgement. Exactly-once secara konsep lebih ideal karena setiap event diproses tepat sekali, namun implementasi end-to-end biasanya mahal dan kompleks karena memerlukan koordinasi ketat antara transport, broker, dan storage. Dalam praktik sistem terdistribusi, at-least-once sering dipilih karena lebih realistis terhadap failure.

Konsekuensi at-least-once adalah consumer harus idempotent. Idempotent berarti memproses event yang sama berkali-kali tetap menghasilkan efek akhir yang sama seperti sekali proses. Implementasi ini memakai kunci dedup (topic, event_id) di SQLite dengan UNIQUE constraint. Saat duplicate datang, insert gagal secara deterministik dan event dihitung sebagai duplicate_dropped, bukan diproses ulang. Mekanisme ini mencegah double-write yang dapat terjadi ketika crash terjadi setelah commit tetapi sebelum ack sampai ke publisher. Jadi, idempotent consumer bukan tambahan opsional, melainkan syarat utama agar at-least-once tetap aman untuk state. Pendekatan retry ditambah idempotent processing sejalan dengan pembahasan delivery semantics pada sistem terdistribusi (Tanenbaum & Van Steen, 2007, Bab 3).

### T4 (Bab 4): Skema penamaan topic dan event_id

Skema penamaan yang baik harus konsisten, bermakna, dan mudah diproses. Untuk topic, pola hierarkis seperti domain.service.stream memberi keuntungan pada routing, filtering, dan observability. Contoh: orders.checkout.logs atau inventory.sync.events. Struktur ini membantu pengelompokan namespace antar domain sehingga tidak bercampur. Dalam konteks sistem terdistribusi, naming bukan hanya label, tetapi fondasi referensi antar komponen.

Untuk event_id, targetnya adalah uniqueness tinggi dan collision resistance. Opsi praktis yang dianjurkan adalah UUIDv4, UUIDv7, atau ULID. Pada sistem ini, dedup dijalankan berdasarkan pasangan (topic, event_id), sehingga event_id yang sama pada topic berbeda tetap dianggap entitas berbeda. Keputusan ini penting karena banyak sistem multi-domain memakai generator ID berbeda per layanan. Dampak skema naming terhadap dedup sangat langsung: bila event_id lemah atau tidak stabil, event valid dapat salah ditolak sebagai duplikasi, atau duplikasi lolos sebagai event baru. Karena itu standar format event_id perlu didokumentasikan di tingkat publisher. Skema naming yang kuat meningkatkan akurasi dedup dan mempermudah audit kejadian saat troubleshooting (Tanenbaum & Van Steen, 2007, Bab 4).

### T5 (Bab 5): Ordering dan batasan pendekatan praktis

Total ordering global tidak selalu dibutuhkan pada log aggregator. Jika tujuan utama adalah ingest, dedup, dan penyajian event unik untuk observability, ordering per aliran atau processing order lokal umumnya cukup. Memaksakan total order global menambah biaya koordinasi, meningkatkan latency, dan memperbesar ketergantungan pada komponen sequencer tunggal. Untuk skenario UTS dengan satu node aggregator, biaya itu tidak sebanding dengan manfaat.

Pendekatan praktis yang dipakai adalah kombinasi timestamp event dari publisher dan processing_order lokal dari SQLite autoincrement. Timestamp memberi informasi waktu kejadian dari sumber, sedangkan processing_order merepresentasikan urutan diterima oleh aggregator. Kombinasi ini cukup untuk analisis operasional dasar. Namun ada keterbatasan penting: clock antar publisher dapat skew, sehingga timestamp tidak selalu mencerminkan causal order sebenarnya. Selain itu, processing_order hanya valid di instance lokal dan tidak dapat dianggap urutan global lintas node. Oleh karena itu, sistem ini secara eksplisit tidak menjanjikan total ordering global. Fokus utama tetap pada correctness dedup dan stabilitas state akhir. Pilihan ini konsisten dengan pembahasan clocks, ordering, dan practical compromises pada sistem terdistribusi (Tanenbaum & Van Steen, 2007, Bab 5).

### T6 (Bab 6): Failure modes dan strategi mitigasi

Failure mode utama pada aggregator adalah duplicate delivery, out-of-order arrival, dan crash process. Duplicate delivery muncul dari retry publisher saat timeout atau saat status ack tidak pasti. Out-of-order muncul karena variasi delay jaringan dan concurrency pengirim. Crash dapat terjadi ketika service berhenti tiba-tiba di tengah pemrosesan, yang berisiko menimbulkan ketidaksesuaian antara event yang sudah diterima dan state yang tersimpan.

Strategi mitigasi yang digunakan adalah: pertama, penerapan idempotent consumer dengan dedup key (topic, event_id) untuk menahan efek duplikasi. Kedua, penyimpanan dedup pada SQLite file-based agar tahan restart. Ketiga, logging warning untuk setiap duplikasi agar kualitas delivery channel mudah dipantau. Keempat, penggunaan antrean asynchronous untuk memisahkan fase penerimaan event dan fase pemrosesan, sehingga burst traffic tidak langsung membebani penyimpanan. Untuk kegagalan sementara di sisi pengirim, retry dengan backoff tetap direkomendasikan sebagai praktik operasional. Sistem ini tidak memaksakan reorder global terhadap out-of-order karena fokus utamanya ingestion reliability, bukan strict sequencing. Dengan strategi tersebut, sistem dirancang gagal secara aman, lalu pulih tanpa menghasilkan double-processing state. Prinsip ini sejalan dengan fault tolerance pada sistem terdistribusi (Tanenbaum & Van Steen, 2007, Bab 6).

### T7 (Bab 7): Eventual consistency dalam konteks aggregator

Eventual consistency berarti bahwa jika tidak ada update baru, state sistem akan konvergen ke kondisi stabil yang sama. Pada aggregator ini, konvergensi diartikan sebagai himpunan event unik yang pada akhirnya tetap, walaupun selama proses terjadi retry dan duplicate delivery. Karena model delivery bersifat at-least-once, duplicate adalah konsekuensi normal yang harus ditoleransi, bukan dianggap anomali langka.

Idempotency dan deduplication adalah mekanisme inti untuk mencapai konvergensi tersebut. Ketika event duplikat diterima, operasi insert ke SQLite ditolak oleh UNIQUE(topic, event_id), sehingga state tidak berubah. Artinya, state akhir tidak bergantung pada berapa kali publisher mengirim ulang event yang sama. Setelah lalu lintas retry berhenti, isi store akan stabil pada satu rekaman per event unik. Ini menghasilkan konsistensi praktis untuk kebutuhan observability dan audit log. Tanpa idempotency, duplicate akan menumpuk dan menghasilkan state divergen, sehingga statistik menjadi menyesatkan. Dengan dedup store yang durable, restart service tidak merusak konvergensi state karena identitas event yang sudah diproses tetap tersimpan. Pendekatan ini selaras dengan konsep consistency model pragmatis pada sistem terdistribusi (Tanenbaum & Van Steen, 2007, Bab 7).

### T8 (Bab 1-7): Metrik evaluasi sistem dan kaitan desain

Evaluasi sistem menggunakan metrik throughput, publish latency, duplicate rate, dedup effectiveness, dan restart safety. Throughput mengukur jumlah event per detik yang mampu diproses. Publish latency mengukur waktu respons endpoint publish saat menerima batch. Duplicate rate mengukur proporsi event berulang yang diterima dalam model at-least-once. Dedup effectiveness mengukur ketepatan sistem dalam menahan event duplikat agar tidak masuk ke hasil akhir. Restart safety memverifikasi bahwa event lama tetap dikenali sebagai duplicate setelah service restart.

Metrik tersebut terkait langsung dengan keputusan desain. Antrean asynchronous meningkatkan isolasi antara penerimaan dan pemrosesan sehingga throughput lebih stabil saat burst. Dedup store SQLite memperkuat correctness dan restart safety, walau menambah overhead I/O dibanding in-memory set. Keputusan untuk tidak memaksakan total ordering global menurunkan kompleksitas dan latency. Endpoint stats berfungsi sebagai observability minimum untuk memantau received, unique_processed, duplicate_dropped, dan distribusi topic. Dengan demikian, kualitas sistem tidak hanya dinilai dari kecepatan, tetapi dari keseimbangan antara kinerja, ketahanan kegagalan, dan konsistensi state akhir. Kerangka evaluasi ini mengintegrasikan trade-off lintas Bab 1 sampai Bab 7 secara operasional (Tanenbaum & Van Steen, 2007, Bab 1-7).

## 5. Analisis Performa dan Metrik

Pengujian skala minimum menggunakan 5000 event dengan minimal 20 persen duplikasi. Target correctness yang diperiksa adalah:

1. received = 5000
2. unique_processed = 4000
3. duplicate_dropped = 1000

Selain correctness, pengukuran waktu eksekusi publish batch dipakai sebagai indikator responsivitas. Dalam test, batas waktu diset dalam rentang wajar untuk lingkungan lokal. Hasil pengujian menunjukkan pipeline tetap responsif, dedup tetap konsisten, dan statistik sistem sesuai dengan distribusi data uji.

## 6. Validasi Deliverables

1. Kode aplikasi: folder src tersedia.
2. Unit tests: folder tests tersedia dengan 9 test.
3. Dependency file: requirements.txt tersedia.
4. Dockerfile: tersedia dan menjalankan service pada port 8080.
5. Docker Compose bonus: tersedia dengan service aggregator dan publisher.
6. Dokumentasi run: tersedia di README.md.
7. Laporan: file ini (report.md).
8. Video demo YouTube publik: perlu ditambahkan sebelum submit final.

## 7. Rencana Isi Video Demo (5-8 Menit)

1. Build image dan jalankan container.
2. Kirim event normal lalu lihat hasil GET /events dan GET /stats.
3. Kirim duplikasi event_id yang sama dan tunjukkan duplicate_dropped naik.
4. Restart container dengan volume data yang sama.
5. Kirim ulang event lama dan tunjukkan dedup tetap menolak reprocessing.
6. Ringkas arsitektur dan alasan desain (30-60 detik).

## 8. Contoh Alur Eksekusi Demo (Gaya Terminal)

Catatan Windows PowerShell:

1. Gunakan curl.exe (bukan curl) agar tidak memicu alias Invoke-WebRequest.
2. Tambahkan opsi -s agar output fokus ke body JSON.

1. Build image:

```bash
docker build -t uts-aggregator .
```

2. Jalankan container dengan volume data persisten:

```bash
docker rm -f uts-agg
docker run --name uts-agg -d -p 8080:8080 -v uts_aggregator_data:/app/data uts-aggregator
```

3. Verifikasi health endpoint:

```bash
curl.exe -s http://localhost:8080/health
```

Contoh hasil:

```text
{"status":"ok"}
```

4. Cek stats awal:

```bash
curl.exe -s http://localhost:8080/stats
```

Contoh hasil awal:

```text
{"received":0,"unique_processed":0,"duplicate_dropped":0,"topics":{},"topic_count":0,"queue_size":0}
```

5. Kirim 5000 event dengan 20% duplikasi:

```bash
python -m src.publisher_sim --url http://localhost:8080/publish --count 5000 --duplicate-rate 0.2
```

6. Cek stats sesudah publish:

```bash
curl.exe -s http://localhost:8080/stats
```

Contoh hasil utama yang diharapkan:

```text
{"received":5000,"unique_processed":4000,"duplicate_dropped":1000,...}
```

7. Restart container, lalu kirim lagi event dengan event_id yang sama.
8. Tunjukkan duplicate tetap tidak diproses ulang (duplicate_dropped bertambah, event unik tidak bertambah untuk ID yang sama).

## 9. Keterkaitan dengan Materi Bab 1-7

1. Bab 1: karakteristik sistem terdistribusi dan trade-off konsistensi-ketersediaan.
2. Bab 2: pemilihan arsitektur publish-subscribe.
3. Bab 3: delivery semantics at-least-once dan kebutuhan idempotency.
4. Bab 4: naming topic dan event identity.
5. Bab 5: waktu, ordering, dan batasan total ordering.
6. Bab 6: failure modes serta fault tolerance berbasis dedup durable.
7. Bab 7: eventual consistency melalui idempotency + deduplication.

## 10. Kesimpulan

Sistem berhasil mengimplementasikan prinsip utama sistem terdistribusi untuk skenario log aggregation lokal. Kombinasi at-least-once delivery, idempotent consumer, dan deduplication menjaga konsistensi state akhir meskipun terdapat duplicate delivery dan restart service. Dengan arsitektur queue-based dan dedup store durable, sistem tetap responsif, reproducible, dan sesuai kriteria UTS.

## 11. Daftar Pustaka (APA Edisi Ke-7)

Tanenbaum, A. S., & Van Steen, M. (2007). Distributed systems: Principles and paradigms (2nd ed.). Pearson Prentice Hall.
