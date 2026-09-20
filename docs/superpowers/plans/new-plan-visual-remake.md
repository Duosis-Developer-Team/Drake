Doğru. Yalnızca güzel görünen bir dashboard yetmez; Drake’in ikinci temel problemi bilgi mimarisi. Kullanıcı proje, servis, incident, cluster veya inventory kaydını menü ve sayfa derinliklerinde aramamalı. Briefin merkezine “bulunabilirlik ve iki tıklık erişim” hedefini ekliyorum.

Aşağıdaki sürüm önceki briefin güncellenmiş ve geçerli hâlidir:

# Drake Frontend, Data Visualization & UX Remake

## Acil Uygulama Briefi — Önceki Planların Tamamının Yerine Geçer

## 1. Projenin gerçek hedefi

Drake’in backend’i, API’leri, authorization sistemi ve veri altyapısı güçlü durumdadır. Bu çalışma backend’i yeniden tasarlama, yeni veri modeli kurma veya yeni operasyon semantiği üretme çalışması değildir.

Çalışmanın üç eşit derecede önemli hedefi vardır:

1. Drake’in görsel kimliğini tamamen yenilemek.
2. Mevcut verileri tablo ve metin yığınları yerine güçlü görselleştirmelerle sunmak.
3. Kullanıcının aradığı proje, servis, cluster, incident, alert, deployment veya inventory kaydına birkaç saniye içinde ulaşmasını sağlamak.

Başarı tanımı:

> Drake hem etkileyici görünmeli hem de kullanıcının aradığı bilgiye en fazla iki anlamlı etkileşimle ulaşabildiği bir operasyon ürünü olmalıdır.

Görsel güzellik tek başına yeterli değildir. Kullanıcı hâlâ bir kaydı bulmak için sidebar bölümlerini, listeleri ve detay sayfalarını tek tek gezmek zorunda kalıyorsa remake başarısızdır.

## 2. Mevcut frontend’in temel problemleri

Mevcut arayüz aşağıdaki nedenlerle kabul edilmiyor:

- Fazla metin ve tablo ağırlıklı
- Bütün içerikler birbirine benzeyen kutularda
- Ana veri ile yardımcı veri aynı görsel ağırlıkta
- Grafikler ana anlatım aracı değil
- Büyük ekran verimsiz kullanılıyor
- Mobil görünüm masaüstünün daraltılmış hâli gibi
- Navigation bilgi mimarisini yeterince açıklamıyor
- Aynı entity’ye ulaşmak için birden fazla sayfa gezmek gerekiyor
- Sayfalar arasında geçişte filtre ve bağlam kaybolabiliyor
- Global search yeterince merkezi ve görünür değil
- Incident, alert, service, project ve cluster ilişkileri kullanıcıya yol göstermiyor
- Detay sayfalarında “buradan sonra nereye gitmeliyim?” sorusunun cevabı yok
- Kritik kayıtlar ana ekranda görünse bile ilişkili kanıta ulaşmak zahmetli
- Breadcrumb ve geri dönüş davranışı yeterince güçlü değil
- Kullanıcı teknik route yapısını öğrenmek zorunda kalıyor
- Eski yeşil marka kimliği ürünü sıradan bir monitoring paneline benzetiyor

## 3. Tasarım referansı

Ana referans:

**PayFlow — Dashboard UI**

PayFlow’dan alınacak temel yaklaşım:

- Güçlü monokrom ürün kimliği
- Sıcak açık gri/kemik canvas
- Siyah/obsidian navigation
- Baskın koyu hero yüzeyi
- Büyük ve net veri tipografisi
- Asimetrik dashboard yerleşimi
- Birincil, ikincil ve yardımcı yüzeylerin farklı boyutlarda olması
- Az sayıda ve kontrollü accent rengi
- Chart ve tabloların ürün yüzeyiyle bütünleşmesi
- Amaçlı boşluk kullanımı
- Yuvarlatılmış fakat oyuncak görünmeyen geometry
- Büyük ekranlarda premium ürün kompozisyonu

PayFlow birebir kopyalanmayacak. Finans verilerinin yerine Drake’in gerçek operasyon verileri yerleştirilecek. Ancak kalite, kompozisyon, spacing, tipografi ve yüzey yaklaşımı referans seviyesinde olmalıdır.

Datalake-Platform-GUI’den alınacak yaklaşım:

- KPI strip kullanımı
- Grafiklerin tabloların önüne alınması
- Özet → görsel → detay → drill-down sırası
- Büyük sayılar ve anlaşılır dağılımlar
- Filtrelerin görsellerle aynı bağlamda bulunması
- Detay tablolarının ana dashboard’u ele geçirmemesi
- Kullanıcının görselden doğrudan ilgili detaya ilerleyebilmesi

## 4. Yeni Drake marka kimliği

### 4.1 Yeşil tamamen kaldırılıyor

Mevcut Drake yeşili yeni kimliğin parçası değildir.

Yeşil aşağıdaki alanlarda kullanılmayacak:

- Logo
- Sidebar
- Aktif navigation
- Link
- Buton
- Focus ring
- Seçili filtre
- Grafik serisi
- Hero paneli
- Dekoratif accent
- Healthy/success durumu

Eski token isimleri teknik sebeple korunursa bile görsel değerleri yeni palette dönüştürülecek.

### 4.2 Yeni kimlik

Yeni karakter:

**Obsidian + Bone + Solar Yellow**

Light tema:

- Canvas: `#EEEDEA`
- Main surface: `#FAF9F7`
- Raised surface: `#FFFFFF`
- Soft surface: `#F3F1EE`
- Obsidian: `#191816`
- Deep surface: `#24221F`
- Primary text: `#171614`
- Secondary text: `#68645F`
- Muted text: `#918C85`
- Border: `#DDD9D3`
- Strong border: `#C8C2BA`
- Brand accent: `#F2CF55`
- Accent hover: `#E5BC32`
- Accent ink: `#201B0C`

Dark tema:

- Canvas: `#11100F`
- Main surface: `#181715`
- Raised surface: `#211F1C`
- Soft surface: `#292622`
- Primary text: `#F5F2ED`
- Secondary text: `#C0BBB3`
- Muted text: `#8B857C`
- Border: `#35312C`
- Strong border: `#4A453E`
- Brand accent: `#F2CF55`
- Accent hover: `#FFDC67`
- Accent ink: `#201B0C`

Değerler erişilebilir kontrast için ayarlanabilir ancak yeşile dönülmeyecek.

### 4.3 Operasyon renkleri

Marka rengi ile sistem durumu birbirinden ayrılacak:

- Critical: coral red
- Warning: amber/orange
- Healthy: cool blue
- Information: cobalt
- Stale: violet
- Unknown: neutral gray
- Not configured: warm gray
- Not applicable: slate

Önerilen başlangıç değerleri:

- Critical: `#E85D5D`
- Warning: `#D9922E`
- Healthy: `#3C82E0`
- Information: `#5C6FE8`
- Stale: `#9568D8`
- Unknown: `#7C8187`
- Not configured: `#948D84`
- Not applicable: `#A4A7AC`

Her durum ikon ve görünür metinle de ifade edilecek.

### 4.4 Logo

- Mevcut yeşil logo kullanılmayacak
- Obsidian sidebar üzerinde ivory/beyaz monokrom logo
- Açık yüzey üzerinde siyah/obsidian logo
- Solar yellow yalnızca küçük logo detayı olarak kullanılabilir
- Logo formu korunabilir; renk kimliği değiştirilecek

## 5. UX prensipleri

Yeni Drake aşağıdaki prensiplere göre çalışmalıdır:

### 5.1 Önce cevap, sonra kanıt

Her ekran şu sırayı izlemeli:

1. Kullanıcının bilmesi gereken ana cevap
2. Bu cevabı açıklayan görsel kanıt
3. Yapılabilecek ana aksiyon
4. Ayrıntılı teknik kayıtlar

Tablo veya ham kayıt ekranın ilk ve baskın içeriği olmamalı.

### 5.2 Progressive disclosure

Her ayrıntı ilk anda gösterilmeyecek.

- Ana yüzeyde özet
- Hover/focus ile kısa açıklama
- Drawer veya disclosure ile ara detay
- Detail route ile tam kayıt

Kullanıcıyı aynı anda bütün teknik alanlarla karşı karşıya bırakma.

### 5.3 Bağlam kaybı olmayacak

Kullanıcı bir matrix hücresinden, incident kaydından veya search sonucundan detaya gittiğinde:

- Geldiği scope anlaşılmalı
- Breadcrumb doğru olmalı
- Browser Back filtreyi geri getirmeli
- Time range korunmalı
- Seçili project/environment bağlamı kaybolmamalı
- Kullanıcı tekrar ana listeden arama yapmak zorunda kalmamalı

### 5.4 Dead-end sayfa olmayacak

Her detay sayfasında en az bir anlamlı sonraki adım bulunmalı:

- Related project
- Related environment
- Related service
- Related incident
- Related alert
- Related deployment
- Related cluster
- Related inventory evidence

Mevcut API’nin sunduğu ilişkiler kullanılacak. Yeni backend ilişkisi uydurulmayacak.

## 6. Bilgi mimarisi ve navigation

Mevcut sidebar grupları kullanıcı açısından daha anlaşılır hâle getirilecek.

Önerilen ana yapı:

### Overview

- Command Center

### Estate

- Projects
- Service Health
- Clusters
- Inventory veya cluster üzerinden inventory erişimi
- Objectives

### Operations

- Incidents
- Alerts
- Deployments
- Protection

### Configuration

- Onboard Project
- Integrations
- Notification Routing
- Audit & Access

Kurallar:

- Benzer işler aynı grupta bulunmalı
- Kullanıcı “Service Health neden Observe altında?” gibi yapı mantığı çözmek zorunda kalmamalı
- Sidebar section isimleri kısa ve anlaşılır olmalı
- Aktif route ve parent route aynı anda anlaşılmalı
- Mobil drawer aynı bilgi mimarisini korumalı
- Navigation item sayısı artmayacak
- Backend permission sınırları korunacak
- Kullanıcının yetkisi olmayan bölüm görünmeyecek

## 7. Global Search ve Command Palette

Aranan kayıtları derin sayfalardan çıkarmak için global search ana navigasyon yöntemi hâline getirilecek.

Mevcut `⌘K / Ctrl+K` Command Palette korunacak ancak görünür ve güçlü bir entity finder’a dönüştürülecek.

### 7.1 Aranabilecek varlıklar

Mevcut authorized search ve mevcut route verileri kullanılarak sonuçlar gruplandırılacak:

- Pages
- Projects
- Environments
- Services
- Clusters
- Incidents
- Alerts
- Deployments
- Inventory resources, mevcut authorized kaynakta bulunabiliyorsa

Yeni search backend’i yazılmayacak.

### 7.2 Search sonucu tasarımı

Her sonuçta mümkün olduğu ölçüde:

- Entity type ikonu
- Ana isim
- Parent context
- Health/status
- Project/environment
- Son görülme zamanı
- Doğrudan route

Örnek:

`core-api`  
`Service · alpha / dev · Critical`

`HighErrorRate`  
`Incident · core-api · P1 · Open`

`cluster-a`  
`Cluster · Inventory stale`

### 7.3 Search davranışı

- Search input topbar’da sürekli görünür
- Placeholder yalnızca “Search Drake” olmayacak; “Search projects, services, incidents…” gibi açıklayıcı olacak
- `⌘K / Ctrl+K` her sayfadan açacak
- Yazmaya başlayınca sonuçlar entity türüne göre gruplanacak
- Klavye oklarıyla gezinilecek
- Enter ile açılacak
- Escape ile kapanacak
- Son ziyaret edilen kayıtlar sorgu yokken gösterilecek
- Sık kullanılan ana sayfalar gösterilecek
- Yetkisiz kayıtların varlığı belli edilmeyecek
- Sonuç bulunamazsa alternatif ana route önerilecek
- Search sonucu yeni sekme veya gereksiz ara sayfa açmayacak

### 7.4 Bulunabilirlik hedefi

Aşağıdaki kayıtlar global search üzerinden en fazla iki etkileşimle açılabilmeli:

- Project
- Environment
- Service
- Cluster
- Incident
- Alert
- Deployment

Akış:

1. Search aç
2. Sonucu seç

## 8. Breadcrumb ve scope navigator

Detail sayfalarında yalnızca teknik breadcrumb yeterli değildir.

Örnek:

`Projects / Alpha / dev / core-api`

Kurallar:

- Her breadcrumb segmenti tıklanabilir
- UUID gösterilmez
- Project ve environment adı görünür
- Breadcrumb mobilde kontrollü biçimde kısalır
- Son entity adı görünür kalır
- Breadcrumb Back butonunun yerine geçmez ancak bağlamı açıklar

Project, environment ve service detaylarında küçük bir scope navigator kullanılabilir:

- Current project
- Current environment
- Current service
- Sibling değiştirme

Bu selector mevcut authorized collection verileriyle yapılabiliyorsa kullanılacak. Yeni backend endpoint’i eklenmeyecek.

Amaç, kullanıcının sibling service’e geçmek için tekrar Projects ana sayfasına dönmesini engellemektir.

## 9. Contextual navigation ve ilişkili kayıtlar

Her kritik yüzey kullanıcıyı bir sonraki kanıta taşımalıdır.

### Incident Detail

Görünür ilişkiler:

- Affected service
- Project/environment
- Related alert
- Nearby deployment
- Relevant timeline
- Open service health

### Alert Detail

Görünür ilişkiler:

- Related incident
- Affected service
- Project/environment
- Source integration
- Timeline context

### Service Detail

Görünür ilişkiler:

- Project
- Environment
- Binding/workload
- Cluster
- Open incidents
- Active alerts
- Recent deployments

### Cluster Detail

Görünür ilişkiler:

- Inventory
- Problematic resources
- Bound projects/environments, mevcut veri izin veriyorsa
- Agent state
- Inventory freshness

### Inventory Resource Detail

Görünür ilişkiler:

- Parent cluster
- Namespace
- Workload
- Related service binding, mevcut veride bulunuyorsa
- Aynı kind/filter listesine geri dönüş

İlişkiler sayfanın en altında gömülmeyecek. Ana evidence veya action alanına yakın olacak.

## 10. Filtreleme ve liste kullanılabilirliği

### 10.1 URL-backed state

Mevcut URL-backed filtre yaklaşımı korunacak ve bütün önemli listelerde tutarlı hâle getirilecek.

URL’de korunması gerekenler:

- Search
- Status
- Project
- Environment
- Cluster
- Criticality
- Lifecycle
- Time range
- Sort
- Inventory kind

Browser Back kullanıldığında kullanıcı aynı filtrelenmiş görünüme dönmeli.

### 10.2 Filter bar

Yeni filter bar:

- Search solda
- En önemli iki veya üç filtre doğrudan görünür
- İkincil filtreler `More filters` içinde
- Aktif filtreler removable chip olarak görünür
- `Clear all` tek aksiyon
- Sonuç sayısı filter bar içinde
- Filtre uygulamak için ayrıca gereksiz Apply butonu kullanılmaz
- Mobilde filtreler drawer veya bottom sheet içinde açılır
- Kullanıcı hangi filtrelerin aktif olduğunu drawer kapalıyken de görür

### 10.3 Listeler

- Satırın ana hedefi belirgin
- Satırın tamamı veya geniş bir bölümü tıklanabilir
- Primary action yalnızca küçük metin linki değildir
- Critical kayıtlar soldaki status rail ve görünür metinle ayrılır
- Teknik UUID ana listede gösterilmez
- Secondary metadata optik olarak geri çekilir
- Mobilde önemli listeler responsive row/card düzenine geçer
- Yatay kaydırma yalnızca gerçekten yoğun inventory tablolarında son seçenek olabilir

## 11. Hızlı erişim sistemi

Command Center ve entity detail sayfalarında contextual quick actions kullanılacak.

Örnek quick actions:

- Investigate incident
- Open service health
- View project
- View environment
- Open cluster inventory
- Show related deployments
- Clear filters
- Refresh evidence

Kurallar:

- Her panelde çok sayıda action bulunmayacak
- Bir primary, gerekirse bir secondary action
- Action isimleri teknik route isimleri değil kullanıcı amacı olmalı
- “View details” gibi bağlamsız metinler azaltılmalı
- Action kullanıcıyı boş veya yetkisiz route’a göndermemeli

## 12. Yeni global visual system

### 12.1 Tipografi

Yeni font dependency ekleme.

- Hero verdict: 42–48 px
- Ana KPI: 30–38 px
- Sayfa başlığı: 28–32 px
- Section title: 19–22 px
- Panel title: 15–17 px
- Body: 13–14 px
- Caption: 11–12 px
- Micro metadata: minimum 10–11 px
- Teknik değerler: monospace

Ana değerler mevcut arayüzden belirgin biçimde büyük olmalı.

### 12.2 Spacing

- Page padding: 28–32 px desktop
- Tablet: 20–24 px
- Mobile: 14–16 px
- Major section gap: 24–28 px
- Panel gap: 16–20 px
- Hero padding: 28–32 px
- Primary panel padding: 20–24 px
- Utility surface padding: 14–18 px

### 12.3 Radius

- Shell/hero: 22–26 px
- Primary panel: 16–20 px
- Nested surface: 12–14 px
- Button/input: 10–12 px
- Badge: pill veya 8–10 px

### 12.4 Panel varyantları

En az dört seviye:

1. Hero
2. Primary visualization
3. Secondary insight
4. Utility/detail

Her panel aynı border, radius, shadow ve padding’e sahip olmayacak.

## 13. Global application shell

### Sidebar

- Obsidian yüzey
- 232–248 px
- Monokrom logo
- Yeni bilgi mimarisi
- Aktif öğede charcoal yüzey + solar indicator
- Yeşil aktif rail yok
- Navigation satırları 42–46 px
- Utility/scope alanı altta kompakt
- Permission’a göre görünürlük korunur

### Topbar

- 60–64 px
- Global entity search baskın utility
- Time range yalnızca ilgili bağlamlarda
- Notification/theme/account tek group
- Her ikon ayrı kart gibi görünmez
- 768 px taşması yok
- Mobilde hamburger + search + notification + avatar

### Main canvas

- Bone/stone canvas
- Geniş ekranı verimli kullanır
- Amaçlı boşluk
- Güçlü asimetrik grid
- Uzun kart listesi görünümü yok

## 14. Command Center — amiral ekran

### 14.1 Üst başlık

- Command Center
- Kısa açıklama
- Freshness
- Time range
- Refresh

### 14.2 Operational Pulse hero

- 12 kolonun yaklaşık 8’i
- 240–280 px
- Obsidian yüzey
- Büyük critical count
- Etkilenen project/scope
- Incident ve alert sayısı
- En kritik olay
- Primary investigation action
- Coral red yalnızca durum vurgusunda
- Solar küçük marka/interaction vurgusunda

### 14.3 Sources & estate summary

Hero yanında:

- Sources reporting
- Büyük `5/5`
- Coverage ring/segmented indicator
- Oldest source
- Eksik kaynak
- Project/environment/cluster/service sayıları

Coverage health score gibi gösterilmez.

### 14.4 Correlation Timeline

- Ana görselleştirme
- Gerçek zaman ekseni
- Incident/alert/deployment lane’leri
- Tıklanabilir event
- Hover/focus tooltip
- İlişkili zaman vurgusu
- Detail drill-down
- Progress bar görünümü yok

### 14.5 Attention Queue

- Critical-first
- Entity + problem + scope + age
- Tıklanabilir satır
- Primary incident action
- Kontrollü yükseklik
- Unknown kayıtlar geri planda

### 14.6 Health Matrix

- Gerçek heatmap tile’ları
- Project × environment
- Status + service count
- Hover ile worst-service özeti
- Hücreden doğrudan drill-down
- Mobilde disclosure/list

### 14.7 Risk stack

- Capacity risk
- Certificate/PVC
- Integration distribution
- Service-health distribution
- Segmented bar, horizontal bar veya tek ring
- Çok sayıda donut yok

### 14.8 Estate overview

Standing State dört büyük panel olmayacak.

Tek kompakt yüzey:

- Cluster fleet
- Integrations
- Catalog
- Service health

Her segment ilgili sayfaya gider.

## 15. Projects ve Project Detail

### Projects

- Portfolio Risk gerçek matrix/heatmap
- Görselden table filtresine geçiş
- URL state korunur
- Project satırından detail’e doğrudan geçiş
- Mobilde project/health/criticality aynı satır grubunda
- Kırpılmış badge yok
- Yatay scroll yok

### Project Detail

- Project verdict
- Criticality ve health ayrı
- Environment/service topology
- Sibling environment erişimi
- Service detail’e doğrudan link
- Service-health görünümü
- Cluster/workload ilişkisi
- Signals öncelikli
- Standing state geri planda

Kullanıcı başka bir servise geçmek için Projects ana sayfasına dönmek zorunda kalmamalı.

## 16. Mobil UX

Mobil sıra:

1. Header ve search
2. Operational Pulse
3. Primary action
4. Attention Queue
5. Timeline summary
6. Health Matrix
7. Risk
8. Estate overview

Mobil navigation:

- Drawer açıldığında global search üstte
- Son ziyaret edilen entity’ler
- Navigation grupları
- Aktif context
- Account/theme utility

Mobil listeler:

- Ana isim
- Status
- Parent context
- Age veya freshness
- Sağ yön/action

Yatay tablo ve kırpılmış badge kabul edilmez.

## 17. Bulunabilirlik kabul senaryoları

Aşağıdaki akışlar elle doğrulanmalı:

### Senaryo 1 — Servis bulma

Kullanıcı herhangi bir sayfadayken `core-api` arar ve en fazla iki etkileşimle Service Detail’i açar.

### Senaryo 2 — Kritik olaydan kanıta

Command Center’daki critical item’dan en fazla iki tıklamayla ilgili service veya incident evidence ekranına ulaşır.

### Senaryo 3 — Project içinde gezinme

Project Detail’den bir environment ve onun service detail’ine geri ana listeye dönmeden ulaşır.

### Senaryo 4 — Cluster riski

Cluster riskinden ilgili inventory listesi veya resource evidence ekranına en fazla iki tıklamayla ulaşır.

### Senaryo 5 — Filtreyi kaybetmeme

Projects veya Service Health listesindeki filtreli görünümden detaya gider; Browser Back ile aynı filtre, sort ve search’e döner.

### Senaryo 6 — Mobil arama

390 px ekranda navigation drawer veya global search üzerinden proje, servis ve incident sonuçlarına erişir.

### Senaryo 7 — Dead-end kontrolü

Project, environment, service, incident, alert, cluster ve inventory detail sayfalarının her birinde anlamlı bir sonraki aksiyon bulunur.

## 18. Uygulama kapsamı

Öncelikli frontend alanları:

### Global

- `apps/web/src/app/globals.css`
- Design tokens
- Status palette
- Tema değerleri

### Shell

- AppShell
- Sidebar
- TopBar
- Brand
- PageFrame
- PageHeader
- Breadcrumb
- Command Palette

### Shared UI

- Panel
- SectionHeader
- StatusBadge
- DataTable
- FilterBar
- SearchInput
- Select
- Button
- Modal
- Drawer
- Loading/empty/error states

### Command Center

- Ana page composition
- Verdict/Operational Pulse
- Attention Queue
- Evidence Coverage
- Timeline
- Health Matrix
- Capacity Risk
- Estate summaries

### Hızlı özel düzeltmeler

- Projects
- Project Detail
- Global search results
- Breadcrumb/context navigation

## 19. Yasak kapsam

- Backend değişikliği
- API değişikliği
- Migration
- Contract değişikliği
- Authorization değişikliği
- Yeni aggregation
- Sahte veri
- Yapay health score
- Yeni dependency
- Büyük state/pagination refactor’ı
- Yeni test altyapısı
- Yeni E2E dosyaları
- Dokümantasyon çalışması
- Production/sslip.io deploy

## 20. Çalışma sırası

### Aşama 1 — Kimlik, shell ve global search

- Yeşili kaldır
- Yeni palette geç
- Logoyu monokromlaştır
- Sidebar/topbar/canvas
- Navigation grupları
- Global search görünümü
- Search sonuç grupları
- Breadcrumb sistemi
- Shared typography/panels/controls

### Aşama 2 — Command Center

- Operational Pulse
- Coverage/estate
- Timeline
- Attention
- Health Matrix
- Risk stack
- Estate overview

### Aşama 3 — Findability

- Entity result presentation
- Contextual links
- Related entity actions
- Filter/back preservation
- Dead-end sayfa aksiyonları
- Project/environment/service geçişleri

### Aşama 4 — Responsive ve dark

- 1024/768
- 390
- Navigation drawer
- Mobil search
- Badge/table taşmaları
- Dark surface hierarchy

### Aşama 5 — Projects ve Project Detail

- Portfolio heatmap
- Mobile project list
- Project verdict
- Topology lanes
- Scope navigation

### Aşama 6 — Minimum doğrulama

Yalnızca:

- Typecheck
- Production build
- Command Center smoke
- Search → service akışı
- Critical item → evidence akışı
- Projects filter → detail → Back akışı
- Cluster → inventory akışı
- 1920/390 light-dark kontrolü

Full test paketleri çalıştırılmayacak.

## 21. Final teslim

- Command Center before/after 1920 light
- Command Center 1920 dark
- Command Center 390 light/dark
- Global search açık görünüm
- Search result → Service Detail akışı
- Projects 390
- Project Detail 1280
- Typecheck
- Production build
- Local preview adresi
- Commit listesi
- Deploy edilmedi bilgisi

## 22. Kesin ret kriterleri

Teslim reddedilir:

- Herhangi bir ana yüzeyde marka yeşili varsa
- Logo veya navigation yeşilse
- Değişim yalnızca renk/radius seviyesindeyse
- Command Center eşit kart listesiyse
- Timeline progress bar gibiyse
- Health Matrix tablo gibiyse
- Kullanıcı aradığı entity için sidebar’da sayfa sayfa dolaşıyorsa
- Service, incident veya cluster global search ile bulunamıyorsa
- Detay sayfaları dead-end ise
- Browser Back filtreleri kaybediyorsa
- İlişkili kayıtlar detayların altında gömülüyse
- Mobilde arama zor, badge kırpılmış veya tablo yatay kayıyorsa
- Teknik UUID’ler ana navigation öğesi gibi gösteriliyorsa
- Hazır admin template veya Grafana hissi varsa
- Yeni backend veya gereksiz test çalışması yapılmışsa

Nihai başarı ölçüsü:

> Drake’in mevcut operasyon verileri PayFlow kalitesinde premium bir görsel sistem içinde sunulmalı; kullanıcı aradığı herhangi bir ana kaydı global search veya bağlamsal drill-down üzerinden en fazla iki anlamlı etkileşimle bulabilmelidir.