/**
 * Strings for Agent Visibility's Office: the people who change the
 * repositories, drawn as people in an office. People, repository and
 * project names are data and shown as recorded.
 */
import { defineMessages } from "../define";

export const devOffice = defineMessages({
  en: {
    title: "The office",
    lead: "Everyone who changes the repositories, as people in an office. Live, whoever's Claude Code is working right now sits at their desk in that product's room. Replay a day and every commit takes its author to their desk; every delivery is carried to the cluster.",
    mode: { label: "Mode", live: "Live", replay: "Replay a day" },
    live: {
      working: "{count, plural, =0 {Nobody at a desk} other {# at work}}",
      refresh: "Refreshed every 30 seconds",
      notConnected: "Claude Code does not report here yet. Live, the office shows who works where once it does; a day can be replayed from commits meanwhile.",
    },
    replay: {
      day: "Day",
      play: "Play",
      pause: "Pause",
      speed: "Speed",
      time: "Time of day",
      summary: "{commits, plural, one {# commit} other {# commits}} · {deliveries, plural, one {# delivery} other {# deliveries}}",
      empty: "Nothing was committed or delivered on this day.",
      truncated: "Only the day's first 2,000 commits are played.",
      ended: "The day is over",
    },
    zoom: { label: "Zoom", in: "Zoom in", out: "Zoom out", fit: "Show the whole office" },
    fullscreen: { enter: "Full screen", exit: "Leave full screen" },
    rooms: {
      cluster: "Cluster",
      lobby: "Lobby",
      vacant: "Empty office",
      available: "Available",
      other: "{count, plural, one {Other project} other {Other projects · #}}",
      notCatalogued: "not in the catalog",
    },
    cluster: {
      health: "{healthy} of {total} workloads healthy",
      ref: "{ref}",
      unknown: "No cluster in view",
    },
    places: {
      title: "Who is where",
      lobby: "in the lobby",
      room: "at a desk in {room}",
      cluster: "carrying a delivery to the cluster",
      walking: "on the way",
    },
    feed: {
      commit: "{person} → {room}",
      deployment: "{person} delivers {room}",
      anonymous: "{room} delivered",
    },
    hint: "Drag to look around. Click a person or a room for the details.",
    empty: "Nobody has changed a repository in this window yet.",
  },
  tr: {
    title: "Ofis",
    lead: "Repoları değiştiren herkes, bir ofisteki insanlar olarak. Canlıda Claude Code'u şu an çalışan kişi o ürünün odasında masasında oturur. Bir günü tekrar oynatınca her commit sahibini masasına götürür; her teslimat kümeye taşınır.",
    mode: { label: "Mod", live: "Canlı", replay: "Günü oynat" },
    live: {
      working: "{count, plural, =0 {Masada kimse yok} other {# kişi çalışıyor}}",
      refresh: "30 saniyede bir yenilenir",
      notConnected: "Claude Code henüz buraya raporlamıyor. Raporladığında canlı mod kimin nerede çalıştığını gösterir; o zamana kadar bir gün commit'lerden oynatılabilir.",
    },
    replay: {
      day: "Gün",
      play: "Oynat",
      pause: "Duraklat",
      speed: "Hız",
      time: "Günün saati",
      summary: "{commits, plural, one {# commit} other {# commit}} · {deliveries, plural, one {# teslimat} other {# teslimat}}",
      empty: "Bu gün ne commit atıldı ne teslimat yapıldı.",
      truncated: "Günün yalnızca ilk 2.000 commit'i oynatılır.",
      ended: "Gün bitti",
    },
    zoom: { label: "Yakınlaştırma", in: "Yakınlaştır", out: "Uzaklaştır", fit: "Tüm ofisi göster" },
    fullscreen: { enter: "Tam ekran", exit: "Tam ekrandan çık" },
    rooms: {
      cluster: "Küme",
      lobby: "Lobi",
      vacant: "Boş ofis",
      available: "Boş",
      other: "{count, plural, one {Diğer proje} other {Diğer projeler · #}}",
      notCatalogued: "katalogda değil",
    },
    cluster: {
      health: "{total} iş yükünden {healthy} sağlıklı",
      ref: "{ref}",
      unknown: "Görünür küme yok",
    },
    places: {
      title: "Kim nerede",
      lobby: "lobide",
      room: "{room} odasında masasında",
      cluster: "kümeye teslimat taşıyor",
      walking: "yolda",
    },
    feed: {
      commit: "{person} → {room}",
      deployment: "{person}, {room} teslimatını taşıyor",
      anonymous: "{room} teslim edildi",
    },
    hint: "Etrafa bakmak için sürükle. Ayrıntılar için bir kişiye ya da odaya tıkla.",
    empty: "Bu dönemde henüz kimse bir repoyu değiştirmedi.",
  },
});
