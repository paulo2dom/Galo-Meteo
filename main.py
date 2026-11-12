# Galo Meteo - estação meteorológica web no Raspberry Pi Pico 2 W (MicroPython)
#
# O Pico faz três coisas:
#   1. acerta a hora por NTP e serve-a em /hora (relógio da página)
#   2. serve a página da estação em / (enviada aos bocados, para poupar RAM)
#   3. faz de "ponte" para a API do IPMA em /ipma/... (só é usado se o
#      browser não conseguir ir diretamente à API por causa de CORS)
#
# Tudo o resto (Open-Meteo, qualidade do ar, fontes) é carregado pelo browser,
# por isso o Pico só tem de guardar e enviar ~61 KB de HTML.

import gc, network, ntptime, socket, ssl, time

try:
    from secrets import SSID, PASSWORD   # ficheiro local, fora do git
except ImportError:
    print("Falta o secrets.py no Pico: copia o secrets.example.py para secrets.py,")
    print("preenche o nome e a palavra-passe do Wi-Fi e envia-o para o Pico.")
    raise

# ---------- Wi-Fi ----------
network.hostname("meteo")            # em muitas redes fica em http://meteo.local
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(SSID, PASSWORD)
print("A ligar ao Wi-Fi...")
while not wlan.isconnected():
    time.sleep(0.5)
print("Ligado! Abre http://%s no browser" % wlan.ifconfig()[0])

# ---------- NTP ----------
ntptime.host = "pt.pool.ntp.org"

def sincronizar():
    try:
        ntptime.settime()            # relógio interno em UTC
        print("Hora sincronizada")
    except Exception as e:
        print("Falha NTP:", e)

sincronizar()
ultima_sync = time.time()

# ---------- Hora de Portugal continental (com hora de verão) ----------
def ultimo_domingo(ano, mes):
    wd = time.localtime(time.mktime((ano, mes, 31, 0, 0, 0, 0, 0)))[6]  # 6 = domingo
    return 31 - (wd + 1) % 7

def hora_local():
    t = time.time()
    ano = time.localtime(t)[0]
    inicio = time.mktime((ano, 3, ultimo_domingo(ano, 3), 1, 0, 0, 0, 0))
    fim = time.mktime((ano, 10, ultimo_domingo(ano, 10), 1, 0, 0, 0, 0))
    return time.localtime(t + (3600 if inicio <= t < fim else 0))

# ---------- Envio aos bocados ----------
def enviar(cliente, dados):
    if isinstance(dados, str):
        dados = dados.encode()
    mv = memoryview(dados)
    for i in range(0, len(dados), 1024):     # pedaços pequenos: o lwIP do Pico agradece
        cliente.sendall(mv[i:i + 1024])

def responder(cliente, estado, tipo, corpo):
    cliente.send(("HTTP/1.1 %s\r\nContent-Type: %s\r\nCache-Control: no-store\r\n"
                  "Connection: close\r\n\r\n" % (estado, tipo)).encode())
    enviar(cliente, corpo)

# ---------- Ponte para a API do IPMA ----------
PERMITIDOS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/_-.")

def abrir_tls(sock, host):
    # Nota: o certificado do servidor não é verificado (o Pico não tem CAs
    # carregadas). Aceitável para dados públicos de meteorologia.
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.verify_mode = ssl.CERT_NONE
        return ctx.wrap_socket(sock, server_hostname=host)
    except AttributeError:
        return ssl.wrap_socket(sock, server_hostname=host)

def proxy_ipma(cliente, caminho):
    if (not caminho.startswith("/open-data/") or ".." in caminho
            or any(c not in PERMITIDOS for c in caminho)):
        responder(cliente, "400 Bad Request", "text/plain", "pedido invalido")
        return
    raw = socket.socket()
    s = None
    try:
        raw.settimeout(10)
        raw.connect(socket.getaddrinfo("api.ipma.pt", 443)[0][-1])
        s = abrir_tls(raw, "api.ipma.pt")
        # HTTP/1.0 para a resposta não vir em "chunked"
        s.write(("GET %s HTTP/1.0\r\nHost: api.ipma.pt\r\n"
                 "User-Agent: pico-meteo\r\nConnection: close\r\n\r\n" % caminho).encode())
        estado = s.readline()
        while True:                                  # salta os cabeçalhos
            l = s.readline()
            if not l or l == b"\r\n":
                break
        if b" 200 " not in estado:
            responder(cliente, "502 Bad Gateway", "text/plain", "IPMA: " + estado.decode().strip())
            return
        cliente.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n"
                     b"Cache-Control: no-store\r\nConnection: close\r\n\r\n")
        buf = bytearray(1024)
        mv = memoryview(buf)
        while True:                                  # reencaminha aos bocados (pouca RAM)
            n = s.readinto(buf)
            if not n:
                break
            cliente.sendall(mv[:n])
    finally:
        (s or raw).close()
        gc.collect()

# ---------- Página ----------
HTML = r"""<!doctype html>
<html lang="pt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Galo Meteo</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🐓</text></svg>">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fredoka:wght@500;600;700&family=Nunito:wght@400;600;800&display=swap">
<style>
:root{--tinta:#1d1b2f;--papel:#fffdf6;--sub:#5b5870;--sol:#ffd23f;--coral:#ff6b5b;--mar:#2ec4b6;--ceu:#4ea8ff;
  --fundo:#ffe8a3;--fundo-pt:#f5d264;--fundo-txt:#1d1b2f}
body[data-ceu=nuvens]{--fundo:#dfe7ef;--fundo-pt:#c6d3e0}
body[data-ceu=chuva]{--fundo:#b9d4ec;--fundo-pt:#9cbfe0}
body[data-ceu=trovoada]{--fundo:#a29ec0;--fundo-pt:#8d89ab}
body[data-ceu=noite]{--fundo:#1f2547;--fundo-pt:#2c3463;--fundo-txt:#f4f1ff}
body[data-ceu=calor]{--fundo:#ffc9a6;--fundo-pt:#ffab7a}
body[data-ceu=ameno]{--fundo:#d6f0c2;--fundo-pt:#b9e09c}
body[data-ceu=frio]{--fundo:#d4ecfa;--fundo-pt:#b2daf2}
body[data-ceu=neve]{--fundo:#f1f5fb;--fundo-pt:#d9e2ee}
body[data-ceu=nevoeiro]{--fundo:#dcdce2;--fundo-pt:#c5c5ce}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;color:var(--tinta);font:16px/1.45 Nunito,system-ui,sans-serif;
  background:var(--fundo) radial-gradient(var(--fundo-pt) 1.6px,transparent 1.8px) 0 0/18px 18px;transition:background-color .8s}
.fx,h1,h2{font-family:Fredoka,Nunito,system-ui,sans-serif}
header{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:12px;
  padding:18px 16px 4px;color:var(--fundo-txt);max-width:1200px;margin:auto}
#hora{font:700 clamp(2.8rem,10vw,4.6rem)/1 Fredoka,system-ui,sans-serif;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
#data{font-weight:800;margin-top:4px}
.marca{font:600 .95rem Fredoka,system-ui,sans-serif;opacity:.8;margin-bottom:4px}
select,button{font:700 1rem Nunito,system-ui,sans-serif;color:var(--tinta);background:var(--papel);border:3px solid var(--tinta);
  border-radius:12px;padding:8px 12px;box-shadow:3px 3px 0 var(--tinta);cursor:pointer}
button:active{transform:translate(2px,2px);box-shadow:1px 1px 0 var(--tinta)}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:18px;padding:14px 16px 22px;max-width:1200px;margin:auto}
.card{background:var(--papel);border:3px solid var(--tinta);border-radius:20px;padding:16px 18px;box-shadow:6px 6px 0 var(--tinta);min-width:0}
.card h2{margin:0 0 12px;font-size:1.1rem;font-weight:600}
.largo{grid-column:1/-1}
/* ---- escolher o dia ---- */
.chips{grid-column:1/-1;min-width:0;display:flex;gap:10px;overflow-x:auto;padding:2px 2px 8px;scroll-margin-top:12px}
.chip{flex:0 0 auto;font-size:.95rem;padding:6px 12px;white-space:nowrap}
.chip small{font-weight:600;color:var(--sub)}
.chip.on{background:var(--sol)}
.quando{display:inline-block;margin-top:14px;font:600 .9rem Fredoka,system-ui,sans-serif;background:var(--tinta);color:var(--papel);
  border-radius:8px;padding:2px 10px}
.sub{color:var(--sub)}
/* ---- herói ---- */
.heroi{display:grid;grid-template-columns:minmax(150px,260px) 1fr;gap:10px 22px;align-items:center}
@media (max-width:640px){.heroi{grid-template-columns:1fr}#masc{max-width:220px;margin:auto}}
.balao{position:relative;background:#fff;border:3px solid var(--tinta);border-radius:22px;padding:14px 16px;font-size:1.12rem;font-weight:700}
.balao:before{content:"";position:absolute;left:-17px;top:34px;border:14px solid transparent;border-right:16px solid var(--tinta);border-left:0}
.balao:after{content:"";position:absolute;left:-11px;top:37px;border:11px solid transparent;border-right:13px solid #fff;border-left:0}
@media (max-width:640px){.balao:before{left:46%;top:-17px;border:14px solid transparent;border-bottom:16px solid var(--tinta);border-top:0}
  .balao:after{left:calc(46% + 3px);top:-11px;border:11px solid transparent;border-bottom:13px solid #fff;border-top:0}}
.quem{font:600 .85rem Fredoka,system-ui,sans-serif;color:var(--sub);margin-bottom:4px}
.linha-temp{display:flex;align-items:center;flex-wrap:wrap;gap:6px 16px;margin-top:14px}
#temp{font:700 4.2rem/1 Fredoka,system-ui,sans-serif}
#desc{font-weight:800;font-size:1.15rem}
.carimbo{display:inline-block;font:700 1rem Fredoka,system-ui,sans-serif;letter-spacing:.06em;background:var(--sol);border:3px solid var(--tinta);
  border-radius:10px;padding:4px 10px;transform:rotate(-4deg);box-shadow:3px 3px 0 var(--tinta)}
.acoes{margin-top:12px;display:flex;gap:10px;flex-wrap:wrap}
/* ---- mascote ---- */
#masc{width:100%;height:auto;display:block;overflow:visible}
#masc .acc,#masc .fechado{display:none}
#masc.sol .oculos,#masc.calor .suor,#masc.chuva .guarda,#masc.chuva .gotas,#masc.frio .gorro,#masc.frio .cachecol,
#masc.noite .touca,#masc.noite .zzz,#masc.noite .fechado,#masc.vento .vento,#masc.nevoa .nevoa,#masc.trovoada .raio,#masc.neve .flocos{display:inline}
#masc.noite .pupila,#masc.noite .branco{display:none}
#boneco{animation:boia 3s ease-in-out infinite;transform-origin:110px 152px}
#masc.vento #boneco{animation:abana .5s ease-in-out infinite}
.gotas path,.flocos text{animation:cai 1.1s linear infinite}
.gotas path:nth-child(2n),.flocos text:nth-child(2n){animation-delay:-.55s}
.suor path{animation:pinga 1.4s ease-in infinite}
.vento path{stroke-dasharray:30 14;animation:sopra .8s linear infinite}
.zzz text{animation:sobe 2.4s ease-in-out infinite}
@keyframes boia{50%{transform:translateY(-7px) rotate(-2deg)}}
@keyframes abana{50%{transform:rotate(-4deg) translateX(-3px)}}
@keyframes cai{from{transform:translateY(-12px);opacity:0}30%{opacity:1}to{transform:translateY(26px);opacity:0}}
@keyframes pinga{from{transform:translateY(0);opacity:1}to{transform:translateY(16px);opacity:0}}
@keyframes sopra{to{stroke-dashoffset:-44}}
@keyframes sobe{50%{transform:translateY(-6px);opacity:.5}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
/* ---- avisos ---- */
#avisos{display:flex;flex-wrap:wrap;gap:10px}
.aviso{border:3px solid var(--tinta);border-radius:12px;padding:6px 12px;font-weight:800;box-shadow:3px 3px 0 var(--tinta)}
.yellow{background:#ffd43b}.orange{background:#ff922b}.red{background:#e03131;color:#fff}
/* ---- mosaicos ---- */
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px}
.tile{border:2.5px solid var(--tinta);border-radius:14px;padding:10px 12px;background:#fff}
.tile .rot{display:block;font-size:.8rem;font-weight:800;color:var(--sub);text-transform:uppercase;letter-spacing:.04em}
.tile b{display:flex;align-items:center;gap:6px;font-size:1.6rem;font-weight:600;line-height:1.25}
.tile b small{font-size:.85rem;color:var(--sub)}
.tile .nota{display:block;font-size:.85rem;font-weight:600}
.seta{width:20px;height:20px;fill:var(--tinta)}
/* ---- hora a hora ---- */
.horas{display:flex;gap:8px;overflow-x:auto;padding:4px 2px 10px;scroll-snap-type:x mandatory}
.h{flex:0 0 66px;text-align:center;border:2.5px solid var(--tinta);border-radius:14px;padding:8px 2px;background:#fff;scroll-snap-align:start}
.h.ja{background:var(--sol)}
.h .hh{font-weight:800;font-size:.85rem}.h i{display:block;font-style:normal;font-size:1.5rem;margin:2px 0}
.h b{font:600 1.15rem Fredoka,system-ui,sans-serif;display:block}.h .pp{font-size:.75rem;font-weight:700;color:#1c5fa8}
svg.graf{width:100%;height:auto;display:block;margin-top:6px}
.barra{fill:var(--ceu);opacity:.45}.linha{fill:none;stroke:var(--coral);stroke-width:3;stroke-linejoin:round}
.eixo{fill:var(--sub);font:700 12px Nunito,system-ui,sans-serif;text-anchor:middle}
.valor{fill:var(--tinta);font:600 12px Fredoka,system-ui,sans-serif;text-anchor:middle}
.legenda{font-size:.82rem;color:var(--sub);margin-top:4px;font-weight:600}
/* ---- sol & lua ---- */
.sol-lua{display:grid;grid-template-columns:1fr;gap:10px}
.par{display:flex;justify-content:space-between;font-weight:800;gap:10px}
.lua{display:flex;align-items:center;gap:12px;border-top:2.5px dashed var(--tinta);padding-top:10px}
.lua .em{font-size:2.6rem;line-height:1}
/* ---- ar ---- */
.aqi{display:flex;align-items:center;gap:14px}
.aqi .n{min-width:74px;text-align:center;font:700 2rem/1 Fredoka,system-ui,sans-serif;border:3px solid var(--tinta);border-radius:14px;padding:10px 8px}
.polen{display:grid;grid-template-columns:auto 1fr auto;gap:6px 10px;align-items:center;margin-top:14px;font-weight:700;font-size:.92rem}
.met{display:flex;gap:3px}.met span{width:18px;height:10px;border:2px solid var(--tinta);border-radius:3px}.met span.on{background:var(--coral)}
/* ---- vestir ---- */
.vestir{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.vestir li{display:flex;gap:10px;align-items:center;border:2.5px solid var(--tinta);border-radius:12px;padding:8px 10px;background:#fff;font-weight:700}
.vestir li .e{font-size:1.5rem}.vestir li small{display:block;font-weight:600;color:var(--sub)}
/* ---- 5 dias ---- */
#dias{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}
@media (max-width:640px){#dias{grid-template-columns:repeat(2,1fr)}}
.dia{text-align:center;border:2.5px solid var(--tinta);border-radius:14px;padding:10px 6px;background:#fff}
button.dia{font:inherit;color:inherit;width:100%;box-shadow:none;transition:transform .15s}
button.dia:hover{transform:translateY(-3px)}
.dia.on{background:var(--sol);box-shadow:3px 3px 0 var(--tinta)}
.dia .nome,.dia .ic,.dia .mm,.dia .extra{display:block}
.dia .nome{font-weight:800;text-transform:capitalize}.dia .ic{font-size:2.2rem;margin:4px 0}
.dia .mm{font:600 1.3rem Fredoka,system-ui,sans-serif}.dia .mm small{color:var(--sub);font-size:1rem}
.dia .extra{font-size:.8rem;font-weight:600;color:var(--sub)}
.dia .piada{display:inline-block;margin-top:6px;font-size:.78rem;font-weight:800;background:var(--sol);border:2px solid var(--tinta);border-radius:8px;padding:1px 6px;transform:rotate(-2deg)}
footer{padding:0 16px 24px;color:var(--fundo-txt);font-size:.85rem;font-weight:600;max-width:1200px;margin:auto}
footer a{color:inherit}
</style>
</head>
<body data-ceu="sol">
<header>
  <div>
    <div class="marca">🐓 Galo Meteo</div>
    <div id="hora">--:--</div>
    <div id="data"></div>
  </div>
  <select id="local" aria-label="Local"></select>
</header>

<main>
  <nav id="chips" class="chips" aria-label="Escolher o dia"></nav>

  <section class="card largo">
    <div class="heroi">
      <svg id="masc" viewBox="0 0 220 200" role="img" aria-label="O Galo Zé, galo de Barcelos cata-vento">
        <g class="acc gotas" fill="#4ea8ff" stroke="#1d1b2f" stroke-width="2">
          <path d="M20 64q5 9 0 12q-5-3 0-12z"/><path d="M204 72q5 9 0 12q-5-3 0-12z"/>
          <path d="M30 124q5 9 0 12q-5-3 0-12z"/><path d="M198 122q5 9 0 12q-5-3 0-12z"/></g>
        <g class="acc flocos" font-size="16" fill="#fff" stroke="#1d1b2f" stroke-width=".6">
          <text x="14" y="70">❄</text><text x="196" y="76">❄</text><text x="26" y="130">❄</text><text x="194" y="132">❄</text></g>
        <g class="acc raio"><path d="M34 2 22 28h10l-8 22 20-30H34l8-18z" fill="#ffd23f" stroke="#1d1b2f" stroke-width="3" stroke-linejoin="round"/></g>
        <g class="acc vento" fill="none" stroke="#1d1b2f" stroke-width="3" stroke-linecap="round">
          <path d="M188 96h30"/><path d="M196 112h22"/><path d="M186 140h30"/></g>
        <path d="M110 150v48" stroke="#1d1b2f" stroke-width="5" stroke-linecap="round"/>
        <circle cx="110" cy="178" r="18" fill="#fff" stroke="#1d1b2f" stroke-width="3"/>
        <g font-family="Fredoka,system-ui,sans-serif" font-weight="700" font-size="8" fill="#1d1b2f" text-anchor="middle">
          <text x="110" y="170">N</text><text x="110" y="193">S</text><text x="98" y="181">O</text><text x="122" y="181">E</text></g>
        <g id="cv" style="transform-box:view-box;transform-origin:110px 178px;transition:transform 1.2s ease">
          <path d="M110 165l5 12-5-3-5 3z" fill="#ff6b5b" stroke="#1d1b2f" stroke-width="1.5" stroke-linejoin="round"/>
          <path d="M110 189v-11" stroke="#1d1b2f" stroke-width="2.5" stroke-linecap="round"/></g>
        <g id="boneco">
          <g class="acc guarda">
            <path d="M128 8 126 100" stroke="#1d1b2f" stroke-width="4" stroke-linecap="round"/>
            <path d="M66 36Q128-20 190 36q-10-8-21 0-11-10-21 0-10-10-21 0-11-10-21 0-10-10-21 0-9-8-19 0z" fill="#ff6b5b" stroke="#1d1b2f" stroke-width="4" stroke-linejoin="round"/>
            <path d="M128 8Q112 18 106 36M128 8q16 10 22 28" fill="none" stroke="#1d1b2f" stroke-width="3"/></g>
          <g stroke="#ffb703" stroke-width="5" stroke-linecap="round"><path d="M100 138l6 13"/><path d="M120 138l-6 13"/><path d="M98 152h24"/></g>
          <g fill="#23202f" stroke="#1d1b2f" stroke-width="3" stroke-linejoin="round">
            <path d="M70 104C42 92 26 62 40 30c8 26 22 46 40 58z"/>
            <path d="M64 114C32 112 12 84 16 54c10 28 30 44 54 50z"/>
            <path d="M66 122C40 130 18 116 12 96c16 14 34 18 56 14z"/></g>
          <g fill="none" stroke-width="3" stroke-linecap="round">
            <path d="M46 50c6 14 14 24 24 32" stroke="#ffd23f"/><path d="M24 68c8 16 20 26 36 32" stroke="#ff6b5b"/>
            <path d="M22 104c12 8 24 10 38 8" stroke="#2ec4b6"/></g>
          <path d="M60 100C60 72 108 66 136 82c18 10 22 38 4 54-20 16-64 16-78-4-6-10-4-22-2-32z" fill="#23202f" stroke="#1d1b2f" stroke-width="3"/>
          <path d="M124 86c4-16 10-32 24-40 14-6 26 4 24 18-2 12-12 20-20 32z" fill="#23202f" stroke="#1d1b2f" stroke-width="3" stroke-linejoin="round"/>
          <path d="M144 44q0-14 9-8 2-13 10-5 5-10 10 2 1 7-6 11-12 2-23 0z" fill="#e63946" stroke="#1d1b2f" stroke-width="2.5" stroke-linejoin="round"/>
          <path d="M163 66q0 14 8 12 5-4-1-14z" fill="#e63946" stroke="#1d1b2f" stroke-width="2"/>
          <path d="M170 53l15 5-14 5z" fill="#ffb703" stroke="#1d1b2f" stroke-width="2" stroke-linejoin="round"/>
          <path d="M100 100c-5-8-16-3-11 6l11 11 11-11c5-9-6-14-11-6z" fill="#e63946"/>
          <circle cx="78" cy="116" r="5" fill="#ffd23f"/><circle cx="78" cy="116" r="2" fill="#e63946"/>
          <circle cx="126" cy="118" r="5" fill="#4ea8ff"/><circle cx="126" cy="118" r="2" fill="#fff"/>
          <path d="M86 132q8-8 16 0-8 6-16 0zM110 90q8-8 16 0-8 6-16 0z" fill="#2ec4b6"/>
          <g fill="#fff"><circle cx="88" cy="88" r="2"/><circle cx="136" cy="104" r="2"/><circle cx="70" cy="132" r="2"/><circle cx="112" cy="134" r="2"/></g>
          <path d="M84 104q20 22 46 6" fill="none" stroke="#fff" stroke-width="2.5" stroke-dasharray="1 5" stroke-linecap="round"/>
          <g class="acc cachecol" fill="#ffd23f" stroke="#1d1b2f" stroke-width="3" stroke-linejoin="round">
            <path d="M124 80q16 10 34 2l3 11q-19 9-38-2z"/><path d="M132 88l-8 26 11 2 6-24z"/></g>
          <circle class="branco" cx="158" cy="54" r="5.5" fill="#fff" stroke="#1d1b2f" stroke-width="1.5"/>
          <circle class="pupila" cx="159.5" cy="54" r="2.6" fill="#1d1b2f"/>
          <path class="fechado" d="M153 55q5 4 10 0" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round"/>
          <g class="acc oculos"><rect x="149" y="48" width="22" height="12" rx="5" fill="#1d1b2f" stroke="#ffd23f" stroke-width="2.5"/>
            <path d="M153 51h6" stroke="#fff" stroke-width="2" opacity=".7"/></g>
          <g class="acc suor" fill="#4ea8ff" stroke="#1d1b2f" stroke-width="2"><path d="M188 38q5 9 0 12-5-3 0-12z"/><path d="M136 40q4 8 0 10-4-2 0-10z"/></g>
          <g class="acc gorro"><path d="M142 46q12-36 34 2-18-10-34-2z" fill="#ff6b5b" stroke="#1d1b2f" stroke-width="3" stroke-linejoin="round"/>
            <circle cx="158" cy="22" r="7" fill="#ffd23f" stroke="#1d1b2f" stroke-width="3"/></g>
          <g class="acc touca"><path d="M142 46q18-42 58-30-16 10-16 34-20-12-42-4z" fill="#7b6cff" stroke="#1d1b2f" stroke-width="3" stroke-linejoin="round"/>
            <circle cx="200" cy="16" r="6" fill="#fff" stroke="#1d1b2f" stroke-width="3"/></g>
        </g>
        <g class="acc zzz" font-family="Fredoka,system-ui,sans-serif" font-weight="700" fill="#fff" stroke="#1d1b2f" stroke-width="1.2">
          <text x="116" y="34" font-size="16">z</text><text x="100" y="22" font-size="20">z</text><text x="82" y="10" font-size="24">Z</text></g>
        <g class="acc nevoa" fill="none" stroke="#fff" stroke-width="9" stroke-linecap="round" opacity=".8">
          <path d="M4 96q20-8 40 0t40 0 40 0 40 0 40 0"/><path d="M20 130q20-8 40 0t40 0 40 0 40 0"/></g>
      </svg>
      <div>
        <div class="balao" aria-live="polite"><div class="quem">O Galo Zé diz:</div><span id="fala">Deixa-me espreitar o céu…</span></div>
        <div id="quando" class="quando">Agora</div>
        <div class="linha-temp">
          <div id="temp">--°</div>
          <div><div id="desc">a carregar…</div><div id="sens" class="sub"></div></div>
          <span class="carimbo" id="carimbo">…</span>
        </div>
        <div class="acoes"><button id="outra" type="button">🎲 Conta outra, Zé</button></div>
      </div>
    </div>
  </section>

  <section class="card largo"><h2 id="tit-avisos">⚠️ Avisos IPMA</h2><div id="avisos" class="sub">a carregar…</div></section>

  <section class="card largo"><h2 id="tit-tiles">🔍 Hoje em detalhe</h2><div id="tiles" class="tiles">a carregar…</div></section>

  <section class="card largo"><h2 id="tit-horas">⏰ Hora a hora</h2><div id="horas" class="horas"></div><div id="grafico"></div>
    <div class="legenda">Linha: temperatura · barras: probabilidade de chuva</div></section>

  <section class="card"><h2 id="tit-sol">🌅 Sol &amp; Lua</h2><div id="sol" class="sol-lua">a carregar…</div></section>
  <section class="card"><h2 id="tit-ar">🌬️ Ar &amp; pólen</h2><div id="ar">a carregar…</div></section>
  <section class="card"><h2 id="tit-vestir">👕 O que vestir</h2><ul id="vestir" class="vestir"><li>a carregar…</li></ul>
    <div class="legenda" id="vestir-quando"></div></section>

  <section class="card largo"><h2>📅 Previsão IPMA · 5 dias</h2><div id="dias">a carregar…</div>
    <div class="legenda">Toca num dia para ver o Galo Zé, os detalhes e as piadas desse dia.</div></section>
</main>

<footer>
  <span id="estado"></span><br>
  Dados: <a href="https://www.ipma.pt/" target="_blank">IPMA</a> ·
  <a href="https://open-meteo.com/" target="_blank">Open-Meteo</a> (tempo e qualidade do ar/CAMS) ·
  as piadas são do Galo Zé, os números não.
</footer>

<script>
const $ = id => document.getElementById(id);
const p2 = n => String(n).padStart(2, "0");
const R = Math.round;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const DIAS = ["domingo","segunda","terça","quarta","quinta","sexta","sábado"];
const DIAS_C = ["dom","seg","ter","qua","qui","sex","sáb"];
const MESES = ["janeiro","fevereiro","março","abril","maio","junho","julho","agosto","setembro","outubro","novembro","dezembro"];
const RUMOS = ["N","NE","E","SE","S","SO","O","NO"];
const VENTO_IPMA = {1:"fraco", 2:"moderado", 3:"forte", 4:"muito forte"};

/* ---------- Relógio (hora vem do Pico) ---------- */
let diff = null;
async function syncHora() {
  try {
    const h = await (await fetch("/hora", {cache:"no-store"})).json();
    diff = Date.UTC(h.a, h.m - 1, h.d, h.h, h.mi, h.s) - Date.now();
  } catch (e) {}
}
function tick() {
  if (diff === null) return;
  const t = new Date(Date.now() + diff);
  $("hora").textContent = p2(t.getUTCHours()) + ":" + p2(t.getUTCMinutes());
  $("data").textContent = DIAS[t.getUTCDay()] + ", " + t.getUTCDate() + " de " + MESES[t.getUTCMonth()];
}
syncHora(); setInterval(syncHora, 60000); setInterval(tick, 500);

/* ---------- IPMA: direto, ou através do Pico se houver bloqueio CORS ---------- */
let ipmaDireto = true;
async function ipma(path) {
  if (ipmaDireto) {
    try {
      const r = await fetch("https://api.ipma.pt" + path, {cache:"no-store"});
      if (r.ok) return await r.json();
      throw new Error("IPMA " + r.status);
    } catch (e) {
      if (!(e instanceof TypeError)) throw e;   // TypeError = bloqueado/sem rede
      ipmaDireto = false;
    }
  }
  const r = await fetch("/ipma" + path, {cache:"no-store"});
  if (!r.ok) throw new Error("IPMA via Pico " + r.status);
  return r.json();
}

/* ---------- Ícones e categorias ---------- */
function iconeIPMA(id) {
  if (id === 1) return "☀️";
  if (id === 2) return "🌤️";
  if (id === 3 || id === 25) return "⛅";
  if (id === 4 || id === 24 || id === 27) return "☁️";
  if (id === 5) return "🌥️";
  if ([6,7,12,13,15].includes(id)) return "🌦️";
  if ([8,9,10,11,14].includes(id)) return "🌧️";
  if ([16,17,26].includes(id)) return "🌫️";
  if ([18,22].includes(id)) return "❄️";
  if ([19,20,23].includes(id)) return "⛈️";
  if ([21,28,29,30].includes(id)) return "🌨️";
  return "❔";
}
const WMO = {0:["Céu limpo","☀️"],1:["Pouco nublado","🌤️"],2:["Parcialmente nublado","⛅"],3:["Nublado","☁️"],
  45:["Nevoeiro","🌫️"],48:["Nevoeiro com geada","🌫️"],51:["Chuvisco fraco","🌦️"],53:["Chuvisco","🌦️"],55:["Chuvisco forte","🌧️"],
  56:["Chuvisco gelado","🌧️"],57:["Chuvisco gelado","🌧️"],61:["Chuva fraca","🌧️"],63:["Chuva","🌧️"],65:["Chuva forte","🌧️"],
  66:["Chuva gelada","🌧️"],67:["Chuva gelada","🌧️"],71:["Neve fraca","❄️"],73:["Neve","❄️"],75:["Neve forte","❄️"],77:["Grãos de neve","❄️"],
  80:["Aguaceiros fracos","🌦️"],81:["Aguaceiros","🌦️"],82:["Aguaceiros fortes","🌧️"],85:["Aguaceiros de neve","🌨️"],86:["Aguaceiros de neve","🌨️"],
  95:["Trovoada","⛈️"],96:["Trovoada com granizo","⛈️"],99:["Trovoada com granizo","⛈️"]};
function wmo(c, dia) {
  const r = WMO[c] || ["—","❔"];
  return (!dia && c <= 1) ? [r[0], "🌙"] : r;
}
const rumo = g => RUMOS[R((g % 360) / 45) % 8];
const ehChuva = c => (c >= 51 && c <= 67) || (c >= 80 && c <= 82) || c >= 95;
const ehNeve = c => (c >= 71 && c <= 77) || c === 85 || c === 86;
const ehNevoa = c => c === 45 || c === 48;
const seta = g => `<svg class="seta" viewBox="0 0 20 20" style="transform:rotate(${(g + 180) % 360}deg)" aria-hidden="true"><path d="M10 1 17 18 10 13 3 18z"/></svg>`;
const minutos = s => +s.slice(11, 13) * 60 + +s.slice(14, 16);

/* ---------- Estado ---------- */
const NDIAS = 5;                        // hoje + 4 dias já previstos
let locais = [], local = null, tiposTempo = {};
let tempo = null, arDados = null, ipmaDias = null, avisosLista = null, avisosAtivos = [], piadas = [], iPiada = 0;
let diaSel = 0;                         // 0 = agora; 1..4 = dias de previsão
let dataSel = null;                     // data do dia escolhido ("AAAA-MM-DD"); null = agora
const pref = { ler(){ try { return localStorage.getItem("meteo_local"); } catch(e){ return null; } },
               gravar(v){ try { localStorage.setItem("meteo_local", v); } catch(e){} } };
const cap = s => s.charAt(0).toUpperCase() + s.slice(1);

async function iniciar() {
  try {
    const [d, t] = await Promise.all([ipma("/open-data/distrits-islands.json"),
                                      ipma("/open-data/weather-type-classe.json")]);
    locais = d.data.sort((a, b) => a.local.localeCompare(b.local, "pt"));
    t.data.forEach(x => tiposTempo[x.idWeatherType] = x.descWeatherTypePT);
  } catch (e) {
    $("estado").textContent = "Não foi possível obter a lista de locais do IPMA.";
    $("fala").textContent = "Não consigo falar com o IPMA. Já bati à porta com o bico e nada.";
    return;
  }
  const sel = $("local");
  locais.forEach(l => { const o = document.createElement("option");
                        o.value = l.globalIdLocal; o.textContent = l.local; sel.appendChild(o); });
  sel.value = pref.ler() || "1110600";               // por omissão: Lisboa
  if (!sel.value) sel.value = locais[0].globalIdLocal;
  sel.onchange = () => { pref.gravar(sel.value); escolher(); };
  $("outra").onclick = () => { if (piadas.length) { iPiada = (iPiada + 1) % piadas.length; $("fala").textContent = piadas[iPiada]; } };
  $("chips").onclick = e => { const b = e.target.closest("[data-k]"); if (b) selecionarDia(+b.dataset.k); };
  $("dias").onclick = e => {
    const b = e.target.closest("[data-k]");
    if (!b) return;
    selecionarDia(+b.dataset.k);
    const calmo = matchMedia("(prefers-reduced-motion: reduce)").matches;
    $("chips").scrollIntoView({behavior: calmo ? "auto" : "smooth", block: "start"});
  };
  escolher();
  setInterval(atualizarTempo, 5 * 60 * 1000);       // dados novos a cada 5 minutos
}

function escolher() {
  local = locais.find(l => String(l.globalIdLocal) === $("local").value);
  atualizarTempo();
}

function selecionarDia(k) {
  diaSel = k;
  dataSel = k && tempo ? tempo.daily.time[k] : null;
  desenhar();
}

async function atualizarTempo() {
  await Promise.allSettled([buscarTempo(), buscarAr(), buscarPrevisao5(), buscarAvisos()]);
  desenhar();
  const t = new Date();
  $("estado").textContent = local.local + " · atualizado às " + p2(t.getHours()) + ":" + p2(t.getMinutes()) +
                            (ipmaDireto ? "" : " · IPMA através do Pico");
}

/* ---------- Buscar dados ---------- */
async function buscarTempo() {
  const u = "https://api.open-meteo.com/v1/forecast?latitude=" + local.latitude + "&longitude=" + local.longitude +
    "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,is_day,wind_speed_10m," +
    "wind_direction_10m,wind_gusts_10m,pressure_msl,precipitation,cloud_cover,dew_point_2m,visibility,uv_index" +
    "&hourly=temperature_2m,apparent_temperature,precipitation_probability,weather_code,is_day,wind_gusts_10m,uv_index," +
    "relative_humidity_2m,dew_point_2m,pressure_msl,cloud_cover,visibility" +
    "&daily=weather_code,sunrise,sunset,uv_index_max,temperature_2m_max,temperature_2m_min,apparent_temperature_max," +
    "apparent_temperature_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max," +
    "wind_direction_10m_dominant,daylight_duration&timezone=auto&forecast_days=6";
  try {
    const r = await fetch(u);
    if (!r.ok) throw new Error("Open-Meteo " + r.status);
    const j = await r.json();
    const i0 = j.hourly.time.findIndex(t => t.slice(0, 13) === j.current.time.slice(0, 13));
    j.i0 = i0 < 0 ? 0 : i0;
    tempo = j;
  } catch (e) {
    tempo = null;
  }
}

async function buscarAr() {
  const v = "european_aqi,pm2_5,pm10,olive_pollen,grass_pollen,birch_pollen";
  const u = "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=" + local.latitude + "&longitude=" + local.longitude +
            "&current=" + v + "&hourly=" + v + "&timezone=auto&forecast_days=5";
  try {
    const r = await fetch(u);
    if (!r.ok) throw new Error("Open-Meteo AQ " + r.status);
    arDados = await r.json();
  } catch (e) {
    arDados = null;
  }
}

async function buscarPrevisao5() {
  try { ipmaDias = (await ipma("/open-data/forecast/meteorology/cities/daily/" + local.globalIdLocal + ".json")).data; }
  catch (e) { ipmaDias = null; }
}

async function buscarAvisos() {
  try { avisosLista = await ipma("/open-data/forecast/warnings/warnings_www.json"); }
  catch (e) { avisosLista = null; }
}

/* ---------- Dias e "vista" do dia escolhido ---------- */
const dataDe = k => new Date(tempo.daily.time[k] + "T12:00:00");
const diaNome = k => k === 0 ? "hoje" : k === 1 ? "amanhã" : DIAS[dataDe(k).getDay()];
const diaLongo = k => k < 2 ? diaNome(k) : diaNome(k) + ", " + dataDe(k).getDate();
const horasDe = (h, data) => h.time.reduce((r, t, i) => (t.slice(0, 10) === data && r.push(i), r), []);
const vals = (arr, idx) => idx.map(i => (arr || [])[i]).filter(x => x != null);   // tolera variáveis em falta
const media = a => a.length ? a.reduce((s, x) => s + x, 0) / a.length : NaN;

// Junta tudo o que as secções precisam num só objeto. Para hoje usa as condições
// atuais; para os outros dias resume a previsão (máximas, médias e totais do dia).
function vistaDe(k) {
  const j = tempo, d = j.daily, h = j.hourly;
  if (k === 0) {
    const c = j.current, idx = [];
    for (let i = j.i0; i < Math.min(j.i0 + 24, h.time.length); i++) idx.push(i);
    return {k, hoje: true, c, s: c.apparent_temperature, chuva: ehChuva(c.weather_code) || c.precipitation > 0,
            tmax: d.temperature_2m_max[0], tmin: d.temperature_2m_min[0], horas: idx, vestir: idx.slice(0, 12),
            pmax: Math.max(0, ...vals(h.precipitation_probability, idx.slice(0, 6)))};
  }
  const idx = horasDe(h, d.time[k]), vis = vals(h.visibility, idx);
  const pmax = d.precipitation_probability_max[k] ?? 0;
  const c = {
    temperature_2m: d.temperature_2m_max[k], apparent_temperature: d.apparent_temperature_max[k],
    weather_code: d.weather_code[k], is_day: 1, precipitation: d.precipitation_sum[k],
    wind_speed_10m: d.wind_speed_10m_max[k], wind_gusts_10m: d.wind_gusts_10m_max[k],
    wind_direction_10m: d.wind_direction_10m_dominant[k], uv_index: d.uv_index_max[k],
    relative_humidity_2m: R(media(vals(h.relative_humidity_2m, idx))), dew_point_2m: media(vals(h.dew_point_2m, idx)),
    pressure_msl: media(vals(h.pressure_msl, idx)), cloud_cover: R(media(vals(h.cloud_cover, idx))),
    visibility: vis.length ? Math.min(...vis) : NaN
  };
  return {k, hoje: false, c, s: c.apparent_temperature, sMin: d.apparent_temperature_min[k],
          chuva: c.precipitation >= 1 || (ehChuva(c.weather_code) && pmax >= 50),
          tmax: d.temperature_2m_max[k], tmin: d.temperature_2m_min[k], pmax, horas: idx,
          vestir: idx.filter(i => { const hh = +h.time[i].slice(11, 13); return hh >= 8 && hh <= 22; })};
}

function desenhar() {
  // O dia escolhido é seguido pela data, não pela posição: depois da meia-noite,
  // "amanhã" passa a ser hoje (volta a "Agora") e os outros dias recuam uma casa.
  const k = tempo && dataSel ? tempo.daily.time.indexOf(dataSel) : 0;
  diaSel = k > 0 && k < NDIAS ? k : 0;
  if (!diaSel) dataSel = null;
  desenharChips();
  const v = tempo ? vistaDe(diaSel) : null;
  if (v) { desenharHeroi(v); desenharTiles(v); desenharHoras(v); desenharSol(v); desenharVestir(v); }
  else $("desc").textContent = "Sem dados do Open-Meteo.";
  desenharAr(v || {k: 0, hoje: true});
  desenharAvisos(v);
  desenharDias();
  falar(v);
}

function desenharChips() {
  if (!tempo) { $("chips").innerHTML = ""; return; }
  const d = tempo.daily, c = tempo.current;
  let s = "";
  for (let k = 0; k < NDIAS; k++) {
    const [, ic] = k === 0 ? wmo(c.weather_code, c.is_day) : wmo(d.weather_code[k], 1);
    const nome = k === 0 ? "Agora" : k === 1 ? "Amanhã" : cap(DIAS_C[dataDe(k).getDay()]) + " " + dataDe(k).getDate();
    s += `<button type="button" class="chip${k === diaSel ? " on" : ""}" data-k="${k}" aria-pressed="${k === diaSel}">` +
         `${ic} ${nome} <small>${R(d.temperature_2m_max[k])}°/${R(d.temperature_2m_min[k])}°</small></button>`;
  }
  $("chips").innerHTML = s;
  const barra = $("chips"), on = barra.querySelector(".chip.on");   // mantém o dia escolhido visível
  if (on) barra.scrollLeft += on.getBoundingClientRect().left - barra.getBoundingClientRect().left -
                             (barra.clientWidth - on.offsetWidth) / 2;
}

/* ---------- Modos: fundo, carimbo e acessórios do Galo Zé ---------- */
function ceuDe(v) {
  const c = v.c, w = c.weather_code, s = v.s;
  if (w >= 95) return "trovoada";
  if (ehNeve(w)) return "neve";
  if (v.chuva) return "chuva";
  if (ehNevoa(w)) return "nevoeiro";
  if (!c.is_day) return "noite";
  if (s >= 28) return "calor";
  if (s <= 10) return "frio";
  if (w >= 3) return "nuvens";
  if (s < 20) return "ameno";
  return "sol";
}

function humor(v) {
  const c = v.c, w = c.weather_code, s = v.s;
  if (w >= 95) return "TROVÕES!";
  if (ehNeve(w)) return "NEVE?!";
  if (v.chuva) return "PINGA-PINGA";
  if (ehNevoa(w)) return "NEVOEIRO";
  if (c.wind_gusts_10m >= 60) return "VENTANIA";
  if (s >= 32) return "MODO GRELHA";
  if (s >= 26) return "CALORZINHO";
  if (s >= 20) return "PERFEITO";
  if (s >= 15) return "AMENO";
  if (s >= 10) return "FRESQUINHO";
  if (s >= 5) return "FRIO";
  return "GELADO";
}

function modosGalo(v) {
  const c = v.c, w = c.weather_code, s = v.s, m = [];
  if (v.chuva) m.push("chuva");
  if (w >= 95) m.push("trovoada");
  if (ehNeve(w)) m.push("neve", "frio");
  if (ehNevoa(w)) m.push("nevoa");
  if (c.wind_gusts_10m >= 45) m.push("vento");
  if (s <= 10) m.push("frio");
  if (!c.is_day && !v.chuva) m.push("noite");
  if (c.is_day && w <= 2 && s >= 18) m.push("sol");
  if (s >= 30) m.push("calor");
  return m;
}

function desenharHeroi(v) {
  const c = v.c, [desc, ic] = wmo(c.weather_code, c.is_day);
  document.body.dataset.ceu = ceuDe(v);
  $("quando").textContent = v.hoje ? "Agora" : "Previsão para " + diaLongo(v.k);
  $("temp").textContent = R(c.temperature_2m) + "°";
  $("desc").textContent = ic + " " + desc;
  if (v.hoje) {
    $("sens").textContent = "Sensação " + R(v.s) + "° · máx " + R(v.tmax) + "° · mín " + R(v.tmin) + "°";
  } else {
    const ip = (ipmaDias || []).find(x => x.forecastDate === tempo.daily.time[v.k]);
    const ipDesc = ip && tiposTempo[+ip.idWeatherType];
    $("sens").textContent = "Máx " + R(v.tmax) + "° · mín " + R(v.tmin) + "° · sensação até " + R(v.s) + "° · chuva " +
                            v.pmax + "%" + (ipDesc ? " · IPMA: " + ipDesc : "");
  }
  $("carimbo").textContent = humor(v);
  $("masc").setAttribute("class", modosGalo(v).join(" "));
  $("cv").style.transform = `rotate(${c.wind_direction_10m}deg)`;
  $("masc").setAttribute("aria-label", `O Galo Zé, cata-vento: vento de ${rumo(c.wind_direction_10m)} ` +
                         (v.hoje ? "a " : "até ") + `${R(c.wind_speed_10m)} km/h`);
}

/* ---------- Detalhe ---------- */
const uvNivel = u => u < 3 ? "Baixo" : u < 6 ? "Moderado" : u < 8 ? "Alto" : u < 11 ? "Muito alto" : "Extremo";

function desenharTiles(v) {
  const c = v.c, d = tempo.daily, vis = c.visibility, h = v.hoje, chuva = d.precipitation_sum[v.k];
  $("tit-tiles").textContent = "🔍 " + cap(diaLongo(v.k)) + " em detalhe";
  const t = [
    [h ? "Vento" : "Vento máx", `${R(c.wind_speed_10m)} <small>km/h</small> ${seta(c.wind_direction_10m)}`,
      `de ${rumo(c.wind_direction_10m)} · ` + (c.wind_speed_10m >= 40 ? "despenteia tudo" : c.wind_speed_10m >= 20 ? "brisa marota" : "quase parado")],
    [h ? "Rajadas" : "Rajadas máx", `${R(c.wind_gusts_10m)} <small>km/h</small>`,
      c.wind_gusts_10m >= 70 ? "Segura-te ao poste!" : c.wind_gusts_10m >= 45 ? "Guarda-chuvas em risco" : "Tranquilas"],
    [h ? "Humidade" : "Humidade média", `${c.relative_humidity_2m}<small>%</small>`,
      c.relative_humidity_2m >= 85 ? "Cabelo em modo frizz" : c.relative_humidity_2m <= 35 ? "Ar seco, bebe água" : "Na boa"],
    [h ? "Ponto de orvalho" : "Orvalho médio", `${R(c.dew_point_2m)}°`,
      c.dew_point_2m >= 20 ? "Abafado" : c.dew_point_2m >= 15 ? "Um pouco pegajoso" : "Confortável"],
    [h ? "Pressão" : "Pressão média", `${R(c.pressure_msl)} <small>hPa</small>`,
      c.pressure_msl >= 1022 ? "Anticiclone a mandar" : c.pressure_msl <= 1005 ? "Depressão (a do tempo)" : "Normalzinha"],
    [h ? "Visibilidade" : "Visibilidade mín", vis >= 10000 ? `10+ <small>km</small>` : vis >= 1000 ? `${R(vis / 1000)} <small>km</small>` : `${R(vis)} <small>m</small>`,
      vis < 1000 ? "Nem vejo a cauda" : vis < 5000 ? "Meio enevoado" : "Vê-se Espanha (quase)"],
    [h ? "Nuvens" : "Nuvens (média)", `${c.cloud_cover}<small>%</small>`,
      c.cloud_cover >= 85 ? "Tampa cinzenta" : c.cloud_cover <= 15 ? "Céu limpinho" : "Umas quantas"],
    h ? ["UV agora", `${R(c.uv_index)}`, `${uvNivel(c.uv_index)} · máx hoje ${R(d.uv_index_max[0])}`]
      : ["UV máx", `${R(c.uv_index)}`, uvNivel(c.uv_index)],
    [h ? "Chuva hoje" : "Chuva prevista", `${chuva} <small>mm</small>`,
      chuva >= 10 ? "Dia de galochas" : chuva > 0 ? "Uns pingos" : "Secura total"],
    h ? ["Agora mesmo", `${c.precipitation} <small>mm</small>`, c.precipitation > 0 ? "Está a cair!" : "Nada a cair"]
      : ["Prob. de chuva", `${v.pmax}<small>%</small>`, v.pmax >= 70 ? "Quase certo" : v.pmax >= 40 ? "Pode calhar" : "Pouco provável"],
  ];
  $("tiles").innerHTML = t.filter(x => !x[1].includes("NaN")).map(x =>     // sem dados → o mosaico não aparece
    `<div class="tile"><span class="rot">${x[0]}</span><b class="fx">${x[1]}</b><span class="nota">${x[2]}</span></div>`).join("");
}

/* ---------- Hora a hora ---------- */
function desenharHoras(v) {
  const h = tempo.hourly;
  $("tit-horas").textContent = "⏰ Hora a hora" + (v.hoje ? "" : " · " + diaLongo(v.k));
  $("horas").innerHTML = v.horas.map((i, n) => {
    const [, ic] = wmo(h.weather_code[i], h.is_day[i]), ja = v.hoje && n === 0;
    return `<div class="h${ja ? " ja" : ""}"><div class="hh">${ja ? "agora" : h.time[i].slice(11, 13) + "h"}</div>
      <i>${ic}</i><b>${R(h.temperature_2m[i])}°</b><div class="pp">💧${h.precipitation_probability[i] ?? 0}%</div></div>`;
  }).join("");
  $("horas").scrollLeft = 0;
  $("grafico").innerHTML = v.horas.length ? grafico(h, v.horas) : "";
}

function grafico(h, idx) {
  const W = Math.max(320, $("grafico").clientWidth || 600), H = 170, top = 24, bot = 26, ch = H - top - bot;
  const T = idx.map(i => h.temperature_2m[i]);
  const P = idx.map(i => h.precipitation_probability[i] ?? 0);
  const tmin = Math.floor(Math.min(...T)) - 1, tmax = Math.ceil(Math.max(...T)) + 1;
  const pad = 16, slot = (W - 2 * pad) / idx.length, x = k => pad + (k + 0.5) * slot;
  const yT = t => top + ch - (t - tmin) / (tmax - tmin) * ch;
  let s = `<svg class="graf" viewBox="0 0 ${W} ${H}" role="img" aria-label="Temperatura e probabilidade de chuva hora a hora">`;
  P.forEach((p, k) => { const bh = p / 100 * ch;
    s += `<rect class="barra" x="${x(k) - slot * 0.35}" y="${top + ch - bh}" width="${slot * 0.7}" height="${bh}" rx="3"/>`; });
  s += `<polyline class="linha" points="${T.map((t, k) => x(k) + "," + yT(t)).join(" ")}"/>`;
  const passo = W < 500 ? 4 : W < 800 ? 3 : 2;
  idx.forEach((i, k) => { if (k % passo === 0) {
    s += `<text class="eixo" x="${x(k)}" y="${H - 6}">${h.time[i].slice(11, 13)}h</text>`;
    s += `<text class="valor" x="${x(k)}" y="${yT(T[k]) - 8}">${R(T[k])}°</text>`; } });
  return s + "</svg>";
}

/* ---------- Sol & Lua ---------- */
function faseLua(ms) {
  const sin = 29.530588853, ref = Date.UTC(2000, 0, 6, 18, 14);
  let idade = ((ms - ref) / 864e5) % sin; if (idade < 0) idade += sin;
  const f = idade / sin, k = R(f * 8) % 8;
  const nomes = ["Lua nova","Lua crescente","Quarto crescente","Gibosa crescente","Lua cheia","Gibosa minguante","Quarto minguante","Lua minguante"];
  const emo = ["🌑","🌒","🌓","🌔","🌕","🌖","🌗","🌘"];
  const piadas = ["Escuro como breu: ótimo para ver estrelas.", "A lua está a ganhar coragem.", "Meia lua, meia dose.",
    "Quase cheia, quase lobisomem.", "Lua cheia: dizem que as marés e os tios ficam agitados.", "A encolher devagarinho.",
    "Meia lua outra vez, mas do outro lado.", "Lua a despedir-se."];
  return {nome: nomes[k], em: emo[k], piada: piadas[k], ilum: R((1 - Math.cos(2 * Math.PI * f)) / 2 * 100)};
}

function desenharSol(v) {
  const j = tempo, d = j.daily, k = v.k, nasc = d.sunrise[k].slice(11), por = d.sunset[k].slice(11);
  const a = minutos(d.sunrise[k]), b = minutos(d.sunset[k]);
  let f = 0.5, dia = true, msg = `Sol de ${diaLongo(k)}: nasce às ${nasc} e põe-se às ${por}.`;
  if (v.hoje) {
    const n = minutos(new Date(Date.now() + j.utc_offset_seconds * 1000).toISOString());
    f = (n - a) / (b - a); dia = f >= 0 && f <= 1;
    const falta = dia ? b - n : (n < a ? a - n : a + 1440 - n);
    msg = dia ? `Faltam ${Math.floor(falta / 60)}h${p2(falta % 60)} para o pôr do sol.`
              : `O sol está a dormir. Acorda daqui a ${Math.floor(falta / 60)}h${p2(falta % 60)}.`;
  }
  const ang = Math.PI * Math.min(Math.max(f, 0), 1);
  const sx = 150 - 125 * Math.cos(ang), sy = 112 - 90 * Math.sin(ang);
  const dur = d.daylight_duration[k], dd = (d.daylight_duration[k + 1] - dur) / 60;
  const lua = faseLua(v.hoje ? Date.now() : dataDe(k).getTime());
  $("tit-sol").textContent = "🌅 Sol & Lua" + (v.hoje ? "" : " · " + diaLongo(k));
  $("sol").innerHTML = `
    <svg viewBox="0 0 300 130" style="width:100%;height:auto" role="img" aria-label="Posição do sol no céu">
      <path d="M25 112A125 90 0 0 1 275 112" fill="none" stroke="#1d1b2f" stroke-width="3" stroke-dasharray="6 7"/>
      <path d="M8 112h284" stroke="#1d1b2f" stroke-width="3" stroke-linecap="round"/>
      ${dia ? `<circle cx="${sx}" cy="${sy}" r="15" fill="#ffd23f" stroke="#1d1b2f" stroke-width="3"/>
               <circle cx="${sx - 5}" cy="${sy - 3}" r="1.8" fill="#1d1b2f"/><circle cx="${sx + 5}" cy="${sy - 3}" r="1.8" fill="#1d1b2f"/>
               <path d="M${sx - 5} ${sy + 4}q5 5 10 0" fill="none" stroke="#1d1b2f" stroke-width="2" stroke-linecap="round"/>`
            : `<text x="150" y="80" font-size="34" text-anchor="middle">${lua.em}</text>`}
    </svg>
    <div class="par"><span>🌅 ${nasc}</span><span>🌇 ${por}</span></div>
    <div class="sub" style="font-weight:600">${msg} Dia com ${Math.floor(dur / 3600)}h${p2(R(dur % 3600 / 60))} de luz` +
      (isNaN(dd) ? "." : `; ${v.hoje ? "amanhã" : "no dia seguinte"} ${dd >= 0 ? "+" : "−"}${Math.abs(R(dd))} min ${dd >= 0 ? "(a crescer!)" : "(a encolher…)"}`) +
    `</div>
    <div class="lua"><span class="em">${lua.em}</span><div><b>${lua.nome}</b> · ${lua.ilum}% iluminada<div class="sub">${lua.piada}</div></div></div>`;
}

/* ---------- O que vestir (hoje: próximas 12 h; outros dias: das 8h às 22h) ---------- */
function desenharVestir(v) {
  const h = tempo.hourly, idx = v.vestir.length ? v.vestir : v.horas, r = [];
  let smin = 99, smax = -99, pmax = 0, gmax = 0, uvmax = 0;
  for (const i of idx) {
    smin = Math.min(smin, h.apparent_temperature[i]); smax = Math.max(smax, h.apparent_temperature[i]);
    pmax = Math.max(pmax, h.precipitation_probability[i] ?? 0); gmax = Math.max(gmax, h.wind_gusts_10m[i]);
    uvmax = Math.max(uvmax, h.uv_index[i] ?? 0);
  }
  $("tit-vestir").textContent = "👕 O que vestir" + (v.hoje ? "" : " · " + diaLongo(v.k));
  $("vestir-quando").textContent = v.hoje ? "Para as próximas 12 horas" : "Para " + diaLongo(v.k) + ", das 8h às 22h";
  const fx = `sensação entre ${R(smin)}° e ${R(smax)}°`;
  const chove = pmax >= 40 || (v.hoje ? v.c.precipitation > 0 : v.chuva), amp = smax - smin;
  // Variedade: cada sugestão tem várias versões. A escolha depende do dia, por isso
  // muda de dia para dia, mas não salta a cada atualização da página.
  let sem = dataDe(v.k).getDate() + dataDe(v.k).getMonth() * 31;
  const p = (...ops) => { const o = ops[sem++ % ops.length]; r.push(o); };
  const uvTxt = `UV até ${R(uvmax)} (${uvNivel(uvmax).toLowerCase()})`;
  // 1) Parte de cima: decide-se pela hora mais fria do período
  if (smin <= 3) p(["🧥", "Casaco quente e camisola grossa", fx + ". Não é altura de armar em forte."],
                   ["🧥", "Kispo e camisola de lã", fx + ". Modo boneco de neve."],
                   ["🧥", "Sobretudo e camisola de gola alta", fx + ". Elegante e quentinho."]);
  else if (smin <= 8) p(["🧥", "Casaco e camisola", fx + ". A avó tinha razão."],
                        ["🧥", "Casaco acolchoado e sweat", fx + ". Fecha o fecho até cima."],
                        ["🧥", "Parka e camisola de malha", fx + ". Conforto acima de tudo."]);
  else if (smin <= 13) p(["🧥", "Blusão ou casaco leve", fx + ". Com uma camisola fina por baixo."],
                         ["🧥", "Casaco de ganga e camisola fina", fx + ". Clássico que nunca falha."],
                         ["🧥", "Gabardina leve e camisa", fx + ". Ar de detetive incluído."]);
  else if (smin <= 17) p(["🧶", "Camisola ou sweat", fx + ". Chega e sobra."],
                         ["🧶", "Camisola fina de malha", fx + ". Nem mais, nem menos."],
                         ["👕", "Camisa de manga comprida e colete", fx + ". O colete é opcional, o estilo não."]);
  else if (smin <= 21) p(["👕", "T-shirt e um casaquinho à mão", fx + ". Para quando arrefecer."],
                         ["👕", "Polo e uma sweat na mochila", fx + ". Mais vale prevenir."],
                         ["👚", "Camisa leve de manga comprida", fx + ". Arregaça as mangas se aquecer."]);
  else p(["👕", "T-shirt ou camisa fresca", fx + ". Tecidos leves, que hoje aquece."],
         ["👕", "Camisa de linho ou t-shirt de algodão", fx + ". Quanto mais leve, melhor."],
         ["🎽", "Top ou t-shirt de alças", fx + ". Ombros ao sol (com protetor!)."]);
  if (amp >= 8 && smin <= 17) p(["🧅", "Veste-te por camadas", `A sensação sobe ${R(amp)}° ao longo do período. Tira e põe à vontade.`],
                                ["🧅", "Modo cebola", `De ${R(smin)}° a ${R(smax)}° de sensação: uma camada para cada hora.`]);
  // 2) Parte de baixo
  if (smax >= 27 && !chove) p(["🩳", "Calções ou saia", `Sensação até ${R(smax)}°. Pernas ao léu.`],
                              ["🩳", "Bermudas ou vestido leve", `Sensação até ${R(smax)}°. As calças ficam em casa.`],
                              ["👗", "Vestido fresco ou calções", `Sensação até ${R(smax)}°. Deixa o ar circular.`]);
  else if (smin <= 3) p(["👖", "Calças quentes", "Das grossas, que o frio entra por baixo."],
                        ["👖", "Calças de bombazine", "Quentinhas e com aquele barulhinho ao andar."],
                        ["👖", "Calças com collants por baixo", "Truque de quem sabe."]);
  else p(["👖", "Calças", smax >= 24 ? "Das fininhas, que vai aquecer." : "As do costume servem."],
         ["👖", "Calças de ganga", smax >= 24 ? "Das leves, que a tarde aquece." : "Dão com tudo."],
         ["👖", smax >= 24 ? "Calças de tecido leve" : "Chinos ou calças de sarja", smax >= 24 ? "Para não cozer as pernas." : "Um toque arranjadinho."]);
  // 3) Calçado
  if (chove && pmax >= 70) p(["🥾", "Botas ou calçado impermeável", "Poças à vista. Meias molhadas é que não."],
                             ["🥾", "Galochas ou botas", "Para saltar nas poças sem remorsos."],
                             ["🥾", "Botins impermeáveis", "Pés secos, dia feliz."]);
  else if (smax >= 28 && !chove) p(["🩴", "Sandálias ou sapatos frescos", "Os pés também merecem arejar."],
                                   ["🩴", "Chinelos ou alpercatas", "Modo férias ativado."],
                                   ["👟", "Sapatilhas de lona", "Leves e fresquinhas."]);
  else if (smin <= 5) p(["🥾", "Botas ou sapatos forrados", "Com meias grossas, já agora."],
                        ["🥾", "Botins quentes", "Dedos dos pés agradecem."],
                        ["👟", "Sapatilhas e meias grossas", "As meias fazem metade do trabalho."]);
  else p(["👟", "Sapatilhas ou sapatos fechados", chove ? "Fechados, que pode pingar." : "Confortáveis e está feito."],
         ["👟", "Ténis do dia a dia", chove ? "Evita os de pano, que pode pingar." : "Prontos para tudo."],
         ["👞", "Sapatos ou mocassins", chove ? "Cuidado com as poças." : "Para andar bem composto."]);
  // 4) Acessórios
  if (smin <= 5) p(["🧣", "Cachecol e gorro", "As orelhas agradecem."],
                   ["🧣", "Cachecol e carapuço", "Pescoço tapado, constipação evitada."],
                   ["🧣", "Gola e gorro de lã", "Só se veem os olhos, e ainda bem."]);
  if (smin <= 2) p(["🧤", "Luvas", "Mãos nos bolsos não conta."], ["🧤", "Luvas quentes", "Para conseguires mexer no telemóvel, tira só uma."]);
  if (chove && gmax >= 50) p(["🧥", "Impermeável com capuz", `Até ${pmax}% de chuva e rajadas de ${R(gmax)} km/h: o guarda-chuva vira-se do avesso.`],
                             ["🧥", "Capa de chuva", `Rajadas de ${R(gmax)} km/h e ${pmax}% de chuva. O guarda-chuva não sobrevive.`]);
  else if (chove) p(["☂️", "Guarda-chuva", `Até ${pmax}% de probabilidade de chuva.`],
                    ["☂️", "Chapéu de chuva", `${pmax}% de probabilidade de pingar. Não o deixes no café.`],
                    ["🌂", "Guarda-chuva dobrável", `Até ${pmax}% de chuva. Cabe na mochila.`]);
  else if (gmax >= 45) p(["🌬️", "Corta-vento", `Rajadas até ${R(gmax)} km/h. Segura o chapéu.`],
                         ["🌬️", "Casaco corta-vento", `Rajadas até ${R(gmax)} km/h. Penteados em risco.`]);
  if (uvmax >= 8) p(["🧢", "Chapéu ou boné", uvTxt + ". Cabeça à sombra."],
                    ["👒", "Chapéu de aba larga", uvTxt + ". Sombra portátil."],
                    ["🧢", "Boné ou panamá", uvTxt + ". A careca agradece."]);
  if (uvmax >= 6) p(["🧴", "Protetor solar", uvTxt + ". Ninguém quer ficar cor de camarão."],
                    ["🧴", "Protetor solar (e repõe!)", uvTxt + ". De duas em duas horas."],
                    ["🧴", "Creme protetor", uvTxt + ". Nariz e orelhas também contam."]);
  if (uvmax >= 3 && pmax < 40) p(["😎", "Óculos de sol", `UV até ${R(uvmax)}. Estilo garantido.`],
                                 ["😎", "Óculos escuros", `UV até ${R(uvmax)}. Ar de estrela de cinema.`],
                                 ["😎", "Óculos de sol", `UV até ${R(uvmax)}. Os olhos também se queimam.`]);
  $("vestir").innerHTML = r.map(x => `<li><span class="e">${x[0]}</span><div>${x[1]}<small>${x[2]}</small></div></li>`).join("");
}

/* ---------- Qualidade do ar e pólen (Open-Meteo / CAMS) ---------- */
const AQI = [[20,"Bom","#50f0e6","#1d1b2f"],[40,"Razoável","#50ccaa","#1d1b2f"],[60,"Moderado","#f0e641","#1d1b2f"],
             [80,"Mau","#ff5050","#1d1b2f"],[100,"Muito mau","#960032","#fff"],[1e9,"Péssimo","#7d2181","#fff"]];
function nivelPolen(v) { return v == null ? 0 : v < 10 ? 1 : v < 50 ? 2 : v < 200 ? 3 : 4; }

function arDe(v) {      // hoje: valores atuais; outros dias: o máximo previsto para esse dia
  if (!arDados) return null;
  if (v.hoje) return arDados.current;
  const h = arDados.hourly, idx = horasDe(h, tempo.daily.time[v.k]);
  const mx = n => { const a = vals(h[n], idx); return a.length ? Math.max(...a) : null; };
  return {european_aqi: mx("european_aqi"), pm2_5: mx("pm2_5"), pm10: mx("pm10"),
          olive_pollen: mx("olive_pollen"), grass_pollen: mx("grass_pollen"), birch_pollen: mx("birch_pollen")};
}

function desenharAr(v) {
  const c = arDe(v);
  $("tit-ar").textContent = "🌬️ Ar & pólen" + (v.hoje ? "" : " · " + diaLongo(v.k));
  if (!c || c.european_aqi == null) {
    $("ar").textContent = v.hoje ? "Sem dados de qualidade do ar." : "Sem previsão de qualidade do ar para este dia.";
    return;
  }
  const q = c.european_aqi, b = AQI.find(x => q <= x[0]);
  const comico = q <= 20 ? "Ar de serra. Respira fundo!" : q <= 40 ? "Respira-se bem." : q <= 60 ? "Assim-assim, como café requentado."
               : (v.hoje ? "Hoje o ar está pesado." : "O ar vai estar pesado.") + " Exercício intenso? Melhor não.";
  const nomes = ["—","baixo","moderado","alto","muito alto"];
  const pol = [["🫒 Oliveira", c.olive_pollen], ["🌾 Gramíneas", c.grass_pollen], ["🌳 Bétula", c.birch_pollen]].map(([n, x]) => {
    const l = nivelPolen(x);
    return `<span>${n}</span><span class="met">${[1,2,3,4].map(k => `<span class="${k <= l ? "on" : ""}"></span>`).join("")}</span>
            <span class="sub">${x == null ? (v.hoje ? "fora de época" : "sem previsão") : nomes[l]}</span>`;
  }).join("");
  $("ar").innerHTML = `<div class="aqi"><div class="n" style="background:${b[2]};color:${b[3]}">${R(q)}</div>
    <div><b>${b[1]}</b> <span class="sub">${v.hoje ? "(índice europeu)" : "(máximo previsto)"}</span>
    <div class="sub">PM2.5 ${R(c.pm2_5)} · PM10 ${R(c.pm10)} µg/m³</div>
    <div style="font-weight:700">${comico}</div></div></div><div class="polen">${pol}</div>`;
}

/* ---------- Previsão 5 dias (IPMA) ---------- */
function legendaDia(d, id) {
  const p = +d.precipitaProb, tx = +d.tMax, tn = +d.tMin;
  if ([19,20,23].includes(id)) return "Trovões à vista";
  if ([18,28,29,30].includes(id)) return "Neve à vista";
  if (p >= 70) return "Dia de galochas";
  if (p >= 40) return "Talvez pingue";
  if (+d.classWindSpeed >= 3) return "Ventania";
  if ([16,17,26].includes(id)) return "Dia de nevoeiro";
  if (tx >= 32) return "Modo forno";
  if (tx >= 26) return "Cheira a praia";
  if (tn <= 3) return "Geadinha";
  if (tx >= 20) return id <= 2 ? "Sol sem desculpas" : "Perfeitinho";
  if (tx >= 15) return "Ameno";
  if (tx >= 10) return "Fresquinho";
  return "Frio de rachar";
}

function desenharDias() {
  if (!ipmaDias) { $("dias").textContent = "Sem previsão do IPMA."; return; }
  $("dias").innerHTML = ipmaDias.map(d => {
    const dt = new Date(d.forecastDate + "T12:00:00"), id = Number(d.idWeatherType);
    const k = tempo ? tempo.daily.time.indexOf(d.forecastDate) : -1, ok = k >= 0 && k < NDIAS;
    const tag = ok ? "button" : "div";
    return `<${tag} class="dia${ok && k === diaSel ? " on" : ""}"${ok ? ` type="button" data-k="${k}" aria-pressed="${k === diaSel}"` : ""} title="${esc(tiposTempo[id])}">
      <span class="nome">${DIAS_C[dt.getDay()]} ${dt.getDate()}</span>
      <span class="ic">${iconeIPMA(id)}</span>
      <span class="mm">${R(d.tMax)}° <small>${R(d.tMin)}°</small></span>
      <span class="extra">💧 ${R(d.precipitaProb)}% · ${esc(d.predWindDir)} ${VENTO_IPMA[d.classWindSpeed] || ""}</span>
      <span class="piada">${legendaDia(d, id)}</span>
    </${tag}>`;
  }).join("");
}

/* ---------- Avisos (IPMA) ---------- */
const NIVEL = {yellow:"Amarelo", orange:"Laranja", red:"Vermelho"};
const fmt = s => s.slice(8, 10) + "/" + s.slice(5, 7) + " " + s.slice(11, 16);

function desenharAvisos(v) {
  const el = $("avisos"), outro = v && !v.hoje;
  avisosAtivos = [];
  $("tit-avisos").textContent = "⚠️ Avisos IPMA" + (outro ? " · " + diaLongo(v.k) : "");
  if (!avisosLista) { el.className = "sub"; el.textContent = "Sem dados de avisos."; return; }
  const agoraISO = new Date().toISOString().slice(0, 16), data = outro ? tempo.daily.time[v.k] : null;
  avisosAtivos = avisosLista.filter(w => w.idAreaAviso === local.idAreaAviso && w.awarenessLevelID !== "green" &&
    w.endTime.slice(0, 16) > agoraISO &&
    (!data || (w.startTime.slice(0, 16) < data + "T23:59" && w.endTime.slice(0, 16) > data + "T00:00")));
  if (!avisosAtivos.length) {
    el.className = "sub";
    el.textContent = outro ? "Sem avisos para este dia (o IPMA só emite avisos para os próximos 3 dias)."
                           : "Sem avisos para esta zona. O Galo Zé está descansado.";
    return;
  }
  el.className = "";
  el.innerHTML = avisosAtivos.map(w =>
    `<div class="aviso ${esc(w.awarenessLevelID)}" title="${esc(w.text)}">
       ${NIVEL[w.awarenessLevelID] || esc(w.awarenessLevelID)} · ${esc(w.awarenessTypeName)} · ${fmt(w.startTime)} → ${fmt(w.endTime)}
     </div>`).join("");
}

/* ---------- O Galo Zé fala (piadas com números reais) ---------- */
function piadasAgora(v, U, L) {      // U = mau tempo (dito primeiro); L = o resto
  const c = v.c, d = tempo.daily, t = R(c.temperature_2m), s = R(v.s), w = c.weather_code;
  if (w >= 95) U.push(`Trovoada em ${local.local}! Até eu, que canto às 6 da manhã, fico caladinho.`);
  if (ehNeve(w)) U.push(`Neve com ${t}°?! Tenho as patas geladas e as penas em pé.`);
  if (v.chuva) U.push(`Está a chover (${c.precipitation} mm agora). Galo molhado vira canja: leva guarda-chuva!`);
  else if (v.pmax >= 50) L.push(`${v.pmax}% de probabilidade de chuva nas próximas 6 horas. Depois não digas que não avisei!`);
  if (ehNevoa(w)) L.push(`Nevoeiro em ${local.local}… se calhar é hoje que o D. Sebastião volta.`);
  if (c.wind_gusts_10m >= 50) U.push(`Rajadas de ${R(c.wind_gusts_10m)} km/h! Como bom cata-vento, já dei três voltas e estou tonto.`);
  if (c.wind_speed_10m >= 8 && c.wind_gusts_10m < 50)
    L.push(`Vento de ${rumo(c.wind_direction_10m)} a ${R(c.wind_speed_10m)} km/h. Sei disto porque sou, literalmente, um cata-vento.`);
  if (s >= 32) L.push(`Sensação de ${s}°. Mais um bocadinho e passo de galo a frango de churrasco.`);
  else if (s >= 26) L.push(`${t}° lá fora. Cheira a praia, a protetor solar e a bola de Berlim.`);
  else if (s >= 20) L.push(`${t}° e ${WMO[w] ? WMO[w][0].toLowerCase() : "assim-assim"}. Tempo perfeito para cantar à janela.`);
  else if (s >= 15) L.push(`${t}°, ameno: nem frio nem calor, mesmo como eu gosto.`);
  else if (s >= 10) L.push(`${t}°, sensação de ${s}°. Casaquinho leve, que a avó tem sempre razão.`);
  else if (s >= 5) L.push(`Sensação de ${s}°! Estou com pele de galinha… literalmente.`);
  else L.push(`Sensação de ${s}°?! Isto já não é frio, é congelador. Nem canto, para não gelar o bico.`);
  if (!c.is_day && w <= 1) L.push(`Céu limpo e ${t}°. Eu vou dormir, que amanhã canto às 6. Tu aproveita as estrelas.`);
  if (d.uv_index_max[0] >= 7) L.push(`UV máximo de ${R(d.uv_index_max[0])} hoje. Protetor, senão ficas da cor de um camarão.`);
  if (c.relative_humidity_2m >= 90) L.push(`Humidade a ${c.relative_humidity_2m}%. Tenho as penas todas encaracoladas.`);
  if (c.relative_humidity_2m <= 30) L.push(`Humidade de só ${c.relative_humidity_2m}%. Estou seco como bacalhau. Bebe água!`);
  if (v.tmax - v.tmin >= 12) L.push(`Hoje vai de ${R(v.tmin)}° a ${R(v.tmax)}°. Veste-te à cebola: camadas!`);
}

function piadasDia(v, U, L) {
  const c = v.c, k = v.k, t = R(v.tmax), n = R(v.tmin), s = R(v.s), w = c.weather_code;
  const q = k === 1 ? "amanhã" : "para " + diaNome(k), Q = cap(q);
  if (w >= 95) U.push(`${Q} há trovoada à vista em ${local.local}. Vou passar o dia escondido no galinheiro.`);
  if (ehNeve(w)) U.push(`Neve prevista ${q}?! Vou já tirar o gorro da gaveta.`);
  if (v.chuva) U.push(`${Q} vai chover (${c.precipitation} mm previstos, ${v.pmax}% de probabilidade). Galo molhado vira canja: guarda-chuva na mochila!`);
  else if (v.pmax >= 40) L.push(`${Q} há ${v.pmax}% de probabilidade de chuva. Depois não digas que não avisei!`);
  if (ehNevoa(w)) L.push(`${Q} há nevoeiro previsto em ${local.local}… será desta que o D. Sebastião volta?`);
  if (c.wind_gusts_10m >= 50) U.push(`${Q} as rajadas podem chegar aos ${R(c.wind_gusts_10m)} km/h. Já estou a treinar piruetas de cata-vento.`);
  if (s >= 32) L.push(`Máxima de ${t}° ${q} (sensação de ${s}°). Vou passar de galo a frango de churrasco.`);
  else if (s >= 26) L.push(`${Q} sobe até aos ${t}°. Cheira a praia e a bola de Berlim!`);
  else if (s >= 20) L.push(`${t}° de máxima ${q}. Tempo perfeito para cantar à janela.`);
  else if (s >= 15) L.push(`${Q} vai estar ameno, entre ${n}° e ${t}°. Nem frio nem calor, como eu gosto.`);
  else if (s >= 10) L.push(`${Q} fica fresquinho, de ${n}° a ${t}°. Casaquinho leve, que a avó tem sempre razão.`);
  else if (s >= 5) L.push(`Só ${t}° de máxima ${q}. Pele de galinha garantida… literalmente.`);
  else L.push(`Máxima de ${t}° ${q}?! Isso é congelador. Nem canto, para não gelar o bico.`);
  if (v.tmin <= 3) L.push(`Mínima de ${n}° ${q}. Cheira-me a geada: cuidado com o vidro do carro de manhã.`);
  if (c.uv_index >= 7) L.push(`UV até ${R(c.uv_index)} ${q}. Protetor, senão ficas da cor de um camarão.`);
  if (v.tmax - v.tmin >= 12) L.push(`${Q} vai de ${n}° a ${t}°. Veste-te à cebola: camadas!`);
}

function falar(v) {
  const A = [], U = [], L = [];        // avisos, mau tempo, o resto (por esta ordem de prioridade)
  if (avisosAtivos.length) {
    const w = avisosAtivos[0];
    A.push(`Agora a sério: há aviso ${(NIVEL[w.awarenessLevelID] || "").toLowerCase()} de ${w.awarenessTypeName.toLowerCase()} ` +
           (v && !v.hoje ? `de ${fmt(w.startTime)} ` : "") + `até ${fmt(w.endTime)}. Tem cuidado!`);
  }
  if (v) (v.hoje ? piadasAgora : piadasDia)(v, U, L);
  const a = arDe(v || {k: 0, hoje: true});
  if (a && a.european_aqi > 60)
    L.push(v && !v.hoje ? `Qualidade do ar prevista até ${R(a.european_aqi)} nesse dia. Corre só atrás do autocarro.`
                        : `Índice de qualidade do ar a ${R(a.european_aqi)}. Hoje corre só atrás do autocarro.`);
  piadas = [...A, ...U, ...L];
  if (!piadas.length) piadas.push("Hoje não tenho dados nem piadas. Volta daqui a pouco!");
  // Com avisos ou mau tempo, o Galo Zé começa pelo mais importante; senão, escolhe uma ao acaso.
  iPiada = A.length || U.length ? 0 : Math.floor(Math.random() * piadas.length);
  $("fala").textContent = piadas[iPiada];
}

iniciar();
</script>
</body>
</html>"""
HTML = HTML.encode()
gc.collect()

# ---------- Servidor ----------
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("0.0.0.0", 80))
srv.listen(4)
srv.settimeout(5)      # permite voltar ao loop para ressincronizar o NTP

while True:
    if time.time() - ultima_sync > 3600:
        sincronizar()
        ultima_sync = time.time()

    try:
        cliente, addr = srv.accept()
    except OSError:
        continue

    try:
        # O browser abre ligações "de reserva" que muitas vezes nunca enviam nada.
        # Esperamos só 2 s pelo pedido e fechamo-las em silêncio, para não
        # prender o servidor (que só atende um pedido de cada vez).
        cliente.settimeout(2)
        try:
            pedido = cliente.recv(1024)
        except OSError:                  # ETIMEDOUT / ECONNRESET: ligação vazia
            continue                     # o finally fecha a ligação
        if not pedido:                   # o browser fechou sem pedir nada
            continue
        cliente.settimeout(10)           # para enviar a resposta há mais tempo
        pedido = pedido.decode()
        caminho = pedido.split(" ")[1] if " " in pedido else "/"

        if caminho == "/":
            responder(cliente, "200 OK", "text/html; charset=utf-8", HTML)
        elif caminho == "/hora":
            a, m, d, h, mi, s = hora_local()[:6]
            responder(cliente, "200 OK", "application/json",
                      '{"a":%d,"m":%d,"d":%d,"h":%d,"mi":%d,"s":%d}' % (a, m, d, h, mi, s))
        elif caminho.startswith("/ipma/"):
            proxy_ipma(cliente, caminho[5:])
        else:
            responder(cliente, "404 Not Found", "text/plain", "404")
    except Exception as e:
        print("Erro:", e)
    finally:
        cliente.close()
        gc.collect()
