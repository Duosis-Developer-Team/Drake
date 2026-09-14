# Drake Visual/UI-UX Remake — Ürün ve Tasarım Briefi

**Tarih:** 2026-09-13

**Durum:** İnceleme için tasarım briefi

**Karar sahibi:** CTO

**Uygulama sahibi:** Senior Developer (Sonnet 5)

**Kapsam:** Drake web uygulamasının bilgi mimarisi, görsel sistemi, veri
görselleştirmesi ve kullanıcı akışları

**Kapsam dışı:** Backend yeniden yazımı, veri semantiğinin değiştirilmesi,
Drake'in güvenlik sınırlarının gevşetilmesi

## 1. Yönetici özeti

Drake güçlü bir observability ve operations control plane'dir. Ürünün temel
sorunu veri eksikliği veya backend yetersizliği değildir. Sorun, mevcut
frontend'in bu veriyi kullanıcı adına yeterince ilişkilendirmemesi,
önceliklendirmemesi ve görsel bir karar modeline dönüştürmemesidir.

Remake'in amacı Drake'i daha süslü göstermek değildir. Amaç, nöbetteki bir
platform operasyon mühendisinin aşağıdaki soruları birkaç saniye içinde
cevaplayabilmesini sağlamaktır:

1. Şu anda ne bozuk veya risk altında?
2. Etkilenen proje, ortam, servis ve cluster hangileri?
3. Sorun ne zaman başladı ve hemen öncesinde ne değişti?
4. Drake bu yargıya hangi kanıtlarla ulaştı?
5. Kullanıcının bir sonraki güvenli eylemi nedir?

Yeni deneyim üç kaynağı birleştirecektir:

- PayFlow referansının mekânsal hiyerarşisi ve sakin kompozisyonu,
- Datalake Platform GUI'nin analitik derinliği ve drill-down davranışı,
- Drake'in veri dürüstlüğü, erişilebilirliği ve operasyon güvenliği.

Bu üç kaynaktan hiçbiri birebir kopyalanmayacaktır. Sonuç Drake'in kendi
ürün karakterine sahip olmalıdır.

## 2. Kanıt ve mevcut durum

İnceleme aşağıdaki kaynaklar üzerinden yapılmıştır:

- 30 Next.js route'u ve yaklaşık 30.600 satır TypeScript/TSX,
- mevcut App Shell, navigasyon, global arama ve tema sistemi,
- ECharts adaptörü ve ortak chart bileşenleri,
- Command Center ve kritik detay sayfaları,
- Sprint 13 UI audit ve 27 mimari karar kaydı,
- CI, unit, E2E, axe ve responsive test politikası,
- 2026-08-06 ile 2026-08-15 arasındaki 296 commit,
- Datalake Platform GUI'nin güncel kaynakları ve canlı ekran kanıtları,
- PayFlow Dashboard UI referansının 3200×2400 görseli.

### 2.1 Korunacak güçlü yanlar

- Browser yalnızca Drake API ile konuşur.
- `unknown`, `stale`, `partial`, `not_configured`, `permission denied` ve
  gerçek `zero` birbirine dönüştürülmez.
- Null örnek grafiklerde sıfır gibi çizilmez.
- Cluster agent salt okunurdur ve güvenlik sınırları UI uğruna gevşetilmez.
- Erişilebilir chart tablosu, tooltip, legend ve reduced-motion desteği vardır.
- Sidebar, mobil drawer, tema ve klavye navigasyonu test altındadır.
- Filtrelerin URL'de tutulduğu yerlerde görünüm paylaşılabilir ve geri tuşu
  anlamlıdır.
- ECharts tembel yüklenir; canvas renkleri semantic token mirror'ı üzerinden
  doğru çözülür.

### 2.2 Çözülecek ana problemler

- Ana ekran bir karar yüzeyinden çok eş ağırlıklı kart/panel koleksiyonudur.
- Tipografi ve grafik açıklamaları gereğinden fazla küçüktür.
- Marka yeşili; zemin, seçim, vurgu ve sağlıklı durum arasında fazla rol
  taşımaktadır.
- 30 route'un çoğu tablo, rozet ve metinsel panel ağırlıklıdır.
- Incident, alert, deployment, SLO ve service health verileri aynı zaman
  ekseninde ilişkilendirilmemektedir.
- Global arama yalnızca catalog entity'lerini bulur; operasyon nesnelerini ve
  eylemleri bulmaz.
- Kullanıcı sistemin bilgi modelini bilmeden doğru sayfaya ulaşmakta zorlanır.
- Birincil, ikincil ve destekleyici bilgi çoğu ekranda benzer yüzey ağırlığına
  sahiptir.
- Donut/ring kullanımı bazı yerlerde karşılaştırmayı kolaylaştırmak yerine alan
  tüketmektedir.
- Büyük tablolar doğru veri taşır fakat “önce hangi satıra bakmalıyım?” sorusunu
  yeterince cevaplamaz.

## 3. Ürün konumlandırması ve kullanıcı hiyerarşisi

### 3.1 Birincil kullanıcı

**Nöbetteki Platform Operations / SRE operatörü.**

Bu kullanıcı yalnızca alarm kapatmaz. Proje, servis, deployment, cluster,
entegrasyon ve koruma verilerini birlikte değerlendirir. Ana ekran ve ana
navigasyon bu kullanıcının zaman baskısı altındaki kararlarına göre optimize
edilecektir.

### 3.2 İkincil kullanıcı

**Platform sahibi.**

Coverage, kapasite, veri tazeliği, entegrasyon durumu, SLO riski ve biriken
operasyon borcunu takip eder. Aynı tasarım sistemi içinde ayrı bir Risk &
Coverage görünümü kullanır.

### 3.3 Üçüncül kullanıcı

**Yönetici veya kapasite karar vericisi.**

Trend, risk, kapasite, güvenilirlik ve yatırım ihtiyacını özetleyen ayrı bir
Executive/Capacity görünümüne sahiptir. Bu ihtiyaç Command Center'a gereksiz
KPI kartları eklemek için kullanılmayacaktır.

## 4. Başarı tanımı

Remake başarılı sayılmak için aşağıdaki sonuçları üretmelidir:

- Kullanıcı Command Center'da ilk 10 saniye içinde en kritik problemi ve
  etkilenen kapsamı belirleyebilmelidir.
- Kritik bir öğeden kanıt sayfasına tek tıklamayla geçilebilmelidir.
- Alert → incident → deployment → service/cluster ilişkisi aynı bağlam içinde
  görülebilmelidir.
- Her önemli ekran tek bir ana soruya cevap vermelidir.
- Kullanıcı aradığı nesnenin menü kategorisini bilmeden global palette
  üzerinden ulaşabilmelidir.
- Sağlıklı görünen hiçbir sayı eksik, yetkisiz veya bayat veriden
  türetilmemelidir.
- Her grafik, grafik olmadan da erişilebilir bir veri ve metin karşılığına
  sahip olmalıdır.
- 390 px'den geniş masaüstlerine kadar yatay sayfa taşması olmamalıdır.
- Light ve dark tema aynı bilgi hiyerarşisini korumalıdır.
- Yeniden tasarım mevcut API ve güvenlik mimarisini zorunlu olmadıkça
  değiştirmemelidir.

## 5. Yaklaşım kararı

### Seçenek A — Bütün route'ları tek seferde yeniden tasarlamak

Görsel tutarlılık hızlı oluşur ancak regresyon alanı çok büyür. Kullanıcı
akışları doğrulanmadan 30 route'a yanlış bir düzen yayma riski vardır.

### Seçenek B — Sistem + dikey dilimler (önerilen)

Önce tasarım tokenları, shell, command palette ve görselleştirme grameri
kurulur. Ardından Command Center ile birinci dikey dilim tamamlanır. Aynı
kurallar sırasıyla catalog, cluster, reliability ve yönetim yüzeylerine
yayılır.

Avantajları:

- Her dalga gerçek veriyle ve screenshot QA ile doğrulanır.
- Mevcut çalışan route'lar topluca riske atılmaz.
- Yanlış tasarım kararları 30 sayfaya yayılmadan düzeltilebilir.
- Sonnet 5'e küçük, sınırları net çalışma paketleri verilebilir.

### Seçenek C — Yalnızca Command Center'ı yenilemek

Hızlı görünür sonuç üretir fakat kullanıcı ana ekrandan mevcut detay
sayfalarına geçtiğinde deneyim kopar. Navigasyon ve görselleştirme borcu
çözülmez.

**Karar:** Seçenek B uygulanacaktır.

## 6. Tasarım yönü

### 6.1 Tasarım fikri: Operational Canvas

Drake bir finans uygulaması gibi steril, bir NOC ekranı gibi yorucu veya bir
SaaS template'i gibi kartlardan oluşan bir arayüz olmayacaktır. Görsel fikir,
operasyonel kanıtların sakin bir tuval üzerinde birbiriyle ilişkilendirildiği
**Operational Canvas** olacaktır.

Bir ekran üç seviyeden oluşur:

1. **Verdict:** Kullanıcının önce bilmesi gereken sonuç.
2. **Evidence:** Sonucun dayandığı ölçüm, olay ve ilişkiler.
3. **Action:** Bir sonraki güvenli hareket veya drill-down.

Bir sayfada bu üç seviyeyi desteklemeyen dekoratif yüzey oluşturulmayacaktır.

### 6.2 PayFlow'dan alınacaklar

- Koyu ve sakin sidebar ile açık çalışma alanı arasındaki net ayrım,
- büyük merkez yüzey + dar bağlamsal yan panel kompozisyonu,
- kontrollü renk ve radius kullanımı,
- tablo, özet ve mini grafik arasında ritim,
- bakışı yönlendiren asimetrik grid.

### 6.3 PayFlow'dan alınmayacaklar

- düşük bilgi yoğunluklu fintech boşlukları,
- operasyonel durumların pastel dekorasyona dönüşmesi,
- her değerin bağımsız karta konması,
- semantik durumların yalnızca renkli noktayla anlatılması.

### 6.4 Datalake'ten alınacaklar

- breakdown-first veri anlatımı,
- parametre değişikliğinin sonucu anında güncellemesi,
- görsel özetten tablo detayına drill-down,
- kapasite ve senaryo karşılaştırmaları,
- topology ve data-quality gibi ilişkisel görseller.

### 6.5 Datalake'ten alınmayacaklar

- binlerce satırlık sayfa/component dosyaları,
- kod içine dağılmış renk ve stil sabitleri,
- bütün yüzeylerde aynı büyük radius ve gölge,
- aşırı sekme, rozet ve nested navigation,
- hover sırasında kartların sürekli yükselmesi,
- görsel zenginlik uğruna bilgi yoğunluğunun kontrolsüz artması.

## 7. Görsel sistem

### 7.1 Renk sistemi

Renkler dekoratif değil semantik kullanılacaktır. Marka rengi ile durum
renkleri ayrı sorumluluklara sahip olacaktır.

#### Light tema çekirdeği

| Token | Değer | Kullanım |
|---|---:|---|
| `obsidian` | `#0B1511` | Sidebar ve güçlü kontrast yüzey |
| `canvas` | `#F2F5F2` | Uygulama çalışma zemini |
| `paper` | `#FFFFFF` | Ana içerik yüzeyi |
| `ink` | `#11231B` | Birincil metin |
| `forest` | `#0A5B3D` | Marka, seçim ve ana eylem |
| `serpent` | `#18B566` | Sınırlı marka vurgusu |

#### Durum renkleri

| Durum | Değer | Kural |
|---|---:|---|
| Success | `#078A66` | Yalnız doğrulanmış sağlıklı durum |
| Critical | `#D14343` | Kritik hata ve destructive durum |
| Warning | `#B97809` | Risk, yaklaşan eşik veya degraded |
| Information | `#2E6FD8` | Bilgi, değişiklik ve deployment |
| Stale | `#8A6A18` | Bayat veri; warning'den ayrılır |
| Unknown | `#667085` | Ölçülmemiş veya bilinmeyen |

`forest` hiçbir zaman otomatik olarak “healthy” anlamına gelmeyecektir.

#### Dark tema yönü

- Canvas: `#07100C`
- Surface 1: `#0D1A14`
- Surface 2: `#14241C`
- Border: `#294034`
- Primary text: `#EEF5F1`
- Brand action: `#35D07F`

Dark tema yalnız light değerlerin ters çevrilmesi olmayacaktır. Kritik,
warning, stale ve unknown tonlarının birbirinden ayrımı ayrıca test
edilecektir.

### 7.2 Tipografi

Önerilen aile:

- **IBM Plex Sans:** arayüz ve veri metni,
- **IBM Plex Mono:** digest, namespace, revision, identifier ve kod benzeri
  değerler.

Gerekçe: Bu ürün finans veya pazarlama SaaS'ı değil; altyapı ve operasyon
ürünüdür. Plex ailesi yüksek yoğunlukta okunabilirlik ve teknik karakter sağlar.
Fontlar uygulama ile birlikte self-host edilmelidir.

Önerilen ölçek:

| Rol | Boyut |
|---|---:|
| Command headline | 28–32 px |
| Page title | 22–24 px |
| Section title | 16–18 px |
| Body | 14–15 px |
| Caption | 12–13 px |
| Micro metadata | minimum 12 px |
| Primary metric | 30–40 px, bağlama göre |

11 px metin yalnızca grafik ekseni gibi ikincil ve tekrar eden alanlarda
kullanılabilir. Kullanıcının karar vermesi için gereken bilgi 11 px olamaz.

### 7.3 Radius, sınır ve gölge

- Controls: 8 px
- Standard panels: 12 px
- Dominant canvas: 16–18 px
- Drawers/dialogs: 16 px
- Status pills: tam yuvarlak

Her yüzey aynı radius'u kullanmayacaktır. Birincil hiyerarşi radius farkıyla
da okunacaktır. Standard panel hafif border kullanır; gölge yalnız overlay,
drawer ve gerçekten yükselmiş yüzeylerde bulunur.

### 7.4 Spacing ve grid

- 4 px temel ölçü, 8 px ana ritim,
- expanded sidebar: 248 px,
- collapsed sidebar: 72 px,
- sayfa içi standart boşluk: 24 px,
- geniş ekran içerik sınırı: yaklaşık 1920 px,
- ana dashboard: 12 kolon,
- birincil içerik çoğunlukla 8–9 kolon, bağlamsal rail 3–4 kolon.

İçerik varsayılan olarak sola hizalanacaktır. Merkezi hizalama yalnız empty
state veya tek karar anlarında kullanılacaktır.

### 7.5 Motion

- Otomatik ve sürekli hareket kullanılmayacaktır.
- Sayfa yüklenince her kart ayrı ayrı fade/slide yapmayacaktır.
- Motion yalnız değişen durumu açıklamalıdır: drawer açılması, satırın
  genişlemesi, zaman aralığı değişimi, cross-filter ve başarı onayı.
- Reduced-motion tercihi tüm hareketleri devre dışı bırakacaktır.

## 8. Yeni bilgi mimarisi

Ana navigasyon kullanıcı görevlerine göre sadeleştirilecektir:

### Command

- Command Center

### Inventory

- Projects
- Services
- Clusters

### Reliability

- Objectives
- Protection

### Response

- Incidents
- Alerts
- Deployments

### Platform

- Integrations
- Onboarding
- Notification routing
- Access & audit

Navigasyon permission-aware kalacaktır. Boş grup gösterilmeyecektir. Grup
başlıkları doküman heading hiyerarşisini bozmayacaktır.

### 8.1 Global command palette

Mevcut catalog search genişletilecektir. Palette aşağıdakileri arayacaktır:

- project, environment, service, cluster,
- incident, alert, deployment, SLO ve protection policy,
- sayfalar ve izin verilen ana eylemler,
- son ziyaret edilen öğeler,
- kullanıcının erişebildiği scope'lar.

Sonuçlar türlerine göre gruplanacak, durum ve scope bağlamı taşıyacak ve
klavyeyle tamamen kullanılabilecektir. Search sonucu kullanıcıya yetkisiz bir
entity'nin varlığını sızdırmayacaktır.

Palette iki aşamada genişletilir. İlk aşama statik sayfa/eylem araması ve
mevcut yetkili catalog search'ü birleştirir. Incident, alert, deployment, SLO
ve protection entity araması ancak ilgili API'lerde yetki güvenli search
sözleşmesi doğrulandıktan sonra açılır; frontend bu verileri başka listeleri
toplayıp istemci tarafında gizlice indeksleyerek üretmez.

## 9. Command Center tasarımı

Command Center remake'in ilk gerçek dikey dilimidir ve bütün sistemin tasarım
kalitesini belirler.

### 9.1 Ana soru

**Şimdi neye müdahale etmeliyim ve neden?**

### 9.2 Masaüstü yerleşimi

```text
┌──────────────┬───────────────────────────────────────────────────────────┐
│              │ Scope / time / freshness                     Actions     │
│  Dark        ├───────────────────────────────────────────────────────────┤
│  navigation  │ Operational verdict                                     │
│              │ “2 critical paths need attention” + evidence coverage    │
│              ├───────────────────────────────────┬───────────────────────┤
│              │ Correlation timeline              │ Attention queue       │
│              │ alerts · incidents · deploys      │ ranked, actionable    │
│              │ service/cluster state changes     │                       │
│              ├───────────────────────────────────┼───────────────────────┤
│              │ Service & environment health map  │ Capacity risk         │
│              ├───────────────────────────────────┴───────────────────────┤
│              │ Coverage, freshness and unavailable evidence             │
└──────────────┴───────────────────────────────────────────────────────────┘
```

### 9.3 Bölümler

#### Operational verdict

- Tek güçlü cümle veya durum başlığı,
- kritik/warning toplamı,
- etkilenen service/project/cluster sayısı,
- kaç veri kaynağının cevap verdiği,
- en eski veya en şüpheli evidence freshness bilgisi.

Beş eş KPI kartı kullanılmayacaktır. Sayılar verdict'in içinde veya yanında
bağlamsal olarak yer alacaktır.

#### Correlation timeline

Aynı zaman ekseninde:

- incident açılma/kapanma,
- alert firing/resolved,
- deployment/revision,
- service health geçişi,
- cluster disconnect/reconcile,
- önemli Kubernetes warning event'leri.

Timeline yalnız gerçek ilişkilendirilebilir olayları bir araya getirir.
Korelasyon kanıt değilse “related in time” olarak sunulur; neden-sonuç iddiası
üretilmez.

İlk sürüm yalnız API'nin gerçekten tarihçe sağladığı olayları çizer. Service
veya cluster için geçmiş state transition verisi yoksa mevcut durumu geçmişte
de varmış gibi çoğaltmaz; o lane “history unavailable” olarak işaretlenir.

#### Attention queue

- Critical önce, sonra warning, sonra unknown/stale,
- aynı kök probleme bağlı öğeler mümkünse gruplanır,
- her satır subject, scope, yaş, durum ve ilk güvenli eylemi taşır,
- kullanıcı doğrudan evidence detail'e gider,
- boş state platformun sağlıklı olduğunu iddia etmez; hangi kaynakların
  kontrol edildiğini açıkça gösterir.

#### Service & environment health map

Project → environment → service hiyerarşisini küçük bir health matrix ile
gösterir. Uzun tabloda tek tek satır okumadan problem kümeleri fark edilir.

#### Capacity risk

Toplam kapasite göstermek yerine eşik yaklaşımı ve headroom riski gösterilir:

- eşik altında kalan filesystem/PVC/certificate,
- hızlı tüketilen kaynak,
- tahmini tükenme yalnız veri kalitesi yeterliyse,
- tahmin yapılamıyorsa açıkça “forecast unavailable”.

#### Evidence coverage

“Healthy” yargısının arkasındaki coverage görünür olmalıdır. Configured,
missing, unavailable, stale ve permission-denied kaynaklar ayrı gösterilir.

### 9.4 Responsive davranış

- 1024 px altında attention queue timeline'ın altına geçer.
- Health matrix yatay scroll yerine entity seviyesinde progressive disclosure
  kullanır.
- Mobilde verdict, attention queue ve timeline summary sırasıyla gösterilir.
- Ayrıntılı timeline mobilde ayrı tam ekran drill-down açabilir.

## 10. Sayfa bazlı görselleştirme matrisi

| Yüzey | Ana soru | Birincil görsel | Destekleyici görünüm |
|---|---|---|---|
| Projects | Hangi proje risk taşıyor? | Criticality × health matrisi | Sıralanabilir tablo |
| Project detail | Bu proje nasıl oluşuyor ve neresi etkileniyor? | Environment/service topology | Capability coverage ve trend |
| Environment | Bu ortamda hangi servis bozuldu? | Service health lanes | Workload ve signal özeti |
| Service detail | Servis neden sağlıksız? | Golden-signal small multiples | Deployment/incident annotations |
| Clusters | Hangi cluster görünmez veya riskli? | Connection × freshness matrix | Filtrelenebilir tablo |
| Cluster detail | Kapasite ve workload riski nerede? | Headroom board | Namespace/workload heatmap |
| Inventory | Problemli kaynaklar nasıl dağılıyor? | Ranked bars + health composition | Yoğun kaynak tablosu |
| Incidents | Hangi olay önce ele alınmalı? | Severity/age queue | Service ve scope dağılımı |
| Incident detail | Ne oldu ve nasıl gelişti? | Lifecycle + correlation timeline | Evidence ve audit trail |
| Alerts | Şu anda ne ateşliyor? | Severity × age distribution | Filtrelenebilir alert table |
| Alert detail | Bu sinyal neye bağlı? | Threshold context chart | Mapping ve transitions |
| Deployments | Hangi değişiklik risk üretti? | Outcome timeline | Environment/service breakdown |
| Deployment detail | Değişiklikten önce/sonra ne oldu? | Before/after signal overlay | Revision ve incident context |
| Objectives | Hangi SLO tükeniyor? | Error-budget burn ranking | Objective table |
| SLO detail | Bütçe ne hızla tükeniyor? | Burn-rate + exhaustion projection | Evaluation history |
| Protection | Nerede restore güveni zayıf? | Backup × restore-confidence matrix | Policy list |
| Protection detail | Kanıt gerçekten kurtarılabilirliği gösteriyor mu? | Run/drill timeline | Evidence detail |
| Integrations | Hangi veri kaynağı coverage'ı düşürüyor? | Provider coverage map | Operational list |
| Onboarding | Proje sisteme güvenli nasıl giriyor? | Step/progress model | Findings ve plan diff |
| Notifications | Ne gönderildi ve neden? | Delivery state flow | Table/audit detail |
| Access & audit | Kim neyi değiştirdi? | Filtered event stream | Roles/grants tables |

Admin ve konfigürasyon sayfalarına sırf görsel olsun diye grafik
eklenmeyecektir. Karşılaştırma için tablo daha iyi ise tablo korunur.

## 11. Görselleştirme grameri

Her veri tipi için varsayılan bir görsel cevap tanımlanacaktır:

- **Zaman içindeki değişim:** line/area chart veya event timeline,
- **az sayıdaki parçaların bütünü:** composition bar,
- **uzun kuyruklu kategori dağılımı:** sorted horizontal bars,
- **iki boyutlu durum yoğunluğu:** heatmap/matrix,
- **bağımlılık ve blast radius:** topology graph,
- **kullanılan/kalan kapasite:** linear capacity bar,
- **eşik ve hedef:** reference line/band,
- **önce/sonra karşılaştırması:** aligned overlay veya delta strip,
- **audit/lifecycle:** chronological event stream.

### 11.1 Yasaklar

- Beşten fazla dilim için pie/donut kullanılmaz.
- Gauge tek sayıyı büyük yer kaplayarak göstermek için kullanılmaz.
- Null veya unavailable değer sıfır çizilmez.
- Tahmin, confidence bilgisi olmadan kesin değer gibi sunulmaz.
- Renk tek durum taşıyıcısı olamaz.
- Grafik yalnız boşluk doldurmak için eklenmez.
- Aynı ekranda farklı bileşenler aynı metriği farklı formüllerle hesaplamaz.

### 11.2 Ortak chart sözleşmesi

Her chart aşağıdakileri desteklemelidir:

- başlık ve cevapladığı soru,
- birim,
- zaman aralığı ve as-of/freshness,
- loading, empty, denied, stale, partial ve error durumları,
- tooltip ve legend,
- klavye veya erişilebilir veri tablosu karşılığı,
- retry/correlation id gerektiğinde,
- theme tokenları,
- deterministic test modu.

## 12. Etkileşim modeli

### 12.1 Scope ve zaman

- Global scope yalnız onu gerçekten kullanan sayfalarda görünür.
- Zaman aralığı yalnız zaman tabanlı sorguları etkileyen route'larda gösterilir.
- Scope ve zaman URL'de tutulur; paylaşılabilir ve geri alınabilir olmalıdır.
- Bir kontrolün etkilediği paneller açıkça belirtilir.

### 12.2 Cross-filter

Grafikte seçilen service, cluster, severity veya zaman aralığı tabloyu ve
yan paneli filtreleyebilir. Aktif cross-filter görünür bir chip/summary olarak
gösterilir ve tek eylemle temizlenebilir.

### 12.3 Drill-down

- Hızlı kanıt için sağ drawer,
- bağlam ve tarihçe için tam detail route,
- kritik eylem veya karmaşık form için ayrı sayfa.

Drawer içinde drawer açılmayacaktır. Derin navigation breadcrumb ve back
davranışıyla çözülecektir.

### 12.4 Eylemler

Buton isimleri sonuç odaklı olacaktır: “Refresh evidence”, “Open incident”,
“View affected service”. Genel “Submit”, “Continue” ve “Manage” ifadeleri
bağlam vermeden kullanılmayacaktır.

## 13. Frontend mimarisi

Mevcut Next.js ve ECharts altyapısı korunacaktır. İlk aşamada yeni bir UI
framework, ikinci tablo motoru veya motion kütüphanesi eklenmeyecektir.

Önerilen sorumluluk sınırları:

```text
src/components/ui/             temel kontroller ve yüzeyler
src/components/shell/          shell, navigation, command palette
src/components/data-viz/       ortak operasyon görselleri
src/components/command-center/ Command Center composition
src/components/features/       route/domain odaklı bileşenler
src/lib/design/                semantic tokens ve formatters
src/lib/view-models/           API payload -> presentation model
```

### 13.1 Yeni ortak görseller

- `OperationalTimeline`
- `AttentionQueue`
- `HealthMatrix`
- `EvidenceCoverage`
- `CapacityRiskBoard`
- `TopologyGraph`
- `BeforeAfterSignals`
- `ErrorBudgetBurn`

Bu isimler plan aşamasında mevcut repo yapısıyla karşılaştırılarak kesin
dosya hedeflerine dönüştürülecektir.

### 13.2 View-model katmanı

API response'ları doğrudan devasa componentlerde birleştirilmeyecektir.
Frontend view-model fonksiyonları:

- deterministik,
- yan etkisiz,
- unit-test edilebilir,
- veri durumlarını koruyan,
- presentation için gereken sıralama ve gruplamayı yapan

küçük modüller olacaktır.

### 13.3 Backend sınırı

İlk tercih mevcut endpointleri frontend'de güvenli biçimde compose etmektir.
Yeni endpoint yalnız şu koşullarda önerilebilir:

- aynı verinin istemcide tekrar tekrar türetilmesi tutarsızlık yaratıyorsa,
- gerekli ilişki mevcut response'lardan kurulamayacaksa,
- istek sayısı veya payload boyutu ölçülmüş bir performans sorunu yaratıyorsa,
- authorization sınırı server-side projection gerektiriyorsa.

Yeni endpoint kararı ayrı bir backend scope'u ve ADR incelemesi gerektirir.
Remake backend yeniden yazımına dönüşmeyecektir.

## 14. State ve hata tasarımı

Her bileşen aşağıdaki durumları bilinçli biçimde ele almalıdır:

- initial loading,
- background refreshing,
- empty,
- filtered empty,
- not configured,
- permission denied,
- unknown,
- stale,
- partial,
- throttled,
- provider unavailable,
- not found,
- render/runtime error.

Kurallar:

- Loading son layoutu yaklaşık olarak önceden göstermelidir.
- Refresh mevcut last-good veriyi gereksizce silmemelidir.
- Empty state neden boş olduğunu ve sonraki adımı söylemelidir.
- Unknown/stale/denied hiçbir aggregate içinde sessizce sıfıra dönüşmemelidir.
- Error mesajı correlation id ve güvenli retry sağladığında göstermelidir.
- “All systems healthy” ancak gerekli coverage gerçekten mevcutsa yazılabilir.

## 15. Erişilebilirlik ve responsive kalite tabanı

- WCAG AA metin ve non-text contrast eşikleri korunur.
- Renk hiçbir zaman tek anlam taşıyıcısı değildir.
- Her route tek bir anlamlı `h1` içerir.
- Chartlar metinsel özet ve veri tablosu sağlar.
- Klavye focus sırası görsel sırayı takip eder.
- Command palette ve drawer focus trap uygular; focus tetikleyiciye döner.
- 390, 768, 1024, 1280, 1440 ve 1920 px görünüm test edilir.
- Mobilde masaüstü tablosunu küçültmek yerine bilgi önceliği yeniden düzenlenir.
- `prefers-reduced-motion` desteklenir.
- Kritik route'larda axe serious/critical ihlali sıfır kalır.

## 16. Performans kuralları

- ECharts shell bundle'a eager eklenmez; grafik görünümü gerektiğinde yüklenir.
- Tek timestamp başına timer oluşturulmaz; ortak clock korunur.
- Command Center sorguları mevcut concurrency bütçesini aşmaz.
- Cross-filter kullanıcıya 100 ms içinde görsel geri bildirim verir; uzak veri
  bekleniyorsa pending state gösterir.
- Büyük tablolar progressive rendering/pagination sınırlarını korur.
- Topology ve heatmap veri noktaları limitlenir; gizli veri DOM'da yığılmaz.
- Yeni bağımlılık ancak mevcut araçlarla çözülemeyen ölçülmüş ihtiyaç için eklenir.

## 17. Doğrulama stratejisi

### 17.1 Unit/component testleri

- view-model sıralama ve gruplama,
- unknown/stale/null semantiği,
- token mirror eşleşmesi,
- chart option üretimi,
- command palette result grouping,
- responsive component varyantları,
- reduced-motion davranışı.

### 17.2 E2E

- gerçek test stack'i üzerinde login ve kritik route'lar,
- Command Center'dan evidence detail'e geçiş,
- URL-persisted scope/time/filter,
- keyboard-only kullanım,
- mobil navigation ve palette,
- light/dark tema,
- browser'ın yalnız Drake API ile konuşması,
- yatay taşma kontrolü.

### 17.3 Görsel QA

Mevcut yapısal E2E testlerine ek olarak deterministik fixture verisiyle seçili
ekranlarda screenshot karşılaştırması yapılmalıdır:

- Command Center desktop light/dark,
- Command Center 1280 px,
- Command Center mobile,
- service detail,
- cluster detail,
- incident correlation timeline,
- empty/stale/partial örnekleri.

Her implementasyon dalgası tarayıcıda gerçek screenshot incelemesi yapılmadan
tamamlanmış sayılmaz.

## 18. Teslimat dalgaları

Bu bölüm implementasyon planı değil, kapsam sırasıdır. Ayrıntılı görev planı
brief onayından sonra ayrıca yazılacaktır.

### Dalga 0 — Görsel baseline ve veri envanteri

- Kritik route screenshot baseline'ları,
- mevcut endpoint → UI → state matrisi,
- kullanıcı görevleri ve navigation envanteri,
- performans/bundle başlangıç ölçümleri.

### Dalga 1 — Tasarım sistemi ve shell

- yeni tokenlar ve tipografi,
- sidebar/topbar/page frame,
- command palette,
- ortak panel ve state yüzeyleri,
- chart frame güncellemesi.

### Dalga 2 — Command Center

- operational verdict,
- correlation timeline,
- attention queue,
- health matrix,
- capacity risk,
- evidence coverage.

### Dalga 3 — Inventory exploration

- Projects,
- Project/environment/service hierarchy,
- Clusters ve inventory.

### Dalga 4 — Reliability ve response

- Incidents,
- Alerts,
- Deployments,
- SLO,
- Protection.

### Dalga 5 — Platform ve governance

- Integrations,
- Onboarding,
- Notification routing,
- Access & audit.

### Dalga 6 — Tutarlılık ve kaldırma

- kullanılmayan legacy primitives,
- eski token alias'ları,
- yinelenen layoutlar,
- doküman ve screenshot baseline güncellemesi.

## 19. Sonnet 5 senior developer çalışma protokolü

Senior developer her dalgaya başlamadan önce şu kaynakları okumalıdır:

1. Bu brief,
2. `docs/SPRINT_13_UI_AUDIT.md`,
3. ilgili ADR'ler,
4. değiştirilecek route ve testleri,
5. mevcut chart/state/shell sözleşmeleri.

Çalışma kuralları:

- Tek seferde yalnız onaylı bir dikey dilim uygulanır.
- Önce test/kabul kanıtı yazılır, sonra implementasyon yapılır.
- Mevcut veri semantiği UI kolaylığı için gevşetilmez.
- Büyük “rewrite everything” commitleri oluşturulmaz.
- Her PR tek kullanıcı sorusunu veya tek altyapı katmanını çözer.
- İlgisiz backend refactor'u yapılmaz.
- Yeni bağımlılık CTO onayı olmadan eklenmez.
- Her PR'da desktop, 1280 px ve mobil screenshot kanıtı bulunur.
- “Premium”, “modern” veya “clean” gibi ölçülemeyen kabul ifadeleri yerine
  açık davranış ve görsel kriter kullanılır.

### Escalation

Sonnet 5 iki teknik denemede çözemediği yüksek belirsizlikli mimari veya
kritik güvenlik kararını zorlamaz. Bulguyu, denenmiş seçenekleri ve riski CTO'ya
iletir. Daha pahalı model yalnız bu escalation için değerlendirilir.

## 20. Definition of done

Bir remake dilimi ancak aşağıdakilerin tamamı sağlandığında biter:

- Briefteki ana kullanıcı sorusunu cevaplar.
- Loading/empty/error/denied/stale/partial durumları tasarlanmıştır.
- Unit, typecheck, lint ve ilgili E2E kontrolleri çalıştırılmış ve çıktısı
  görülmüştür.
- Light/dark ve tanımlı responsive boyutlarda screenshot QA yapılmıştır.
- Klavye ve axe kontrolleri geçmiştir.
- API ve state semantiği korunmuştur.
- Bundle ve query concurrency regresyonu yoktur.
- Eski ve yeni componentlerin rastgele karıştığı yarım bir ekran bırakılmamıştır.
- PR yalnız ilgili scope'u içerir.
- Görsel sonuç CTO tarafından brief ile karşılaştırılmıştır.

## 21. Brief dışı kararlar

Bu brief aşağıdaki konuları şimdilik açmaz:

- mobil native uygulama,
- public/customer-facing status page,
- telemetry backend değişimi,
- serbest PromQL editörü,
- AI ile otomatik root-cause iddiası,
- yeni microservice mimarisi,
- Datalake ve Drake frontend kodlarının birleştirilmesi,
- gerçek zamanlı multi-user collaboration.

Bu ihtiyaçlardan biri daha sonra seçilirse ayrı ürün kararı ve spec gerekir.

## 22. Onay kapısı

Bu belge remake'in ürün ve tasarım yönünü sabitler. Onaydan sonra sıradaki
çıktı, **Dalga 0 + Dalga 1 + Command Center** için dosya ve test seviyesinde
ayrıntılı implementasyon planıdır. Senior developer bu plan oluşmadan
uygulama koduna başlamayacaktır.
