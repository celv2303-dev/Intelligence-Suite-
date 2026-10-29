# -*- coding: utf-8 -*-
"""
CORRIDATV — Panel de picks hipicos + despacho verificado a Telegram.

Base (se mantiene de la version anterior):
  - Secretos fuera del codigo (variables de entorno de Render).
  - Entrega verificada: se lee la respuesta de Telegram, se guarda el message_id
    y se reintenta una vez ante fallos transitorios. Banner verde solo si Telegram confirmo.
  - Panel y endpoints protegidos con WEBHOOK_SECRET.
  - parse_mode HTML con escapado.
  - Backstop anti-fuga de datos internos.
  - Registro CSV escrito despues del envio (solo historial).

Embudo de venta (copia del modelo de marketing de TIPSELVERDE, adaptado a hipica):
  1. Programacion del dia  -> banner "MENU DEL DIA" + "PROGRAMACION DEL <DIA>" con etiquetas y horas.
  2. Nuevo pronostico      -> GIF + pick con etiqueta, hipodromo, carrera, caballo, cuota y stake.
  3. Resultado verde       -> foto del ticket + "✅✅✅✅¡ETIQUETA!✅✅✅✅" + texto, y se fija en el canal.
  4. Venta del VIP         -> "NOS JUGAMOS...", "si veo reacciones...", "si ven este mensaje...".
  5. Testimonio / foto     -> captura de un seguidor con texto libre.

El pago del VIP es en USDT red BSC (BEP20) con el QR de binance_qr.png, desde Binance u otro exchange.
Las senales gratis se controlan por ETIQUETA (PARADA FREE 1 / PARADA FREE 2), no por un contador,
asi que un reinicio de Render no afecta nada.
"""
import os
import csv
import hmac
import html
import json
import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

import requests
from flask import Flask, request, jsonify, render_template_string, Response

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # fotos de hasta 10 MB

# ==========================================
# CONFIGURACION — TODO DESDE EL ENTORNO
# ==========================================
def _env(nombre, defecto=""):
    return os.environ.get(nombre, defecto).strip()


TELEGRAM_TOKEN = _env("TELEGRAM_TOKEN")
CHAT_ID_GRATIS = _env("CHAT_ID_GRATIS")
CHAT_ID_VIP = _env("CHAT_ID_VIP")
WEBHOOK_SECRET = _env("WEBHOOK_SECRET")

WHATSAPP_PHONE = _env("WHATSAPP_PHONE")
WHATSAPP_API_KEY = _env("WHATSAPP_API_KEY")

# --- Marca ---
CANAL_NOMBRE = _env("CANAL_NOMBRE") or "CORRIDATV"
# Donde llegan los mensajes de informacion del VIP, reclamos y afiliacion
CONTACTO_INFO = _env("CONTACTO_INFO") or "https://t.me/Intelligence_suite_gratis"
# Donde se manda el comprobante de pago (si no se configura, es el mismo CONTACTO_INFO)
VIP_LINK = _env("VIP_LINK") or CONTACTO_INFO
# Firma del canal: va en la descripcion de Telegram y al pie del menu del dia
# Firma al pie del menu del dia (y descripcion del canal)
FIRMA_MENU = (
    "ESTO NO ES CORAZONADA\n"
    "⏱️ HORAS DE ESTUDIO ✅\n"
    "👁️ SEGUIMIENTO ✅\n"
    "🎥 VIDEO ✅\n"
    "🏇 EJERCICIOS ✅\n"
    "🐎 ESTABLO VIRTUAL ✅\n"
    "👥 MANEJADO POR UN GRAN EQUIPO"
)
DESCRIPCION_CANAL = _env("DESCRIPCION_CANAL") or FIRMA_MENU
BONO_LINK = _env("BONO_LINK")               # link de afiliado (opcional, por ahora vacio)

# --- Pago con Binance ---
# El QR se toma de un archivo binance_qr.png / .jpg subido al repositorio,
# o de BINANCE_QR (URL publica o file_id de Telegram).
BINANCE_QR = _env("BINANCE_QR")
BINANCE_PAY_ID = _env("BINANCE_PAY_ID")     # opcional: se muestra junto al QR
# Deposito de USDT (el QR de binance_qr.png apunta a esta direccion)
USDT_RED = _env("USDT_RED") or "BSC (BEP20)"
# Precio del ESPECIAL (sale entre parentesis en el menu y en la venta)
VALOR_ESPECIAL = _env("VALOR_ESPECIAL") or "$4.99"
# Precio de TODA la informacion del dia en el VIP (super promo)
PRECIO_DIA = _env("PRECIO_DIA") or "$15.99"
# Precio de todos los caballos de valor juntos (una sola promocion)
PRECIO_VALORES = _env("PRECIO_VALORES") or "$9.99"
# Zona horaria de las horas que vienen en tu menu (hipodromos de EE. UU. = hora del Este)
ZONA_MENU = _env("ZONA_MENU") or "America/New_York"
USDT_DIRECCION = _env("USDT_DIRECCION") or "0x45d608619ea45555d3c458fb178113f7d96c109a"
BONO_TEXTO = _env("BONO_TEXTO") or "Bono de bienvenida AQUÍ"
ZONA_HORARIA = _env("ZONA_HORARIA") or "America/Caracas"

# --- Media opcional (URL publica o file_id de Telegram) ---
IMG_MENU_DEL_DIA = _env("IMG_MENU_DEL_DIA")          # banner "MENU DEL DIA"
GIF_NUEVO_PRONOSTICO = _env("GIF_NUEVO_PRONOSTICO")  # GIF antes de cada pick
GIF_GRATIS = _env("GIF_GRATIS")                      # GIF antes de la apuesta gratis (si no, usa el anterior)

NOTA_HORARIOS = _env("NOTA_HORARIOS") or (
    "⏰ Horas de Venezuela 🇻🇪\n"
    "🇨🇴🇵🇪🇪🇨 1 hora menos\n"
    "🇲🇽 2 horas menos\n"
    "🇦🇷🇨🇱 1 hora más"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRO_PATH = os.path.join(BASE_DIR, "registro.csv")

TELEGRAM_API = "https://api.telegram.org/bot{token}/{metodo}"
TIMEOUT_SEG = 30
MAX_INTENTOS = 2
LIMITE_CAPTION = 1024  # Telegram corta el texto de una foto en 1024 caracteres

ERRORES_PERMANENTES = {400, 401, 403, 404}

PATRONES_PROHIBIDOS = (
    "ficha_tecnica",
    "ficha tecnica",
    "ficha técnica",
    "p registrada",
    "prob registrada",
    "no selecciona",
)

# ==========================================
# ETIQUETAS (edita aqui: nombre, grupo por defecto, emoji)
# ==========================================
ETIQUETAS = [
    {"nombre": "PARADA FREE 1", "destino": "gratis", "emoji": "🎁"},
    {"nombre": "PARADA FREE 2", "destino": "gratis", "emoji": "🎁"},
    {"nombre": "ESPECIAL", "destino": "vip", "emoji": "🌟"},
    {"nombre": "ESPECIAL ALTO DIVIDENDO", "destino": "vip", "emoji": "💎💰"},
    {"nombre": "STAKAZO 15", "destino": "vip", "emoji": "💎"},
    {"nombre": "RATAS DEL DÍA", "destino": "vip", "emoji": "🐀"},
    {"nombre": "RATA FUERTE", "destino": "vip", "emoji": "🐀🔥"},
    {"nombre": "BEST BET DEL DÍA", "destino": "vip", "emoji": "🏆"},
    {"nombre": "BEST BET DEL HIPÓDROMO", "destino": "vip", "emoji": "🏆"},
]
ETIQUETAS_POR_NOMBRE = {e["nombre"]: e for e in ETIQUETAS}

# {valor} se reemplaza por el "Valor del dia" que pongas en el panel.
# Borra en el panel las lineas que no tengas ese dia (rata fuerte, best bet...).
PROGRAMACION_DEFECTO = (
    "PARADA FREE 1 | 13:00h\n"
    "PARADA FREE 2 | 15:00h\n"
    "ESPECIAL ({valor}) | 16:00h\n"
    "STAKAZO 15 | 17:00h\n"
    "RATAS DEL DÍA | 18:00h\n"
    "RATA FUERTE | 19:00h\n"
    "BEST BET DEL DÍA | 20:00h"
)

TIPOS_APUESTA = ["1 puesto", "2 puesto", "3 puesto", "4 puesto", "5 puesto", "2 nini", "5 nini",
                 "Ganador", "Exacta", "Trifecta", "Superfecta"]

DIAS = ["LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO"]

# ==========================================
# TEXTOS DE VENTA (copiados del modelo; {oferta} {monto} {link} se rellenan)
# ==========================================
PLANTILLAS_VENTA = {
    "especial": {
        "nombre": "🌟 ESPECIAL del día (con QR)",
        "texto": (
            "🌟<b>ESPECIAL{linea_valor}</b>🌟\n\n"
            "{linea_resena}"
            "👉<b>LA PARADA FUERTE DE LA JORNADA!</b>🔥\n"
            "👉<b>SOLO PARA LOS QUE ENTREN HOY!</b>💰\n"
            "👉<b>PLAZAS MUY LIMITADAS!</b>🎟\n\n"
            "{link}"
            "🏃... Paga en USDT con el QR (desde Binance u otro exchange), manda tu comprobante y recibes el ESPECIAL antes de la carrera."
        ),
    },
    "principal": {
        "nombre": "\U0001F449 NOS JUGAMOS... (venta principal)",
        "texto": (
            "{linea_monto}"
            "\U0001F449<b>EL REGALO DE LA JORNADA!</b>\U0001F4B0\n"
            "\U0001F449<b>DISPONIBLE EN TODAS LAS CASAS!</b>\U0001F4AA\n"
            "\U0001F449<b>PLAZAS MUY LIMITADAS!</b>\U0001F39F\n\n"
            "{link}"
            "\U0001F3C3... Para gente inversora, gente amante del dinero, "
            "la vamos a liar mucho, avisados están.{linea_oferta}"
        ),
    },
    "reacciones": {
        "nombre": "\U0001F525 Si veo muchas reacciones...",
        "texto": "Si veo muchas reacciones voy a dejar el <b>{oferta}</b> \U0001F525",
    },
    "cuota": {
        "nombre": "\U0001F4C9 La cuota baja (por qué pocas plazas)",
        "texto": (
            "Cuando suelto una apuesta por aquí, la cuota baja al momento.\n\n"
            "Por eso prefiero que pocas personas accedan a una apuesta top y con la cuota intacta."
            "{link_final}"
        ),
    },
    "ultimo": {
        "nombre": "⏳ Si ven este mensaje... (último aviso)",
        "texto": (
            "⏳ Si ven este mensaje significa que todavía pueden entrar al <b>{oferta}</b>, "
            "borraré el acceso cuando empiece la carrera."
            "{link_final}"
        ),
    },
    "pago": {
        "nombre": "💳 Cómo pagar (USDT · QR)",
        "texto": (
            "💳 <b>CÓMO ENTRAR AL VIP DE {canal}</b>{linea_oferta}\n\n"
            "1️⃣ Envía <b>USDT</b> por la red <b>{red}</b>: escanea el QR o copia la dirección:\n"
            "<code>{direccion}</code>\n"
            "   Puedes pagar desde Binance o desde cualquier otro exchange o billetera.{linea_pay_id}\n"
            "2️⃣ ⚠️ Usa SOLO la red {red}. Si envías por otra red, el pago se pierde.\n"
            "3️⃣ Manda la captura del comprobante{linea_contacto}.\n"
            "4️⃣ Te damos acceso al momento ✅\n\n"
            "🎟 Plazas limitadas."
        ),
    },
    "libre": {
        "nombre": "✏️ Texto libre",
        "texto": "{texto_libre}",
    },
}

TEXTO_VERDE_DEFECTO = (
    "¡{etiqueta} DENTROOOOO! {detalle_caballo}"
    "La lectura ha sido muy buena. Gracias a todos por siempre confiar. ¡Seguimos trabajando!"
)


# ==========================================
# AUTENTICACION
# ==========================================
def auth_ok() -> bool:
    if not WEBHOOK_SECRET:
        return False
    enviado = request.headers.get("X-Webhook-Secret", "")
    if enviado and hmac.compare_digest(enviado, WEBHOOK_SECRET):
        return True
    cred = request.authorization
    if cred and cred.password and hmac.compare_digest(cred.password, WEBHOOK_SECRET):
        return True
    return False


def respuesta_no_autorizado():
    if not WEBHOOK_SECRET:
        return jsonify({
            "ok": False,
            "error": "WEBHOOK_SECRET no esta configurado en el servidor. "
                     "Configuralo en las variables de entorno de Render."
        }), 503
    if request.is_json:
        return jsonify({"ok": False, "error": "No autorizado."}), 401
    return Response("Acceso restringido.", 401,
                    {"WWW-Authenticate": 'Basic realm="Intelligence Suite"'})


# ==========================================
# PANEL WEB
# ==========================================
PAGINA_INICIO = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Panel - {{ canal }}</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #0f172a; margin: 0; padding: 20px; display: flex; justify-content: center; color: #f8fafc; }
        .container { background: #1e293b; padding: 25px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); width: 100%; max-width: 560px; }
        h2 { text-align: center; color: #4ade80; margin-bottom: 20px; }
        details { background: #0f172a; border: 1px solid #334155; border-radius: 8px; margin-top: 14px; padding: 4px 14px 14px; }
        summary { cursor: pointer; font-weight: bold; font-size: 16px; padding: 10px 0; color: #e2e8f0; }
        label { font-weight: bold; color: #cbd5e1; display: block; margin-top: 12px; font-size: 13px; }
        .fila { display: flex; gap: 10px; }
        .fila > div { flex: 1; }
        input, select, textarea { width: 100%; padding: 11px; margin-top: 5px; background: #334155; border: 1px solid #475569; border-radius: 6px; color: white; font-size: 14px; box-sizing: border-box; font-family: inherit; }
        input[type=checkbox] { width: auto; margin-right: 8px; }
        .check { display: flex; align-items: center; font-weight: normal; }
        button { width: 100%; color: white; padding: 13px; border: none; border-radius: 6px; font-size: 15px; font-weight: bold; margin-top: 18px; cursor: pointer; background: #16a34a; }
        button:hover { filter: brightness(0.9); }
        .b-azul { background: #0284c7; } .b-morado { background: #9333ea; } .b-naranja { background: #ea580c; } .b-gris { background: #475569; }
        .footer { text-align: center; margin-top: 22px; font-size: 11px; color: #64748b; }
        .alerta-exito { background: #16a34a; color: white; padding: 12px; border-radius: 6px; text-align: center; font-weight: bold; margin-bottom: 15px; }
        .alerta-error { background: #b91c1c; color: white; padding: 12px; border-radius: 6px; text-align: left; font-weight: bold; margin-bottom: 15px; }
        .detalle { font-weight: normal; font-size: 12px; margin-top: 8px; line-height: 1.5; font-family: monospace; }
        .aviso-config { background: #b45309; color: white; padding: 12px; border-radius: 6px; font-size: 13px; margin-bottom: 15px; }
        .ayuda { font-size: 12px; color: #94a3b8; margin-top: 4px; }
    </style>
</head>
<body>
    <div class="container">
        <h2>&#127943; {{ canal }}</h2>

        {% if entregado_todo %}<!-- ENTREGA_CONFIRMADA -->{% elif detalle %}<!-- ENTREGA_FALLIDA -->{% endif %}

        {% if faltantes %}
        <div class="aviso-config">&#9888; Configuracion incompleta en el servidor: {{ faltantes|join(', ') }}.</div>
        {% endif %}
        {% if avisos %}
        <div class="aviso-config">{% for a in avisos %}&#9888; {{ a }}<br>{% endfor %}</div>
        {% endif %}

        {% if programado %}
        <div class="alerta-exito" style="background:#1d4ed8">&#9201;&#65039; {{ programado }}</div>
        {% endif %}
        {% if entregado_todo %}
        <div class="alerta-exito">&#9989; ENTREGADO Y CONFIRMADO POR TELEGRAM<div class="detalle">{{ detalle }}</div></div>
        {% elif detalle %}
        <div class="alerta-error">&#10060; NO ENTREGADO<div class="detalle">{{ detalle }}</div></div>
        {% endif %}

        <datalist id="lista-etiquetas">{% for e in etiquetas %}<option value="{{ e.nombre }}">{% endfor %}</datalist>

        <!-- 0. MENU PEGADO -->
        <details {% if vista or cola or resultados_picks %}open{% endif %}>
            <summary>&#128203; 0. Jornada del día (pega el menú y sale todo solo)</summary>
            <form method="POST">
                <label>Pega aquí el menú de etiquetas tal cual:</label>
                <textarea name="menu" rows="10" placeholder="🏷 Etiquetas · miércoles 28 de octubre&#10;🏇 Belmont Park&#10;* ★ BEST BET: HOY NO HAY&#10;* 💰 VALOR DEL DÍA: ⏰ 3:14 PM · C5 · #12 Duty (6/1) jugar 4 puesto&#10;* 🟥 RATA DEL DÍA: ⏰ 3:14 PM · C5 · #1 Mortal Lock (5/1) parada free 1 dar 2 puesto&#10;...">{{ menu_texto }}</textarea>
                <label>Nota de horarios:</label>
                <textarea name="nota" rows="3">{{ menu_nota }}</textarea>
                <button type="submit" formaction="/menu-vista" class="b-azul">&#128065; Vista previa</button>
                {% if vista %}
                <div style="margin-top:16px">
                    <div style="background:#14532d;padding:8px 12px;border-radius:6px;font-weight:bold;margin-top:6px">&#128275; GRUPO GRATIS · así sale la jornada</div>
                    <label class="check"><input type="checkbox" name="pub_menu" value="1" checked>1) Menú del día · sale al publicar</label>
                    <pre style="white-space:pre-wrap;background:#1e293b;padding:10px;border-radius:6px;font-size:12px">{{ vista.publico }}</pre>
                    {% if vista.promo %}
                    <label class="check"><input type="checkbox" name="pub_promo" value="1" checked>2) Súper promo VIP {{ vista.precio_dia }} · a los <input type="number" name="min_promo" value="15" style="width:70px;display:inline-block;margin:0 6px"> min</label>
                    <pre style="white-space:pre-wrap;background:#1e293b;padding:10px;border-radius:6px;font-size:12px">{{ vista.promo }}</pre>
                    {% endif %}
                    <label>3) Paradas FREE con ALERTA · <input type="number" name="min_free" value="60" style="width:70px;display:inline-block;margin:0 6px"> min antes de su carrera:</label>
                    {% for n, t, h in vista.gratis %}
                    <label class="check"><input type="checkbox" name="free" value="{{ n }}" checked>&#127873; {{ t }} &nbsp;<span class="ayuda">(sale {{ h }})</span></label>
                    {% endfor %}
                    {% if not vista.gratis %}<div class="ayuda">Ningún pick marcado como "parada free 1/2" en el menú.</div>{% endif %}
                    {% if vista.valores %}
                    <label class="check"><input type="checkbox" name="pub_valores" value="1" checked>4) Especiales ALTO DIVIDENDO {{ vista.precio_valores }} · <input type="number" name="min_valores" value="120" style="width:70px;display:inline-block;margin:0 6px"> min antes del primero, y último llamado a los <input type="number" name="min_ultimo" value="30" style="width:60px;display:inline-block;margin:0 6px"> min</label>
                    <pre style="white-space:pre-wrap;background:#1e293b;padding:10px;border-radius:6px;font-size:12px">{{ vista.valores }}</pre>
                    {% endif %}

                    <div style="background:#7c2d12;padding:8px 12px;border-radius:6px;font-weight:bold;margin-top:16px">&#128274; GRUPO VIP · picks completos (salen al publicar)</div>
                    {% for n, t in vista.vip %}
                    <label class="check"><input type="checkbox" name="vip" value="{{ n }}" checked>{{ t }}</label>
                    {% endfor %}
                    {% if not vista.vip %}<div class="ayuda">No hay picks para el VIP.</div>{% endif %}

                    <label class="check" style="margin-top:14px;color:#fbbf24"><input type="checkbox" name="prueba" value="1">MODO PRUEBA: enviar todo ya, uno tras otro cada 20 s (ignora las horas)</label>
                    <button type="submit" formaction="/webhook-menu">&#128640; Programar la jornada</button>
                </div>
                {% endif %}
            </form>
            {% if resultados_picks %}
            <form action="/webhook-resultados" method="POST" style="margin-top:18px">
                <div style="background:#1e3a8a;padding:8px 12px;border-radius:6px;font-weight:bold">&#127937; Resultados de la jornada</div>
                <div class="ayuda">✅ = ganamos (en la RATA: la rata perdió). ❌ = no se dio. Deja "—" lo que aún no corre.</div>
                {% for n, t, rata in resultados_picks %}
                <div style="padding:6px 0;border-bottom:1px solid #334155">
                    <div style="font-size:12px">{{ t }}</div>
                    <div class="fila">
                        <div><select name="res_{{ n }}">
                            <option value="">— sin resultado</option>
                            <option value="gano">{% if rata %}✅ La rata perdió (festejo){% else %}✅ Ganó (festejo){% endif %}</option>
                            <option value="perdio">{% if rata %}❌ La rata ganó{% else %}❌ Perdió{% endif %}</option>
                        </select></div>
                        <div><input type="text" name="pago_{{ n }}" placeholder="Pagó (opcional, ej: $14.60)"></div>
                    </div>
                </div>
                {% endfor %}
                <label class="check"><input type="checkbox" name="resumen" value="1" checked>Publicar también el resumen del día (✅ y ❌) en el grupo gratis</label>
                <label class="check"><input type="checkbox" name="fijar" value="1">Fijar los festejos en el canal</label>
                <button type="submit" class="b-naranja">&#127881; Publicar resultados</button>
            </form>
            {% endif %}
            {% if cola %}
            <div style="margin-top:18px">
                <label>&#9201;&#65039; Mensajes programados (hora de Venezuela):</label>
                {% for c in cola %}
                <div class="ayuda" style="padding:4px 0;border-bottom:1px solid #334155">
                    {% if c.estado == 'enviado' %}&#9989;{% elif c.estado == 'fallido' %}&#10060;{% elif c.estado == 'cancelado' %}&#9940;{% else %}&#9203;{% endif %}
                    <b>{{ c.hora }}</b> · {{ 'GRATIS' if c.destino == 'gratis' else 'VIP' }} · {{ c.titulo }}
                    {% if c.estado == 'fallido' %}<br><span style="color:#f87171">{{ c.detalle }}</span>{% endif %}
                </div>
                {% endfor %}
                <form action="/cola-cancelar" method="POST">
                    <button type="submit" class="b-gris">&#9940; Cancelar todo lo pendiente</button>
                </form>
            </div>
            {% endif %}
        </details>

        <!-- 1. PROGRAMACION -->
        <details>
            <summary>&#9728;&#65039; 1. Programación del día (mañana)</summary>
            <form action="/webhook-programacion" method="POST">
                <label>Valor del día del ESPECIAL:</label>
                <input type="text" name="valor" value="{{ valor_especial }}" placeholder="Ej: $4.99">
                <label>Reseña del ejemplar del ESPECIAL (sale debajo del ESPECIAL):</label>
                <textarea name="resena" rows="3" placeholder="Ej: Baja de categoría, viene de llegar segundo a medio cuerpo y repite jinete. Ojo con su velocidad temprana."></textarea>
                <div class="ayuda">No pongas el nombre ni el número: el ejemplar es lo que se cobra.</div>
                <label>Etiquetas y horas (una por línea: ETIQUETA | HORA). Borra las que no haya hoy:</label>
                <textarea name="lineas" rows="8">{{ programacion_defecto }}</textarea>
                <label>Nota de horarios:</label>
                <textarea name="nota" rows="4">{{ nota_horarios }}</textarea>
                <label>Destino:</label>
                <select name="tipo_grupo">
                    <option value="gratis">&#128275; Grupo Gratis</option>
                    <option value="ambos">&#128260; Ambos Grupos</option>
                    <option value="vip">&#128274; Grupo VIP</option>
                </select>
                <label class="check"><input type="checkbox" name="con_banner" value="1" {% if hay_banner %}checked{% endif %}>Enviar antes el banner MENÚ DEL DÍA</label>
                {% if not hay_banner %}<div class="ayuda">Falta IMG_MENU_DEL_DIA en Render para el banner.</div>{% endif %}
                <button type="submit" class="b-azul">&#128197; Publicar programación</button>
            </form>
        </details>

        <!-- 2. PICK -->
        <details open>
            <summary>&#127943; 2. Nuevo pronóstico (pick)</summary>
            <form action="/webhook-pick" method="POST">
                <div class="fila">
                    <div><label>Etiqueta:</label>
                        <input type="text" name="etiqueta" list="lista-etiquetas" value="PARADA FREE 1" required></div>
                    <div><label>Destino:</label>
                        <select name="tipo_grupo">
                            <option value="auto">&#9881;&#65039; Según etiqueta</option>
                            <option value="gratis">&#128275; Grupo Gratis</option>
                            <option value="vip">&#128274; Grupo VIP</option>
                            <option value="ambos">&#128260; Ambos</option>
                        </select></div>
                </div>
                <div class="ayuda">Escoge de la lista o escribe una (ej: BEST BET GULFSTREAM). "Según etiqueta": las FREE van al grupo gratis, las demás al VIP.</div>
                <div class="fila">
                    <div><label>Hipódromo:</label><input type="text" name="hipodromo" placeholder="Ej: Gulfstream Park" required></div>
                    <div><label>Carrera N°:</label><input type="text" name="carrera" placeholder="Ej: 5" required></div>
                </div>
                <div class="fila">
                    <div><label>N° caballo:</label><input type="text" name="numero" placeholder="Ej: 7"></div>
                    <div><label>Nombre del caballo:</label><input type="text" name="caballo" placeholder="Ej: Thunder Bolt"></div>
                </div>
                <label>Tipo de apuesta:</label>
                <input type="text" name="tipo_apuesta" list="tipos" placeholder="Ej: Ganador / Exacta 7-3">
                <datalist id="tipos">{% for t in tipos %}<option value="{{ t }}">{% endfor %}</datalist>
                <div class="fila">
                    <div><label>Cuota / Pago:</label><input type="text" name="cuota" placeholder="Ej: 3,50 o 5-2" required></div>
                    <div><label>Stake:</label><input type="text" name="unidades" value="5" required></div>
                </div>
                <label>Comentario (opcional):</label>
                <textarea name="analisis" rows="2" placeholder="Opcional. Si lo dejas vacío sale el formato limpio."></textarea>
                <label class="check"><input type="checkbox" name="con_gif" value="1" {% if hay_gif %}checked{% endif %}>Enviar antes el GIF NUEVO PRONÓSTICO</label>
                <label class="check"><input type="checkbox" name="con_imagen" value="1" checked>Usar la imagen de la etiqueta (RATA DEL DÍA / RATA FUERTE)</label>
                <div class="ayuda">RATA: pon el favorito vulnerable en N° y nombre, y lo que paga en "Cuota / Pago". El stake no se muestra.<br>BEST BET DEL HIPÓDROMO sale como "BEST BET" + el nombre del hipódromo.</div>
                <button type="submit">&#128640; Publicar pick</button>
            </form>
        </details>

        <!-- 2b. RATA -->
        <details>
            <summary>&#128000; 2b. Rata del día / Rata fuerte</summary>
            <form action="/webhook-pick" method="POST">
                <input type="hidden" name="con_imagen" value="1">
                <div class="fila">
                    <div><label>Etiqueta:</label>
                        <select name="etiqueta">
                            <option value="RATAS DEL DÍA">&#128000; RATAS DEL DÍA</option>
                            <option value="RATA FUERTE">&#128000;&#128293; RATA FUERTE</option>
                        </select></div>
                    <div><label>Destino:</label>
                        <select name="tipo_grupo">
                            <option value="vip">&#128274; Grupo VIP</option>
                            <option value="gratis">&#128275; Grupo Gratis</option>
                            <option value="ambos">&#128260; Ambos</option>
                        </select></div>
                </div>
                <div class="fila">
                    <div><label>Hipódromo:</label><input type="text" name="hipodromo" placeholder="Ej: Gulfstream Park" required></div>
                    <div><label>Carrera N°:</label><input type="text" name="carrera" placeholder="Ej: 5" required></div>
                </div>
                <div class="fila">
                    <div><label>N° ejemplar:</label><input type="text" name="numero" placeholder="Ej: 3" required></div>
                    <div><label>Nombre:</label><input type="text" name="caballo" placeholder="Ej: Gran Favorito" required></div>
                </div>
                <div class="fila">
                    <div><label>ML (dividendo):</label><input type="text" name="cuota" placeholder="Ej: 4-5" required></div>
                    <div><label>Dar (ej: dar 2 puesto). Vacío = pago x $2:</label><input type="text" name="pago" placeholder="Ej: dar 2 puesto"></div>
                </div>
                <div class="ayuda">Escribe lo que hay que dar (ej: dar 2 puesto). Si lo dejas vacío, sale el pago x $2 calculado desde el ML. Sale con la imagen de la rata.</div>
                <button type="submit" style="background:#b91c1c">&#128000; Publicar rata</button>
            </form>
        </details>

        <!-- 3. VERDE -->
        <details>
            <summary>&#9989; 3. Resultado verde (ticket ganador)</summary>
            <form action="/webhook-resultado" method="POST" enctype="multipart/form-data">
                <div class="fila">
                    <div><label>Etiqueta:</label>
                        <input type="text" name="etiqueta" list="lista-etiquetas" value="PARADA FREE 1" required></div>
                    <div><label>Destino:</label>
                        <select name="tipo_grupo">
                            <option value="ambos">&#128260; Ambos</option>
                            <option value="gratis">&#128275; Grupo Gratis</option>
                            <option value="vip">&#128274; Grupo VIP</option>
                        </select></div>
                </div>
                <label>Foto del ticket ganador:</label>
                <input type="file" name="foto" accept="image/*">
                <label>Pagó (opcional, ej: $45.20 por $2):</label>
                <input type="text" name="pago">
                <label>Caballo ganador (opcional, ej: #7 Thunder Bolt en la 5ª de Gulfstream):</label>
                <input type="text" name="detalle_caballo">
                <label>Texto (vacío = texto automático tipo "¡RATA FUERTE DENTROOOOO!..."):</label>
                <textarea name="texto" rows="4" placeholder="¡LA RATA FUERTE TAMBIÉN DENTROOOOO! ... ¡Seguimos trabajando!"></textarea>
                <label class="check"><input type="checkbox" name="fijar" value="1" checked>Fijar el mensaje en el canal</label>
                <button type="submit" class="b-naranja">&#9989; Publicar verde</button>
            </form>
        </details>

        <!-- 4. VENTA -->
        <details>
            <summary>&#128176; 4. Venta del VIP</summary>
            <form action="/webhook-venta" method="POST">
                <label>Mensaje:</label>
                <select name="plantilla">
                    {% for clave, p in plantillas.items() %}<option value="{{ clave }}">{{ p.nombre }}</option>{% endfor %}
                </select>
                <div class="fila">
                    <div><label>Oferta:</label><input type="text" name="oferta" placeholder="Ej: ESPECIAL ($10)"></div>
                    <div><label>Monto (NOS JUGAMOS) o valor del ESPECIAL (vacío = {{ valor_especial }}):</label><input type="text" name="monto" placeholder="Ej: $500"></div>
                </div>
                <label>Contacto para el comprobante (vacío = {{ vip_link }}):</label>
                <input type="text" name="contacto" placeholder="{{ vip_link or '@tuusuario' }}">
                <input type="hidden" name="qr_form" value="1">
                <label class="check"><input type="checkbox" name="con_qr" value="1" checked>Adjuntar el QR de pago (USDT BEP20)</label>
                <label>Texto libre (solo para "Texto libre"):</label>
                <textarea name="texto_libre" rows="3"></textarea>
                <label>Reseña del ESPECIAL (solo para "ESPECIAL del día"):</label>
                <textarea name="resena" rows="3" placeholder="La misma reseña del menú"></textarea>
                <label>Destino:</label>
                <select name="tipo_grupo">
                    <option value="gratis">&#128275; Grupo Gratis</option>
                    <option value="ambos">&#128260; Ambos</option>
                    <option value="vip">&#128274; Grupo VIP</option>
                </select>
                <button type="submit" class="b-morado">&#128227; Publicar venta</button>
            </form>
        </details>

        <!-- 5. TESTIMONIO -->
        <details>
            <summary>&#128172; 5. Testimonio / foto libre</summary>
            <form action="/webhook-foto" method="POST" enctype="multipart/form-data">
                <label>Foto (captura del seguidor):</label>
                <input type="file" name="foto" accept="image/*" required>
                <label>Texto (opcional):</label>
                <textarea name="texto" rows="3" placeholder="En muchos canales de telegram dicen que son los mejores pero no es lo mismo decirlo, que demostrarlo."></textarea>
                <label>Destino:</label>
                <select name="tipo_grupo">
                    <option value="gratis">&#128275; Grupo Gratis</option>
                    <option value="ambos">&#128260; Ambos</option>
                    <option value="vip">&#128274; Grupo VIP</option>
                </select>
                <button type="submit" class="b-gris">&#128247; Publicar foto</button>
            </form>
        </details>

        <!-- 6. CANAL -->
        <details>
            <summary>&#9881;&#65039; 6. Descripción del canal</summary>
            <form action="/webhook-descripcion" method="POST">
                <div class="ayuda" style="white-space: pre-line; margin-top: 10px;">{{ descripcion }}</div>
                <label>Aplicar en:</label>
                <select name="tipo_grupo">
                    <option value="ambos">&#128260; Ambos</option>
                    <option value="gratis">&#128275; Grupo Gratis</option>
                    <option value="vip">&#128274; Grupo VIP</option>
                </select>
                <div class="ayuda">El bot debe ser administrador con permiso para cambiar la info del canal.</div>
                <button type="submit" class="b-gris">&#9881;&#65039; Poner esta descripción</button>
            </form>
        </details>

        <div class="footer">Envio verificado &middot; el banner verde solo aparece si Telegram confirmo la entrega</div>
    </div>
</body>
</html>
"""


# ==========================================
# UTILIDADES
# ==========================================
def config_faltante():
    faltantes = []
    for nombre, valor in (("TELEGRAM_TOKEN", TELEGRAM_TOKEN), ("CHAT_ID_GRATIS", CHAT_ID_GRATIS),
                          ("CHAT_ID_VIP", CHAT_ID_VIP), ("WEBHOOK_SECRET", WEBHOOK_SECRET)):
        if not valor:
            faltantes.append(nombre)
    return faltantes


def avisos_config():
    avisos = []
    if not url_publica(VIP_LINK):
        avisos.append("VIP_LINK / CONTACTO_INFO no es un link valido (usa @usuario o https://t.me/...).")
    foto, foto_url = qr_binance()
    if not (foto or foto_url):
        avisos.append("Falta el QR de Binance: sube binance_qr.png al repositorio o configura BINANCE_QR.")
    return avisos


def esc(valor) -> str:
    return html.escape("" if valor is None else str(valor), quote=False)


def detectar_fuga(*textos):
    unido = " ".join(str(t or "") for t in textos).lower()
    return [p for p in PATRONES_PROHIBIDOS if p in unido]


def es_stake_cero(valor) -> bool:
    try:
        return float(str(valor).replace(",", ".")) == 0.0
    except (TypeError, ValueError):
        return False


def ahora_local():
    if ZoneInfo is not None:
        try:
            return datetime.datetime.now(ZoneInfo(ZONA_HORARIA))
        except Exception:
            pass
    return datetime.datetime.utcnow() - datetime.timedelta(hours=4)


def url_publica(link):
    """@usuario, t.me/..., wa.me/... o http(s) -> URL valida para un boton. Si no, None."""
    link = (link or "").strip()
    if not link:
        return None
    if link.startswith("@"):
        return "https://t.me/" + link[1:]
    if link.startswith(("t.me/", "wa.me/")):
        return "https://" + link
    if link.startswith(("http://", "https://")):
        return link
    return None


def teclado_contacto(con_comprobante=True):
    """Botones: comprobante de pago + informacion/reclamos/afiliacion (uno solo si es el mismo link)."""
    url_pago = url_publica(VIP_LINK)
    url_info = url_publica(CONTACTO_INFO)
    filas = []
    if con_comprobante and url_pago and url_pago == url_info:
        filas.append([{"text": "\U0001F4E9 Comprobante · Info · Reclamos", "url": url_pago}])
    else:
        if con_comprobante and url_pago:
            filas.append([{"text": "\U0001F4E9 Enviar comprobante", "url": url_pago}])
        if url_info:
            filas.append([{"text": "\U0001F4AC Info VIP · Reclamos · Afiliación", "url": url_info}])
    return {"inline_keyboard": filas} if filas else None


def firma_html():
    lineas = FIRMA_MENU.split("\n")
    return f"<b>{esc(lineas[0])}</b>\n" + "\n".join(f"<b>{esc(l)}</b>" for l in lineas[1:])


def texto_descripcion():
    """Descripcion del canal en Telegram (maximo 255 caracteres)."""
    info = url_publica(CONTACTO_INFO)
    texto = DESCRIPCION_CANAL
    if info:
        extra = f"\n\n\U0001F4AC Info VIP, reclamos y afiliación: {info.replace('https://', '')}"
        if len(texto) + len(extra) <= 255:
            texto += extra
    return texto[:255]


def boton(texto, link):
    url = url_publica(link)
    if not url:
        return None
    return {"inline_keyboard": [[{"text": texto, "url": url}]]}


def linea_bono():
    url = url_publica(BONO_LINK)
    if not url:
        return ""
    return f'\n\n➡️ <a href="{html.escape(url, quote=True)}">{esc(BONO_TEXTO)}</a>'


def info_etiqueta(etiqueta):
    """Etiquetas de la lista o escritas a mano (ej: BEST BET GULFSTREAM)."""
    if etiqueta in ETIQUETAS_POR_NOMBRE:
        return ETIQUETAS_POR_NOMBRE[etiqueta]
    e = (etiqueta or "").upper()
    if "FREE" in e or "GRATIS" in e:
        return {"nombre": etiqueta, "destino": "gratis", "emoji": "\U0001F381"}
    if "RATA" in e:
        return {"nombre": etiqueta, "destino": "vip", "emoji": "\U0001F400"}
    if "BEST BET" in e:
        return {"nombre": etiqueta, "destino": "vip", "emoji": "\U0001F3C6"}
    if "STAKAZO" in e:
        return {"nombre": etiqueta, "destino": "vip", "emoji": "\U0001F48E"}
    return {"nombre": etiqueta, "destino": "vip", "emoji": "\U0001F3AF"}


def resolver_destino(tipo_grupo, etiqueta):
    if tipo_grupo in ("gratis", "vip", "ambos"):
        return tipo_grupo
    return info_etiqueta(etiqueta)["destino"]


def qr_binance():
    """(archivo, url): el QR desde binance_qr.png/.jpg del repo, o desde BINANCE_QR."""
    for nombre in ("binance_qr.png", "binance_qr.jpg", "binance_qr.jpeg"):
        ruta = os.path.join(BASE_DIR, nombre)
        if os.path.exists(ruta):
            try:
                with open(ruta, "rb") as f:
                    tipo = "image/png" if nombre.endswith(".png") else "image/jpeg"
                    return (nombre, f.read(), tipo), None
            except OSError:
                pass
    return None, (BINANCE_QR or None)


def imagen_etiqueta(etiqueta):
    """Imagen fija de la etiqueta (rata_del_dia.png / rata_fuerte.png en el repo). (archivo, None) o (None, None)."""
    e = (etiqueta or "").upper()
    if "RATA" not in e:
        return None
    nombre = "rata_fuerte.png" if "FUERTE" in e else "rata_del_dia.png"
    ruta = os.path.join(BASE_DIR, nombre)
    try:
        with open(ruta, "rb") as f:
            return (nombre, f.read(), "image/png")
    except OSError:
        return None


def es_rata(etiqueta):
    return "RATA" in (etiqueta or "").upper()


def etiqueta_final(etiqueta, hipodromo):
    """BEST BET DEL HIPODROMO -> BEST BET GULFSTREAM PARK (una por hipodromo)."""
    if (etiqueta or "").upper() in ("BEST BET DEL HIPÓDROMO", "BEST BET DEL HIPODROMO") and hipodromo:
        return f"BEST BET {hipodromo.upper()}"
    return etiqueta


def contacto_visible(contacto):
    """Como se muestra el contacto en el texto: @usuario tal cual, o el link."""
    contacto = (contacto or "").strip()
    if contacto.startswith("@"):
        return contacto
    return url_publica(contacto) or contacto


# ==========================================
# FORMATOS DE MENSAJE (modelo TIPSELVERDE)
# ==========================================
def mensaje_programacion(lineas, nota, fecha, valor="", resena=""):
    dia = DIAS[fecha.weekday()]
    filas = []
    for l in (lineas or "").splitlines():
        l = l.strip()
        if not l:
            continue
        if "{valor}" in l:
            l = l.replace("{valor}", valor) if valor else l.replace(" ({valor})", "").replace("{valor}", "")
        filas.append(l)
    renglones = []
    resena = (resena or "").strip()
    puesta = False
    for f in filas:
        renglones.append(f"☑️ <b>{esc(f)}</b>")
        if resena and not puesta and "ESPECIAL" in f.upper():
            renglones.append(f"      \U0001F4DD <i>{esc(resena)}</i>")
            puesta = True
    if resena and not puesta:  # el menu no tenia linea de ESPECIAL
        renglones.append(f"\n\U0001F31F <b>ESPECIAL</b>\n\U0001F4DD <i>{esc(resena)}</i>")
    cuerpo = "\n".join(renglones)
    texto = f"\U0001F5D3 <u><b>PROGRAMACIÓN DEL {dia}:</b></u>\n\n{cuerpo}"
    if (nota or "").strip():
        texto += f"\n\n{esc(nota.strip())}"
    texto += "\n\n" + firma_html()
    return texto


def linea_ml_rata(ml, pago=""):
    """Linea corta: 'ML 4-5 · dar 2 puesto' o, si no hay indicacion, 'ML 4-5 · paga $3.60 x $2'."""
    pago = (pago or "").strip()
    texto = f"ML {esc(ml)}" if ml else ""
    if pago and not pago.startswith("$") and not pago.replace(".", "").isdigit():
        extra = esc(pago)  # indicacion libre: "dar 2 puesto", "dar 1 puesto"...
    else:
        monto = pago or pago_desde_ml(ml) or ""
        if monto and not monto.startswith("$"):
            monto = "$" + monto
        extra = f"paga {esc(monto)} x $2" if monto else ""
    if extra:
        texto += (" · " if texto else "") + extra
    return f"<i>{texto}</i>" if texto else ""


def pago_desde_ml(ml, base=2):
    """Pago a ganador por la apuesta minima ($2) segun el ML. '4-5' -> '$3.60'. None si no se entiende."""
    texto = (ml or "").strip().lower().replace(" ", "")
    if texto in ("even", "evens", "1-1", "1/1", "par"):
        num, den = 1.0, 1.0
    else:
        for sep in ("-", "/", ":"):
            if sep in texto:
                partes = texto.split(sep)
                break
        else:
            return None
        try:
            num, den = float(partes[0].replace(",", ".")), float(partes[1].replace(",", "."))
        except (ValueError, IndexError):
            return None
        if den <= 0:
            return None
    return f"${base + base * num / den:.2f}"


def mensaje_pick(etiqueta, hipodromo, carrera, numero, caballo, tipo_apuesta,
                 cuota, unidades, analisis, pago="", hora=""):
    info = info_etiqueta(etiqueta)
    linea_hipodromo = (f"\U0001F3C7 <u>Hípica - {esc(hipodromo)} · Carrera {esc(carrera)}</u>"
                       + (f"\n\u23F0 {esc(hora)}" if hora else ""))
    jugada = " ".join(p for p in (
        f"#{numero.lstrip('#')}" if numero else "",
        caballo,
    ) if p).strip()
    if tipo_apuesta:
        jugada = f"{jugada} · {tipo_apuesta}" if jugada else tipo_apuesta

    if es_rata(etiqueta):
        # La rata: el favorito vulnerable que NO nos gusta para ganar + lo que paga
        ejemplar = " ".join(p for p in (f"#{numero.lstrip('#')}" if numero else "", caballo) if p)
        partes = [
            f"{info['emoji']} <b>{esc(etiqueta)}</b>",
            "",
            linea_hipodromo,
            "",
            f"\U0001F400 <b>{esc(ejemplar or tipo_apuesta)}</b>",
            "\u26A0\uFE0F Favorito vulnerable: <b>NO nos gusta para ganar</b>",
            "",
            linea_ml_rata(cuota, pago),
        ]
    else:
        partes = [
            f"{info['emoji']} <b>{esc(etiqueta)}</b>",
            "",
            linea_hipodromo,
            "",
            f"\U0001F449 <b>{esc(jugada)}</b>",
            "",
            f"\U0001F4C8 <i>Cuota {esc(cuota)}</i>" + (f"   \U0001F4CD <i>STAKE {esc(unidades)}</i>" if unidades else ""),
        ]
    if analisis:
        partes += ["", f"\U0001F4CB {esc(analisis)}"]
    texto = "\n".join(partes)
    texto += linea_bono()
    texto += "\n\n\U0001F51E Juega con responsabilidad."
    return texto


# ==========================================
# MENU DEL DIA PEGADO (formato de tu sistema de etiquetas)
# ==========================================
import re as _re
import unicodedata as _ud

_RE_ENTRADA = _re.compile(
    r"\u23F0\s*([0-9]{1,2}:[0-9]{2}\s*[AaPp]\.?\s*[Mm]\.?)\s*[·\-|]\s*C\s*(\d+)\s*[·\-|]\s*#\s*(\d+)\s+(.+?)\s*\(([^)]+)\)\s*[·\-|:,]?\s*(.*)$")
_DIAS_TXT = {"lunes": "LUNES", "martes": "MARTES", "miercoles": "MIÉRCOLES", "jueves": "JUEVES",
             "viernes": "VIERNES", "sabado": "SÁBADO", "domingo": "DOMINGO"}


def _sin_acentos(t):
    return "".join(c for c in _ud.normalize("NFD", t) if _ud.category(c) != "Mn").upper()


def _limpiar_linea(l):
    return _re.sub(r"^[\s*\-•·]+", "", l).strip()


def parsear_menu(texto):
    """Lee el menu de etiquetas (🏇 hipodromo, BEST BET, VALOR DEL DIA, RATA DEL DIA, RATAS FUERTES)."""
    dia = None
    hipodromos = []
    actual = None
    etiqueta = None
    ultimo = None
    for cruda in (texto or "").splitlines():
        linea = _limpiar_linea(cruda)
        if not linea:
            continue
        plano = _sin_acentos(linea)
        if dia is None and "ETIQUETAS" in plano:
            for k, v in _DIAS_TXT.items():
                if k.upper() in plano:
                    dia = v
            continue
        if linea.startswith("\U0001F3C7"):
            actual = {"nombre": linea.lstrip("\U0001F3C7").strip(), "items": []}
            hipodromos.append(actual)
            etiqueta, ultimo = None, None
            continue
        if actual is None:
            continue
        if "PARA EL GRUPO VIP" in plano or "ESTA LISTA" in plano:
            continue
        nueva = None
        if "BEST BET" in plano:
            nueva = "BEST BET"
        elif "VALOR DEL DIA" in plano:
            nueva = "VALOR"
        elif "RATA" in plano and "FUERTE" in plano:
            nueva = "RATA FUERTE"
        elif "RATA DEL DIA" in plano or "RATAS DEL DIA" in plano:
            nueva = "RATA DEL DÍA"
        if nueva:
            etiqueta, ultimo = nueva, None
        m = _RE_ENTRADA.search(linea)
        if m and etiqueta:
            ultimo = {"etiqueta": etiqueta, "hora": m.group(1).upper().replace(".", ""),
                      "carrera": m.group(2), "numero": m.group(3), "caballo": m.group(4).strip(),
                      "ml": m.group(5).strip(), "tipo": m.group(6).strip(" ·-|:,"), "resena": ""}
            mfree = _re.search(r"parada\s*free\s*(\d+)", ultimo["tipo"], _re.I)
            if mfree:
                ultimo["free"] = mfree.group(1)
                ultimo["tipo"] = _re.sub(r"parada\s*free\s*\d+", "", ultimo["tipo"], flags=_re.I).strip(" ·-|:,")
            actual["items"].append(ultimo)
        elif not nueva and not m and ultimo is not None and ultimo["etiqueta"] == "VALOR" and "HOY NO HAY" not in plano:
            ultimo["resena"] = (ultimo["resena"] + " " + linea).strip()
    # Si un ejemplar es RATA DEL DIA y RATA FUERTE a la vez, queda solo como RATA FUERTE
    for h in hipodromos:
        fuertes = {(i["carrera"], i["numero"]) for i in h["items"] if i["etiqueta"] == "RATA FUERTE"}
        h["items"] = [i for i in h["items"]
                      if not (i["etiqueta"] == "RATA DEL DÍA" and (i["carrera"], i["numero"]) in fuertes)]
    return {"dia": dia, "hipodromos": [h for h in hipodromos if h["items"]]}


def _orden_hora(item):
    try:
        return datetime.datetime.strptime(item["hora"].replace(" ", ""), "%I:%M%p").time()
    except ValueError:
        return datetime.time(23, 59)


def momento_carrera(hora_txt, fecha=None):
    """Hora del menu (zona de los hipodromos, ZONA_MENU) -> datetime con zona. None si no se entiende."""
    try:
        t = datetime.datetime.strptime(hora_txt.replace(" ", ""), "%I:%M%p").time()
    except ValueError:
        return None
    fecha = fecha or ahora_local().date()
    if ZoneInfo is not None:
        try:
            return datetime.datetime.combine(fecha, t, tzinfo=ZoneInfo(ZONA_MENU))
        except Exception:
            pass
    return datetime.datetime.combine(fecha, t, tzinfo=datetime.timezone(datetime.timedelta(hours=-4)))


def a_local(dt):
    if dt is None:
        return None
    if ZoneInfo is not None:
        try:
            return dt.astimezone(ZoneInfo(ZONA_HORARIA))
        except Exception:
            pass
    return dt.astimezone(datetime.timezone(datetime.timedelta(hours=-4)))


def hora_ve(hora_txt):
    """'3:14 PM' del menu -> la misma hora en Venezuela (cambia sola cuando EE. UU. cambia de horario)."""
    dt = a_local(momento_carrera(hora_txt))
    return dt.strftime("%I:%M %p").lstrip("0") if dt else hora_txt


def menu_publico(menu, nota, fecha, valor):
    """1) Menu para el grupo gratis: etiquetas y horas SIN nombre ni numero del ejemplar."""
    dia = menu["dia"] or DIAS[fecha.weekday()]
    nombres = {"BEST BET": "⭐ BEST BET", "VALOR": "\U0001F48E\U0001F4B0 ESPECIAL ALTO DIVIDENDO",
               "RATA DEL DÍA": "\U0001F400 RATA DEL DÍA", "RATA FUERTE": "\U0001F400\U0001F525 RATA FUERTE"}
    bloques = []
    for h in menu["hipodromos"]:
        filas = [f"\U0001F3C7 <u><b>{esc(h['nombre'].upper())}</b></u>"]
        for i in sorted(h["items"], key=_orden_hora):
            nombre = nombres[i["etiqueta"]]
            if i.get("free"):
                nombre = f"\U0001F381 PARADA FREE {i['free']} ({nombre.split(' ', 1)[1]})"
            filas.append(f"☑️ <b>{esc(nombre)}</b> | {esc(hora_ve(i['hora']))} · C{esc(i['carrera'])}")
        bloques.append("\n".join(filas))
    texto = f"\U0001F5D3 <u><b>PROGRAMACIÓN DEL {dia}:</b></u>\n\n" + "\n\n".join(bloques)
    if (nota or "").strip():
        texto += f"\n\n{esc(nota.strip())}"
    texto += "\n\n" + firma_html()
    return texto


def picks_menu(menu):
    """Lista de picks: {titulo, etiqueta, texto, destino, item, hipodromo}. 'parada free N' van al gratis."""
    salida = []
    for h in menu["hipodromos"]:
        for i in sorted(h["items"], key=_orden_hora):
            if i["etiqueta"] == "VALOR":
                etq = "ESPECIAL ALTO DIVIDENDO"
            elif i["etiqueta"] == "BEST BET":
                etq = f"BEST BET {h['nombre'].upper()}"
            else:
                etq = "RATAS DEL DÍA" if i["etiqueta"] == "RATA DEL DÍA" else "RATA FUERTE"
            destino = "vip"
            if i.get("free"):
                etq = f"PARADA FREE {i['free']} · {etq}"
                destino = "gratis"
            hora = hora_ve(i["hora"])
            if es_rata(etq):
                texto = mensaje_pick(etq, h["nombre"], i["carrera"], i["numero"], i["caballo"], "",
                                     i["ml"], "", "", pago=i["tipo"], hora=hora)
            else:
                texto = mensaje_pick(etq, h["nombre"], i["carrera"], i["numero"], i["caballo"], i["tipo"],
                                     i["ml"], "", i["resena"] if i["etiqueta"] == "VALOR" else "", hora=hora)
            titulo = (f"{etq} · {h['nombre']} C{i['carrera']} · #{i['numero']} {i['caballo']} ({i['ml']})"
                      + (f" · {i['tipo']}" if i["tipo"] else "") + f" · {hora}")
            salida.append({"titulo": titulo, "etiqueta": etq, "texto": texto, "destino": destino,
                           "item": i, "hipodromo": h["nombre"]})
    return salida


def alerta_free(pick):
    """3) Alerta llamativa de la PARADA FREE (estilo NUEVO PRONOSTICO)."""
    return (
        "\U0001F6A8\U0001F6A8\U0001F6A8 <b>ALERTA DE PARADA</b> \U0001F6A8\U0001F6A8\U0001F6A8\n"
        f"\U0001F381 <b>{esc(pick['etiqueta'].split(' · ')[0])} · GRATIS</b>\n"
        "⏳ <b>¡FALTA 1 HORA PARA LA CARRERA!</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        + pick["texto"]
        + "\n━━━━━━━━━━━━━━━━\n"
        f"\U0001F525 ¿Quieres TODAS las paradas de hoy? <b>VIP {esc(PRECIO_DIA)}</b>"
    )


def _precio(t):
    try:
        return float(str(t).replace("$", "").replace(",", ".").strip())
    except ValueError:
        return None


def _cuenta(n, uno, varios):
    return f"{n} {uno if n == 1 else varios}"


def _bloque_pago():
    visible = esc(contacto_visible(VIP_LINK)) if VIP_LINK else ""
    return (f"\U0001F4B3 Pago en USDT · Red {esc(USDT_RED)} (QR)\n<code>{esc(USDT_DIRECCION)}</code>"
            + (f"\n\U0001F4E9 Comprobante a: {visible}" if visible else ""))


def valores_del_menu(menu):
    """Caballos de valor del dia (sin los free), ordenados por hora de carrera entre todos los hipodromos."""
    vals = [(h, i) for h in menu["hipodromos"] for i in h["items"]
            if i["etiqueta"] == "VALOR" and not i.get("free")]
    return sorted(vals, key=lambda hi: _orden_hora(hi[1]))


def promo_vip(menu, precio_valores, precio_dia):
    """2) Super promo VIP: resumen de todo lo que trae el VIP hoy (sin ejemplares) + totales + QR."""
    dia = menu["dia"] or DIAS[ahora_local().weekday()]
    lineas, total, hay = [], 0, set()
    for h in menu["hipodromos"]:
        vip = [i for i in h["items"] if not i.get("free")]
        if not vip:
            continue
        c = {k: sum(1 for i in vip if i["etiqueta"] == k) for k in ("VALOR", "BEST BET", "RATA DEL DÍA", "RATA FUERTE")}
        partes = []
        if c["VALOR"]:
            partes.append("\U0001F48E " + _cuenta(c["VALOR"], "Especial ALTO DIVIDENDO", "Especiales ALTO DIVIDENDO"))
        if c["BEST BET"]:
            partes.append("⭐ " + _cuenta(c["BEST BET"], "Best Bet", "Best Bets"))
        if c["RATA DEL DÍA"]:
            partes.append("\U0001F400 " + _cuenta(c["RATA DEL DÍA"], "Rata del día", "Ratas del día"))
        if c["RATA FUERTE"]:
            partes.append("\U0001F400\U0001F525 " + _cuenta(c["RATA FUERTE"], "Rata fuerte", "Ratas fuertes"))
        lineas.append(f"\U0001F3C7 <b>{esc(h['nombre'].upper())}</b>\n      " + " · ".join(partes))
        total += len(vip)
        hay.update(k for k, v in c.items() if v)
    if not total:
        return None
    n_valores = len(valores_del_menu(menu))
    incluye = " + ".join(n for k, n in (("VALOR", "especiales de alto dividendo"), ("RATA", "ratas"), ("BEST BET", "best bets"))
                         if any(x.startswith(k) for x in hay))
    return (
        f"\U0001F525<b>SÚPER PROMO CORRIDATV · {dia}</b>\U0001F525\n\n"
        "Entra al <b>VIP</b> y llévate <b>TODAS las paradas de hoy</b> detalladas "
        "(ejemplar, número, ML, jugada y reseña):\n\n"
        + "\n".join(lineas)
        + f"\n\n\U0001F4E6 <b>{_cuenta(total, 'parada', 'paradas')} en total</b>\n\n"
        + (f"\U0001F48E\U0001F4B0 <b>Solo los {n_valores} ESPECIALES DE ALTO DIVIDENDO: {esc(precio_valores)}</b>\n"
           "      (pagan mucho a ganador y a place)\n" if n_valores > 1 else
           (f"\U0001F48E\U0001F4B0 <b>Solo el ESPECIAL DE ALTO DIVIDENDO: {esc(precio_valores)}</b>\n" if n_valores else ""))
        + f"\U0001F381 <b>TODO EL DÍA POR SOLO {esc(precio_dia)}</b>" + (f" ({incluye})" if incluye else "")
        + "\n\n\U0001F449<b>PLAZAS MUY LIMITADAS!</b>\U0001F39F\n\n"
        + _bloque_pago()
        + "\n\n<b>ESTO NO ES CORAZONADA</b> ✅"
    )


def venta_valores(menu, precio_valores):
    """4) Una sola promocion: todos los caballos de valor (alto dividendo, jugar largo) + QR."""
    vals = valores_del_menu(menu)
    if not vals:
        return None
    n = len(vals)
    bloques = []
    for h, i in vals:
        b = f"\U0001F3C7 <b>{esc(h['nombre'].upper())}</b> · ⏰ {esc(hora_ve(i['hora']))} · C{esc(i['carrera'])}"
        if i["resena"]:
            b += f"\n\U0001F4DD <i>{esc(i['resena'])}</i>"
        bloques.append(b)
    mls = [_precio_ml(i["ml"]) for _, i in vals]
    mls = [m for m in mls if m]
    rango = ""
    if mls:
        lo, hi = min(mls), max(mls)
        rango = (f"\U0001F4C8 Cuotas de <b>{_fmt_ml(lo)}</b> hasta <b>{_fmt_ml(hi)}</b>\n" if lo != hi
                 else f"\U0001F4C8 Cuota de <b>{_fmt_ml(lo)}</b>\n")
    return (
        "\U0001F48E\U0001F4B0\U0001F48E\U0001F4B0\U0001F48E\n"
        "<b>ESPECIALES DE ALTO DIVIDENDO</b>\n"
        "\U0001F48E\U0001F4B0\U0001F48E\U0001F4B0\U0001F48E\n\n"
        f"Hoy tenemos <b>{_cuenta(n, 'ejemplar especial', 'ejemplares especiales')}</b> que "
        "<b>PAGAN MUCHO a GANADOR y a PLACE</b> \U0001F4B5\U0001F4B5\n"
        "\U0001F3AF Para <b>JUGAR LARGO</b>: a 5 puesto o a ganar\n"
        + rango + "\n"
        + "\n\n".join(bloques)
        + f"\n\n\U0001F381 <b>{'LOS ' + str(n) if n > 1 else 'EL ESPECIAL'} POR SOLO {esc(precio_valores)}</b> (una sola promoción)\n"
        "\U0001F449<b>PLAZAS MUY LIMITADAS!</b>\U0001F39F\n\n"
        + _bloque_pago()
        + "\n\n\U0001F3C3... Paga, manda tu comprobante y recibes los ejemplares antes de la primera carrera."
    )


def _precio_ml(ml):
    """'6/1' -> 6.0, '5-2' -> 2.5 (para mostrar el rango de cuotas sin revelar el ejemplar)."""
    t = (ml or "").replace(" ", "")
    for sep in ("-", "/", ":"):
        if sep in t:
            try:
                a, b = t.split(sep)[:2]
                return float(a) / float(b)
            except (ValueError, ZeroDivisionError):
                return None
    return None


def _fmt_ml(x):
    return f"{x:g}/1" if float(x).is_integer() else f"{x:.1f}/1"


def ultimo_llamado_valores(menu, precio_valores, minutos):
    vals = valores_del_menu(menu)
    if not vals:
        return None
    return (
        f"⏳⏳ <b>ÚLTIMO LLAMADO</b> ⏳⏳\n\n"
        f"En <b>{minutos} minutos</b> corre el primer <b>ESPECIAL DE ALTO DIVIDENDO</b> de hoy "
        f"({esc(vals[0][0]['nombre'])}, C{esc(vals[0][1]['carrera'])}).\n\n"
        f"\U0001F48E\U0001F4B0 <b>{_cuenta(len(vals), 'ejemplar que paga mucho', 'ejemplares que pagan mucho')} "
        f"a ganador y place por {esc(precio_valores)}</b>\n"
        f"\U0001F381 O todo el día en el VIP por <b>{esc(PRECIO_DIA)}</b>\n\n"
        + _bloque_pago()
    )


# ==========================================
# COLA DE MENSAJES PROGRAMADOS (la jornada sale sola)
# ==========================================
import threading as _threading
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover
    _fcntl = None

COLA_PATH = os.path.join(BASE_DIR, "cola.json")
JORNADA_PATH = os.path.join(BASE_DIR, "jornada.json")


def guardar_jornada(menu_texto, nota):
    try:
        with open(JORNADA_PATH, "w", encoding="utf-8") as f:
            json.dump({"fecha": ahora_local().strftime("%Y-%m-%d"), "menu": menu_texto, "nota": nota}, f,
                      ensure_ascii=False)
    except OSError:
        pass


def leer_jornada():
    try:
        with open(JORNADA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _corta(etq):
    """'PARADA FREE 1 · RATAS DEL DÍA' -> ('PARADA FREE 1', 'RATAS DEL DÍA')."""
    partes = etq.split(" · ")
    return (partes[0], partes[1]) if len(partes) > 1 else (etq, etq)


def _ejemplar(p):
    i = p["item"]
    return (f"\U0001F3C7 {esc(p['hipodromo'])} · C{esc(i['carrera'])} · "
            f"<b>#{esc(i['numero'])} {esc(i['caballo'])}</b>")


def festejo(p, pago="", cta=True):
    """✅ Festejo llamativo (va al grupo gratis para atraer seguidores)."""
    titulo, base = _corta(p["etiqueta"])
    free = p["destino"] == "gratis"
    cab = f"✅✅✅✅<b>¡{esc(titulo)}!</b>✅✅✅✅"
    if es_rata(p["etiqueta"]):
        cuerpo = (f"\U0001F400 <b>¡JUGARON SU RATA!</b> A ESTA LE DOLÍAN LAS PATAS…\n\n"
                  f"{_ejemplar(p)}\n"
                  "El favorito vulnerable <b>LO MARCAMOS EN ROJO</b> \U0001F534 y no pudo ganar, tal como lo avisamos.")
    else:
        jugada = p["item"].get("tipo") or ""
        grito = ("\U0001F4B0\U0001F4B0 <b>¡ALTO DIVIDENDO DENTROOOO!</b> \U0001F4B0\U0001F4B0"
                 if "ALTO DIVIDENDO" in p["etiqueta"] else
                 "\U0001F525\U0001F525 <b>¡ES VERDEEEEE!</b> \U0001F525\U0001F525")
        cuerpo = (f"{grito}\n\n"
                  f"{_ejemplar(p)}"
                  + (f"\n\U0001F3AF {esc(jugada)}" if jugada else "")
                  + f"\n\n<i>{'¡Y te la regalamos GRATIS!' if free else 'Otra parada que nos entra.'} "
                  "La lectura fue buena. Gracias a todos por siempre confiar.</i>")
    texto = f"{cab}\n\n{cuerpo}"
    if pago:
        texto += f"\n\n\U0001F4B0 <b>PAGÓ {esc(pago)}</b>"
    texto += "\n\n<b>¡SEGUIMOS TRABAJANDO!</b> \U0001F4AA"
    if not free and cta:
        texto += f"\n\n\U0001F512 Esta iba en el VIP. Mañana entra: <b>todo el día por {esc(PRECIO_DIA)}</b>"
    return texto


def marca_negativa(p):
    """❌ Marca honesta cuando el pick no se da."""
    titulo, _ = _corta(p["etiqueta"])
    if es_rata(p["etiqueta"]):
        detalle = "Esta vez el favorito cumplió y ganó."
    else:
        detalle = "No se nos dio esta vez."
    return (f"\u274C <b>{esc(titulo)}</b>\n{_ejemplar(p)}\n{detalle} "
            "Seguimos con disciplina y estudio. \U0001F4AA")


def resumen_resultados(menu, marcados):
    """📊 Resumen del dia para el grupo gratis: todos los ✅ y ❌."""
    dia = menu["dia"] or DIAS[ahora_local().weekday()]
    lineas, g, perd = [], 0, 0
    for p, res in marcados:
        titulo, _ = _corta(p["etiqueta"])
        i = p["item"]
        icono = "\u2705" if res == "gano" else "\u274C"
        g += res == "gano"
        perd += res == "perdio"
        lineas.append(f"{icono} {esc(titulo)} · {esc(p['hipodromo'])} C{esc(i['carrera'])} · #{esc(i['numero'])} {esc(i['caballo'])}")
    return (
        f"\U0001F4CA <b>RESULTADOS CORRIDATV · {dia}</b>\n\n"
        + "\n".join(lineas)
        + f"\n\n<b>\u2705 {g} {'acierto' if g == 1 else 'aciertos'} · \u274C {perd}</b>\n\n"
        + f"\U0001F512 Mañana todas las paradas en el VIP por <b>{esc(PRECIO_DIA)}</b>\n\n"
        + firma_html()
    )
_cola_lock = _threading.Lock()


class _CandadoCola:
    """Candado entre hilos y entre procesos (varios workers de gunicorn) para no enviar dos veces."""
    def __enter__(self):
        _cola_lock.acquire()
        self.f = open(COLA_PATH + ".lock", "a")
        if _fcntl:
            _fcntl.flock(self.f, _fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        if _fcntl:
            _fcntl.flock(self.f, _fcntl.LOCK_UN)
        self.f.close()
        _cola_lock.release()


def _leer_cola():
    try:
        with open(COLA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _guardar_cola(cola):
    tmp = COLA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cola, f, ensure_ascii=False)
    os.replace(tmp, COLA_PATH)


def encolar(items):
    """items: dicts con cuando (datetime), destino, texto, titulo, y opcionales foto ('qr'|'rata_del_dia'|'rata_fuerte'),
    gif (bool), boton ('contacto'|'info')."""
    with _CandadoCola():
        cola = _leer_cola()
        base = max([c["id"] for c in cola] or [0])
        for n, it in enumerate(items, 1):
            cola.append({
                "id": base + n,
                "cuando": it["cuando"].astimezone(datetime.timezone.utc).isoformat(),
                "destino": it["destino"], "texto": it["texto"], "titulo": it["titulo"],
                "foto": it.get("foto"), "gif": bool(it.get("gif")), "boton": it.get("boton"),
                "estado": "pendiente", "detalle": "",
            })
        cola.sort(key=lambda c: c["cuando"])
        _guardar_cola(cola)


def _foto_de(clave):
    if clave == "qr":
        return qr_binance()
    if clave in ("rata_del_dia", "rata_fuerte"):
        return imagen_etiqueta("RATA FUERTE" if clave == "rata_fuerte" else "RATA"), None
    return None, None


def procesar_cola():
    """Envia lo que ya toco. Lo llaman el hilo interno (cada 30 s) y /tick (UptimeRobot)."""
    if config_faltante():
        return 0
    enviados = 0
    ahora = datetime.datetime.now(datetime.timezone.utc)
    with _CandadoCola():
        cola = _leer_cola()
        cambio = False
        for c in cola:
            if c["estado"] != "pendiente" or datetime.datetime.fromisoformat(c["cuando"]) > ahora:
                continue
            foto, foto_url = _foto_de(c.get("foto"))
            opciones = {"foto": foto, "foto_url": foto_url}
            if c.get("gif") and GIF_NUEVO_PRONOSTICO:
                opciones["animacion"] = GIF_NUEVO_PRONOSTICO
            if c.get("boton") == "contacto":
                opciones["reply_markup"] = teclado_contacto()
            elif c.get("boton") == "info":
                opciones["reply_markup"] = teclado_contacto(con_comprobante=False)
            res = despachar_telegram(c["destino"], {c["destino"]: c["texto"]}, **{c["destino"]: opciones})
            ok = bool(res) and all(r["entregado"] for r in res)
            c["estado"] = "enviado" if ok else "fallido"
            c["detalle"] = resumir(res)
            c["enviado_en"] = ahora.isoformat()
            cambio = True
            enviados += 1
        if cambio:
            # se guarda solo lo de las ultimas 48 h
            limite = ahora - datetime.timedelta(hours=48)
            cola = [c for c in cola if datetime.datetime.fromisoformat(c["cuando"]) > limite or c["estado"] == "pendiente"]
            _guardar_cola(cola)
    return enviados


def cola_para_panel():
    hoy = []
    for c in _leer_cola():
        dt = a_local(datetime.datetime.fromisoformat(c["cuando"]))
        hoy.append({"id": c["id"], "hora": dt.strftime("%d/%m %I:%M %p"), "titulo": c["titulo"],
                    "destino": c["destino"], "estado": c["estado"], "detalle": c.get("detalle", "")})
    return hoy


def _bucle_cola():
    import time
    while True:
        try:
            procesar_cola()
        except Exception as exc:  # el hilo nunca debe morir
            print(f"[cola] error: {exc!r}", flush=True)
        time.sleep(30)


_hilo_cola = None


def arrancar_hilo_cola():
    global _hilo_cola
    if _hilo_cola is None or not _hilo_cola.is_alive():
        _hilo_cola = _threading.Thread(target=_bucle_cola, daemon=True, name="cola-corridatv")
        _hilo_cola.start()


def planificar_jornada(menu, nota, opciones):
    """Arma la jornada completa: menu ya, promo VIP, free 1 h antes, venta de valores y ultimo llamado."""
    ahora = datetime.datetime.now(datetime.timezone.utc)
    prueba = opciones.get("prueba")
    paso = datetime.timedelta(seconds=20)
    items = []

    def cuando(dt):
        # en prueba todo sale ya, en orden, separado 20 s
        if prueba:
            return ahora + paso * len(items)
        return max(dt, ahora)

    picks = picks_menu(menu)
    if opciones.get("menu"):
        items.append({"cuando": cuando(ahora), "destino": "gratis", "titulo": "1) Menú del día",
                      "texto": menu_publico(menu, nota, ahora_local(), VALOR_ESPECIAL), "boton": "info"})
    promo = promo_vip(menu, PRECIO_VALORES, PRECIO_DIA) if opciones.get("promo") else None
    if promo:
        items.append({"cuando": cuando(ahora + datetime.timedelta(minutes=opciones.get("min_promo", 15))),
                      "destino": "gratis", "titulo": f"2) Súper promo VIP {PRECIO_DIA}", "texto": promo,
                      "foto": "qr", "boton": "contacto"})
    # VIP: todos los picks completos (los compradores ya los tienen)
    for n, p in enumerate(picks):
        if p["destino"] == "vip" and n in opciones.get("vip", []):
            foto = None
            if es_rata(p["etiqueta"]):
                foto = "rata_fuerte" if "FUERTE" in p["etiqueta"].upper() else "rata_del_dia"
            items.append({"cuando": cuando(ahora), "destino": "vip", "titulo": f"VIP · {p['titulo']}",
                          "texto": p["texto"], "foto": foto})
    # Free: 1 hora antes de su carrera, con alerta
    for n, p in enumerate(picks):
        if p["destino"] != "gratis" or n not in opciones.get("free", []):
            continue
        carrera = momento_carrera(p["item"]["hora"])
        if carrera is None:
            continue
        envio = carrera - datetime.timedelta(minutes=opciones.get("min_free", 60))
        if not prueba and carrera <= ahora:
            continue  # la carrera ya paso
        foto = None
        if es_rata(p["etiqueta"]):
            foto = "rata_fuerte" if "FUERTE" in p["etiqueta"].upper() else "rata_del_dia"
        items.append({"cuando": cuando(envio), "destino": "gratis", "titulo": f"3) Alerta · {p['titulo']}",
                      "texto": alerta_free(p), "foto": foto, "gif": True, "boton": "info"})
    # Valores: una sola promo antes del primer valor + ultimo llamado
    vals = valores_del_menu(menu)
    if vals and opciones.get("valores"):
        primera = momento_carrera(vals[0][1]["hora"])
        if primera and (prueba or primera > ahora):
            items.append({"cuando": cuando(primera - datetime.timedelta(minutes=opciones.get("min_valores", 120))),
                          "destino": "gratis", "titulo": f"4) Especiales ALTO DIVIDENDO {PRECIO_VALORES}",
                          "texto": venta_valores(menu, PRECIO_VALORES), "foto": "qr", "boton": "contacto"})
            ml = opciones.get("min_ultimo", 30)
            items.append({"cuando": cuando(primera - datetime.timedelta(minutes=ml)),
                          "destino": "gratis", "titulo": "5) Último llamado especiales ALTO DIVIDENDO",
                          "texto": ultimo_llamado_valores(menu, PRECIO_VALORES, ml), "foto": "qr", "boton": "contacto"})
    items.sort(key=lambda x: x["cuando"])
    if prueba:  # reordenar con 20 s entre cada uno respetando el orden logico
        for k, it in enumerate(items):
            it["cuando"] = ahora + paso * k
    return items


def html_a_texto(t):
    return html.unescape(_re.sub(r"<[^>]+>", "", t))


def mensaje_verde(etiqueta, texto, detalle_caballo, pago=""):
    cabecera = f"✅✅✅✅<b>¡{esc(etiqueta)}!</b>✅✅✅✅"
    if not texto and es_rata(etiqueta):
        texto = ("¡JUGARON SU RATA! A ESTA LE DOLÍAN LAS PATAS… "
                 + ((detalle_caballo.strip().rstrip(".") + ". ") if detalle_caballo else "")
                 + "El favorito vulnerable LO MARCAMOS EN ROJO 🔴 y no pudo ganar, tal como lo avisamos. "
                 "¡Seguimos trabajando!")
    if not texto:
        texto = TEXTO_VERDE_DEFECTO.format(
            etiqueta=etiqueta,
            detalle_caballo=(detalle_caballo.strip().rstrip(".") + ". ") if detalle_caballo else "",
        )
    salida = f"{cabecera}\n\n"
    if pago:
        salida += f"\U0001F4B0 <b>PAGÓ {esc(pago)}</b>\n\n"
    return salida + f"<i>{esc(texto)}</i>"


def mensaje_venta(clave, oferta, monto, contacto, texto_libre, resena=""):
    """Devuelve (texto, url_del_boton). El pago es por QR de Binance; el contacto recibe el comprobante."""
    contacto = contacto or VIP_LINK
    if clave == "especial" and not monto:
        monto = VALOR_ESPECIAL
    url = url_publica(contacto)
    visible = esc(contacto_visible(contacto))
    linea_contacto = f" a {visible}" if contacto else ""
    pago = (f"\U0001F4B3 Pago en USDT · Red {esc(USDT_RED)} (QR)\n<code>{esc(USDT_DIRECCION)}</code>"
            + (f"\n\U0001F4E9 Comprobante a: {visible}" if contacto else ""))
    plantilla = PLANTILLAS_VENTA[clave]["texto"]
    return plantilla.format(
        linea_monto=(f"\U0001F449<b>NOS JUGAMOS {esc(monto)}!</b>\U0001F525\n" if monto else ""),
        linea_valor=(f" ({esc(monto)})" if monto else ""),
        link=f"{pago}\n\n",
        link_final=f"\n\n{pago}",
        linea_oferta=(f" (<b>{esc(oferta)}</b>)" if oferta else ""),
        red=esc(USDT_RED),
        direccion=esc(USDT_DIRECCION),
        linea_pay_id=(f"\n   Binance Pay ID: <code>{esc(BINANCE_PAY_ID)}</code>" if BINANCE_PAY_ID else ""),
        linea_contacto=linea_contacto,
        canal=esc(CANAL_NOMBRE),
        oferta=esc(oferta),
        texto_libre=esc(texto_libre),
        linea_resena=(f"\U0001F4DD <i>{esc(resena)}</i>\n\n" if resena else ""),
    ), url


# ==========================================
# DESPACHO VERIFICADO
# ==========================================
def llamar_telegram(metodo, datos, archivos=None):
    """Llama a la API de Telegram y CONFIRMA la respuesta. Reintenta ante fallos transitorios."""
    url = TELEGRAM_API.format(token=TELEGRAM_TOKEN, metodo=metodo)
    ultimo_error = "no se intento"
    intentos_hechos = 0

    for intento in range(1, MAX_INTENTOS + 1):
        intentos_hechos = intento
        try:
            if archivos:
                form = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                        for k, v in datos.items()}
                adjuntos = {k: (nombre, contenido, tipo) for k, (nombre, contenido, tipo) in archivos.items()}
                resp = requests.post(url, data=form, files=adjuntos, timeout=TIMEOUT_SEG)
            else:
                resp = requests.post(url, json=datos, timeout=TIMEOUT_SEG)
        except requests.RequestException as exc:
            ultimo_error = f"fallo de red: {type(exc).__name__}"
            continue

        try:
            cuerpo = resp.json()
        except ValueError:
            ultimo_error = f"respuesta no-JSON de Telegram (HTTP {resp.status_code})"
            continue

        if resp.status_code == 200 and cuerpo.get("ok") is True:
            resultado = cuerpo.get("result")
            message_id = resultado.get("message_id") if isinstance(resultado, dict) else None
            return {"entregado": True, "message_id": message_id,
                    "intentos": intentos_hechos, "error": None}

        codigo = cuerpo.get("error_code", resp.status_code)
        ultimo_error = f"HTTP {resp.status_code} · error_code {codigo} · {cuerpo.get('description', 'sin descripcion')}"
        if codigo in ERRORES_PERMANENTES:
            break

    return {"entregado": False, "message_id": None, "intentos": intentos_hechos, "error": ultimo_error}


def enviar_a_chat(chat_id, texto, reply_markup=None, foto=None, foto_url=None,
                  animacion=None, fijar=False):
    """Envia el mensaje principal (texto o foto+texto) y devuelve su resultado verificado.

    animacion: GIF previo (no bloquea si falla). fijar: fija el mensaje principal.
    """
    extras = []
    if animacion:
        r = llamar_telegram("sendAnimation", {"chat_id": chat_id, "animation": animacion})
        extras.append("gif OK" if r["entregado"] else f"gif FALLO ({r['error']})")

    base = {"chat_id": chat_id, "parse_mode": "HTML"}
    if reply_markup:
        base["reply_markup"] = reply_markup

    if foto or foto_url:
        if texto and len(texto) <= LIMITE_CAPTION:
            datos = dict(base, caption=texto)
            texto_aparte = None
        else:
            datos = {"chat_id": chat_id}
            texto_aparte = texto
        if foto:
            res = llamar_telegram("sendPhoto", datos, {"photo": foto})
        else:
            res = llamar_telegram("sendPhoto", dict(datos, photo=foto_url))
        if res["entregado"] and texto_aparte:
            res = llamar_telegram("sendMessage", dict(base, text=texto_aparte,
                                                      disable_web_page_preview=True))
    else:
        res = llamar_telegram("sendMessage", dict(base, text=texto, disable_web_page_preview=True))

    if fijar and res["entregado"] and res["message_id"]:
        r = llamar_telegram("pinChatMessage", {"chat_id": chat_id, "message_id": res["message_id"],
                                               "disable_notification": True})
        extras.append("fijado OK" if r["entregado"] else f"fijar FALLO ({r['error']})")

    res["extras"] = extras
    return res


def despachar_telegram(destino, mensajes, **kwargs_por_grupo):
    """mensajes: {"gratis": texto, "vip": texto}. kwargs_por_grupo: {"gratis": {...}, "vip": {...}}."""
    objetivos = []
    if destino in ("gratis", "ambos"):
        objetivos.append(("gratis", CHAT_ID_GRATIS))
    if destino in ("vip", "ambos"):
        objetivos.append(("vip", CHAT_ID_VIP))

    resultados = []
    for nombre, chat_id in objetivos:
        if not chat_id:
            resultados.append({"grupo": nombre, "chat_id": None, "entregado": False,
                               "message_id": None, "intentos": 0, "extras": [],
                               "error": f"CHAT_ID_{nombre.upper()} no configurado"})
            continue
        res = enviar_a_chat(chat_id, mensajes.get(nombre, ""), **kwargs_por_grupo.get(nombre, {}))
        res["grupo"] = nombre
        res["chat_id"] = chat_id
        resultados.append(res)
    return resultados


def despachar_whatsapp(mensaje, destino):
    if not WHATSAPP_API_KEY or not WHATSAPP_PHONE or WHATSAPP_API_KEY == "pendiente":
        return None
    if destino not in ("vip", "ambos"):
        return None
    url = ("https://api.callmebot.com/whatsapp.php"
           f"?phone={WHATSAPP_PHONE}&text={requests.utils.quote(mensaje)}&apikey={WHATSAPP_API_KEY}")
    try:
        resp = requests.get(url, timeout=TIMEOUT_SEG)
        return {"entregado": resp.status_code == 200, "status": resp.status_code}
    except requests.RequestException as exc:
        return {"entregado": False, "status": f"fallo de red: {type(exc).__name__}"}


def guardar_en_registro(fecha, evento, pronostico, cuota, unidades, destino, resultados):
    """Historial. Ojo: en Render el disco se borra en cada reinicio/deploy."""
    entregados = [r["grupo"] for r in resultados if r["entregado"]]
    fallidos = [f"{r['grupo']}({r['error']})" for r in resultados if not r["entregado"]]
    estado = "ENTREGADO" if resultados and not fallidos else "FALLIDO"
    ids = ";".join(str(r["message_id"]) for r in resultados if r["message_id"])
    existe = os.path.exists(REGISTRO_PATH)
    try:
        with open(REGISTRO_PATH, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not existe:
                writer.writerow(["Fecha", "Evento", "Pronostico", "Cuota", "Unidades",
                                 "Grupo Destino", "Estado", "Entregados", "Fallidos", "Message IDs"])
            writer.writerow([fecha, evento, pronostico, cuota, unidades, destino,
                             estado, ",".join(entregados), ",".join(fallidos), ids])
    except OSError:
        pass


def resumir(resultados):
    partes = []
    for r in resultados:
        extra = f" [{', '.join(r['extras'])}]" if r.get("extras") else ""
        if r["entregado"]:
            partes.append(f"{r['grupo']}: OK (message_id {r['message_id']}, intento {r['intentos']}){extra}")
        else:
            partes.append(f"{r['grupo']}: FALLO -> {r['error']}{extra}")
    return " | ".join(partes)


# ==========================================
# RESPUESTAS COMUNES
# ==========================================
def _picks_jornada():
    j = leer_jornada()
    if not j:
        return []
    return [(n, p["titulo"], es_rata(p["etiqueta"])) for n, p in enumerate(picks_menu(parsear_menu(j["menu"])))]


def render_panel(entregado_todo=False, detalle="", faltantes=None, status=200, **extra):
    return render_template_string(
        PAGINA_INICIO,
        entregado_todo=entregado_todo,
        detalle=detalle,
        faltantes=faltantes or [],
        avisos=avisos_config(),
        canal=CANAL_NOMBRE,
        vip_link=url_publica(VIP_LINK),
        descripcion=texto_descripcion(),
        valor_especial=VALOR_ESPECIAL,
        etiquetas=ETIQUETAS,
        tipos=TIPOS_APUESTA,
        plantillas=PLANTILLAS_VENTA,
        programacion_defecto=PROGRAMACION_DEFECTO,
        nota_horarios=NOTA_HORARIOS,
        hay_banner=bool(IMG_MENU_DEL_DIA),
        hay_gif=bool(GIF_NUEVO_PRONOSTICO or GIF_GRATIS),
        vista=extra.get("vista"),
        menu_texto=extra.get("menu_texto", ""),
        menu_nota=extra.get("menu_nota", NOTA_HORARIOS),
        cola=cola_para_panel(),
        programado=extra.get("programado", ""),
        resultados_picks=_picks_jornada(),
    ), status


def leer_datos():
    quiere_json = request.is_json
    datos = request.get_json(silent=True) if quiere_json else None
    if not isinstance(datos, dict):
        datos = request.form.to_dict()
    return quiere_json, datos


def campo(datos, nombre, defecto=""):
    valor = datos.get(nombre, defecto)
    return ("" if valor is None else str(valor)).strip()


def marcado(datos, nombre):
    return campo(datos, nombre).lower() in ("1", "true", "si", "sí", "on", "yes")


def chequeo_previo():
    """Devuelve una respuesta de error si no se puede enviar, o None si todo bien."""
    if not auth_ok():
        return respuesta_no_autorizado()
    faltantes = config_faltante()
    if faltantes:
        msg = f"Configuracion incompleta en el servidor: {', '.join(faltantes)}"
        if request.is_json:
            return jsonify({"ok": False, "entregado": False, "error": msg}), 503
        return render_panel(detalle=msg, faltantes=faltantes, status=503)
    return None


def responder_error(quiere_json, errores, status=422):
    if quiere_json:
        return jsonify({"ok": False, "entregado": False, "errores": errores}), status
    return render_panel(detalle=" · ".join(errores), status=status)


def responder_envio(quiere_json, resultados, extra=None):
    entregado_todo = bool(resultados) and all(r["entregado"] for r in resultados)
    detalle = resumir(resultados)
    status = 200 if entregado_todo else 502
    if quiere_json:
        cuerpo = {"ok": entregado_todo, "entregado": entregado_todo,
                  "resultados": resultados, "detalle": detalle}
        cuerpo.update(extra or {})
        return jsonify(cuerpo), status
    return render_panel(entregado_todo=entregado_todo, detalle=detalle, status=status)


def validar_destino(tipo_grupo, errores, permitir_auto=False):
    validos = ("ambos", "vip", "gratis", "auto") if permitir_auto else ("ambos", "vip", "gratis")
    if tipo_grupo not in validos:
        errores.append(f"tipo_grupo invalido: '{tipo_grupo}' (usa {' | '.join(validos)})")


def validar_fuga(errores, *textos):
    fugas = detectar_fuga(*textos)
    if fugas:
        errores.append("el texto destinado al cliente contiene datos internos "
                       f"({', '.join(fugas)}). Envio bloqueado.")


def leer_foto(errores, obligatoria=False):
    archivo = request.files.get("foto")
    if archivo and archivo.filename:
        contenido = archivo.read()
        if contenido:
            return (archivo.filename, contenido, archivo.mimetype or "image/jpeg")
    if obligatoria:
        errores.append("falta la foto")
    return None


# ==========================================
# RUTAS
# ==========================================
@app.before_request
def _asegurar_hilo():
    arrancar_hilo_cola()


@app.route("/", methods=["GET"])
def inicio():
    if not auth_ok():
        return respuesta_no_autorizado()
    return render_panel(faltantes=config_faltante())


@app.route("/health", methods=["GET"])
def health():
    faltantes = config_faltante()
    return jsonify({
        "servicio": "activo",
        "canal": CANAL_NOMBRE,
        "configuracion_completa": not faltantes,
        "faltantes": faltantes,
        "vip_link_configurado": bool(url_publica(VIP_LINK)),
        "contacto_info": url_publica(CONTACTO_INFO),
        "bono_configurado": bool(url_publica(BONO_LINK)),
        "banner_menu": bool(IMG_MENU_DEL_DIA),
        "gif_pronostico": bool(GIF_NUEVO_PRONOSTICO or GIF_GRATIS),
    }), 200


@app.route("/menu-vista", methods=["POST"])
def vista_menu():
    if not auth_ok():
        return respuesta_no_autorizado()
    texto = request.form.get("menu", "")
    nota = request.form.get("nota", NOTA_HORARIOS)
    menu = parsear_menu(texto)
    if not menu["hipodromos"]:
        return render_panel(detalle="No se encontró ningún hipódromo (🏇) con picks en el menú pegado.",
                            menu_texto=texto, menu_nota=nota, status=422)
    picks = picks_menu(menu)
    gratis = []
    for n, p in enumerate(picks):
        if p["destino"] == "gratis":
            c = momento_carrera(p["item"]["hora"])
            sale = a_local(c - datetime.timedelta(minutes=60)).strftime("%I:%M %p").lstrip("0") if c else "?"
            gratis.append((n, p["titulo"], f"{sale} si es para hoy"))
    vista = {
        "publico": html_a_texto(menu_publico(menu, nota, ahora_local(), VALOR_ESPECIAL)),
        "promo": html_a_texto(promo_vip(menu, PRECIO_VALORES, PRECIO_DIA) or ""),
        "valores": html_a_texto(venta_valores(menu, PRECIO_VALORES) or ""),
        "precio_dia": PRECIO_DIA,
        "precio_valores": PRECIO_VALORES,
        "gratis": gratis,
        "vip": [(n, p["titulo"]) for n, p in enumerate(picks) if p["destino"] == "vip"],
    }
    return render_panel(vista=vista, menu_texto=texto, menu_nota=nota)


def _entero(valor, defecto):
    try:
        return max(0, int(str(valor).strip()))
    except (TypeError, ValueError):
        return defecto


@app.route("/webhook-menu", methods=["POST"])
def publicar_menu():
    """Programa la jornada. JSON: {"menu": "...", "prueba": false} (todo marcado por defecto)."""
    error = chequeo_previo()
    if error:
        return error
    quiere_json, datos = leer_datos()
    texto = campo(datos, "menu")
    nota = (datos.get("nota", NOTA_HORARIOS) if quiere_json else request.form.get("nota", NOTA_HORARIOS))
    menu = parsear_menu(texto)
    if not menu["hipodromos"]:
        return responder_error(quiere_json, ["no se encontró ningún hipódromo con picks en el menú"])
    validar = []
    validar_fuga(validar, texto)
    if validar:
        return responder_error(quiere_json, validar)

    picks = picks_menu(menu)
    if quiere_json:
        todos = list(range(len(picks)))
        opciones = {"menu": datos.get("pub_menu", True), "promo": datos.get("pub_promo", True),
                    "valores": datos.get("pub_valores", True), "prueba": bool(datos.get("prueba")),
                    "free": todos, "vip": todos}
    else:
        lista = lambda k: [int(x) for x in request.form.getlist(k) if x.isdigit()]
        opciones = {"menu": marcado(datos, "pub_menu"), "promo": marcado(datos, "pub_promo"),
                    "valores": marcado(datos, "pub_valores"), "prueba": marcado(datos, "prueba"),
                    "free": lista("free"), "vip": lista("vip")}
    opciones.update(min_promo=_entero(datos.get("min_promo"), 15), min_free=_entero(datos.get("min_free"), 60),
                    min_valores=_entero(datos.get("min_valores"), 120), min_ultimo=_entero(datos.get("min_ultimo"), 30))

    items = planificar_jornada(menu, nota, opciones)
    if not items:
        return responder_error(quiere_json, ["no hay nada que programar (¿las carreras ya pasaron? usa MODO PRUEBA)"])
    encolar(items)
    guardar_jornada(texto, nota)
    arrancar_hilo_cola()
    procesar_cola()  # lo que toca ya (menu, VIP) sale en este momento

    resumen = [f"{a_local(it['cuando']).strftime('%I:%M %p').lstrip('0')} · {it['titulo'][:60]}" for it in items]
    if quiere_json:
        return jsonify({"ok": True, "programados": len(items), "plan": resumen}), 200
    return render_panel(programado=f"Jornada programada: {len(items)} mensajes. Abajo ves la hora de cada uno "
                                   "y si ya salió (✅) o falló (❌).")


@app.route("/webhook-resultados", methods=["POST"])
def publicar_resultados():
    """Festejos ✅, marcas ❌ y resumen. Form: res_N = gano|perdio, pago_N. JSON igual."""
    error = chequeo_previo()
    if error:
        return error
    quiere_json, datos = leer_datos()
    jornada = leer_jornada()
    texto = campo(datos, "menu") or (jornada or {}).get("menu", "")
    menu = parsear_menu(texto)
    picks = picks_menu(menu)
    if not picks:
        return responder_error(quiere_json, ["no hay jornada guardada: programa primero el menú del día"])

    marcados = []
    for n, p in enumerate(picks):
        res = campo(datos, f"res_{n}").lower()
        if res in ("gano", "perdio"):
            marcados.append((p, res, campo(datos, f"pago_{n}")))
    if not marcados:
        return responder_error(quiere_json, ["marca al menos un pick con ✅ o ❌"])
    fijar = marcado(datos, "fijar")

    resultados = []
    for p, res, pago in marcados:
        foto = imagen_etiqueta(p["etiqueta"]) if (res == "gano" and es_rata(p["etiqueta"])) else None
        if res == "gano":
            texto_m = festejo(p, pago)
            destino = "gratis" if p["destino"] == "gratis" else "ambos"   # los verdes del VIP tambien se festejan en el gratis
            op = {"foto": foto, "fijar": fijar, "reply_markup": teclado_contacto(con_comprobante=False)}
        else:
            texto_m = marca_negativa(p)
            destino = p["destino"]                                     # el ❌ va donde se dio el pick
            op = {}
        texto_vip = festejo(p, pago, cta=False) if res == "gano" else texto_m
        for r in despachar_telegram(destino, {"gratis": texto_m, "vip": texto_vip}, gratis=op, vip=op):
            r["grupo"] = f"{'✅' if res == 'gano' else '❌'} {r['grupo']} {_corta(p['etiqueta'])[0]}"
            resultados.append(r)
    if marcado(datos, "resumen") or (quiere_json and datos.get("resumen", True)):
        resumen = resumen_resultados(menu, [(p, r) for p, r, _ in marcados])
        for r in despachar_telegram("gratis", {"gratis": resumen},
                                    gratis={"reply_markup": teclado_contacto(con_comprobante=False)}):
            r["grupo"] = "📊 resumen gratis"
            resultados.append(r)
    return responder_envio(quiere_json, resultados)


@app.route("/tick", methods=["GET", "POST"])
def tick():
    """Para UptimeRobot / cron-job.org cada 5 min: mantiene despierto el servicio y envia lo pendiente."""
    arrancar_hilo_cola()
    n = procesar_cola()
    pendientes = sum(1 for c in _leer_cola() if c["estado"] == "pendiente")
    return jsonify({"ok": True, "enviados_ahora": n, "pendientes": pendientes}), 200


@app.route("/cola-cancelar", methods=["POST"])
def cancelar_cola():
    if not auth_ok():
        return respuesta_no_autorizado()
    with _CandadoCola():
        cola = _leer_cola()
        n = 0
        for c in cola:
            if c["estado"] == "pendiente":
                c["estado"] = "cancelado"
                n += 1
        _guardar_cola(cola)
    return render_panel(programado=f"Cancelados {n} mensajes pendientes.")


@app.route("/webhook-programacion", methods=["POST"])
def recibir_programacion():
    error = chequeo_previo()
    if error:
        return error
    quiere_json, datos = leer_datos()
    lineas = campo(datos, "lineas") or PROGRAMACION_DEFECTO
    nota = campo(datos, "nota", NOTA_HORARIOS)
    tipo_grupo = campo(datos, "tipo_grupo", "gratis").lower()

    errores = []
    validar_destino(tipo_grupo, errores)
    validar_fuga(errores, lineas, nota, campo(datos, "resena"))
    if errores:
        return responder_error(quiere_json, errores)

    texto = mensaje_programacion(lineas, nota, ahora_local(), campo(datos, "valor") or VALOR_ESPECIAL,
                                 campo(datos, "resena"))
    if marcado(datos, "con_banner") and IMG_MENU_DEL_DIA:
        # el banner va primero como mensaje aparte (igual que el canal modelo)
        for grupo, chat in (("gratis", CHAT_ID_GRATIS), ("vip", CHAT_ID_VIP)):
            if tipo_grupo in (grupo, "ambos"):
                llamar_telegram("sendPhoto", {"chat_id": chat, "photo": IMG_MENU_DEL_DIA})

    opciones = {"reply_markup": teclado_contacto(con_comprobante=False)}
    resultados = despachar_telegram(tipo_grupo, {"gratis": texto, "vip": texto},
                                    gratis=opciones, vip=opciones)
    return responder_envio(quiere_json, resultados, {"tipo_grupo": tipo_grupo})


@app.route("/webhook-descripcion", methods=["POST"])
def actualizar_descripcion():
    """Pone DESCRIPCION_CANAL + contacto como descripcion de los canales (el bot debe ser admin)."""
    error = chequeo_previo()
    if error:
        return error
    quiere_json, datos = leer_datos()
    tipo_grupo = campo(datos, "tipo_grupo", "ambos").lower()
    errores = []
    validar_destino(tipo_grupo, errores)
    if errores:
        return responder_error(quiere_json, errores)
    descripcion = texto_descripcion()
    resultados = []
    for grupo, chat in (("gratis", CHAT_ID_GRATIS), ("vip", CHAT_ID_VIP)):
        if tipo_grupo in (grupo, "ambos"):
            r = llamar_telegram("setChatDescription", {"chat_id": chat, "description": descripcion})
            if not r["entregado"] and "not modified" in (r["error"] or ""):
                r = {"entregado": True, "message_id": None, "intentos": r["intentos"], "error": None}
            r.update(grupo=grupo, chat_id=chat, extras=["descripcion actualizada"] if r["entregado"] else [])
            resultados.append(r)
    return responder_envio(quiere_json, resultados, {"descripcion": descripcion})


@app.route("/webhook-pick", methods=["POST"])
def recibir_pick():
    error = chequeo_previo()
    if error:
        return error
    quiere_json, datos = leer_datos()

    etiqueta = campo(datos, "etiqueta", "PARADA FREE 1")
    tipo_grupo = campo(datos, "tipo_grupo", "auto").lower()
    # Compatibilidad con la version anterior (evento / pronostico)
    hipodromo = campo(datos, "hipodromo") or campo(datos, "evento")
    carrera = campo(datos, "carrera")
    numero = campo(datos, "numero")
    caballo = campo(datos, "caballo")
    tipo_apuesta = campo(datos, "tipo_apuesta") or campo(datos, "pronostico")
    cuota = campo(datos, "cuota")
    unidades = campo(datos, "unidades", "5") or "5"
    analisis = campo(datos, "analisis")
    pago = campo(datos, "pago")

    errores = []
    validar_destino(tipo_grupo, errores, permitir_auto=True)
    if not etiqueta:
        errores.append("falta 'etiqueta'")
    if not hipodromo:
        errores.append("falta 'hipodromo'")
    if not carrera:
        errores.append("falta 'carrera'")
    if not (numero or caballo or tipo_apuesta):
        errores.append("falta el caballo (numero o nombre) o el tipo de apuesta")
    if not cuota:
        errores.append("falta 'cuota'")
    validar_fuga(errores, analisis, hipodromo, caballo, tipo_apuesta)
    if errores:
        return responder_error(quiere_json, errores)

    destino = resolver_destino(tipo_grupo, etiqueta)
    if es_stake_cero(unidades):
        etiqueta_msg = "⚠️ AVISO OPERATIVO"
    else:
        etiqueta = etiqueta_final(etiqueta, hipodromo)
        etiqueta_msg = etiqueta
    texto = mensaje_pick(etiqueta_msg, hipodromo, carrera, numero, caballo, tipo_apuesta,
                         cuota, unidades, analisis, pago)

    kwargs = {"gratis": {}, "vip": {}}
    if marcado(datos, "con_gif"):
        es_gratis = info_etiqueta(etiqueta)["destino"] == "gratis"
        gif = (GIF_GRATIS if es_gratis and GIF_GRATIS else GIF_NUEVO_PRONOSTICO) or GIF_GRATIS
        if gif:
            kwargs["gratis"]["animacion"] = gif
            kwargs["vip"]["animacion"] = gif
    if marcado(datos, "con_imagen") or ("con_imagen" not in datos and quiere_json):
        imagen = imagen_etiqueta(etiqueta)
        if imagen:
            kwargs["gratis"]["foto"] = imagen
            kwargs["vip"]["foto"] = imagen

    resultados = despachar_telegram(destino, {"gratis": texto, "vip": texto}, **kwargs)
    wa = despachar_whatsapp(texto, destino)

    fecha = ahora_local().strftime("%Y-%m-%d %H:%M")
    jugada = " ".join(p for p in (f"#{numero}" if numero else "", caballo, tipo_apuesta) if p)
    guardar_en_registro(fecha, f"{etiqueta} · {hipodromo} C{carrera}", jugada,
                        cuota, unidades, destino, resultados)

    return responder_envio(quiere_json, resultados, {
        "fecha": fecha, "etiqueta": etiqueta, "tipo_grupo": destino, "whatsapp": wa,
    })


@app.route("/webhook-resultado", methods=["POST"])
def recibir_resultado():
    error = chequeo_previo()
    if error:
        return error
    quiere_json, datos = leer_datos()
    etiqueta = campo(datos, "etiqueta", "STAKAZO 15")
    tipo_grupo = campo(datos, "tipo_grupo", "ambos").lower()
    texto_usuario = campo(datos, "texto")
    detalle_caballo = campo(datos, "detalle_caballo")
    foto_url = campo(datos, "foto_url")
    fijar = marcado(datos, "fijar")

    errores = []
    validar_destino(tipo_grupo, errores)
    validar_fuga(errores, texto_usuario, detalle_caballo)
    foto = None if quiere_json else leer_foto(errores)
    if not foto and not foto_url and es_rata(etiqueta):
        foto = imagen_etiqueta(etiqueta)
    if errores:
        return responder_error(quiere_json, errores)

    texto = mensaje_verde(etiqueta, texto_usuario, detalle_caballo, campo(datos, "pago"))
    opciones = {"foto": foto, "foto_url": foto_url or None, "fijar": fijar}
    resultados = despachar_telegram(tipo_grupo, {"gratis": texto, "vip": texto},
                                    gratis=opciones, vip=opciones)
    return responder_envio(quiere_json, resultados, {"etiqueta": etiqueta, "tipo_grupo": tipo_grupo})


@app.route("/webhook-venta", methods=["POST"])
def recibir_venta():
    error = chequeo_previo()
    if error:
        return error
    quiere_json, datos = leer_datos()
    clave = campo(datos, "plantilla", "principal").lower()
    tipo_grupo = campo(datos, "tipo_grupo", "gratis").lower()
    oferta = campo(datos, "oferta")
    monto = campo(datos, "monto")
    link = campo(datos, "contacto") or campo(datos, "link")
    texto_libre = campo(datos, "texto_libre")
    resena = campo(datos, "resena")

    errores = []
    validar_destino(tipo_grupo, errores)
    if clave not in PLANTILLAS_VENTA:
        errores.append(f"plantilla invalida: '{clave}' (usa {' | '.join(PLANTILLAS_VENTA)})")
    elif clave in ("reacciones", "ultimo") and not oferta:
        errores.append("este mensaje necesita la 'oferta' (ej: ESPECIAL ($10))")
    elif clave == "libre" and not texto_libre:
        errores.append("escribe el texto libre")
    validar_fuga(errores, oferta, monto, texto_libre, resena)
    if errores:
        return responder_error(quiere_json, errores)

    texto, url = mensaje_venta(clave, oferta, monto, link, texto_libre, resena)
    if url and url != url_publica(VIP_LINK):
        teclado = boton("\U0001F4E9 Enviar comprobante", url)
        info = url_publica(CONTACTO_INFO)
        if info and teclado:
            teclado["inline_keyboard"].append([{"text": "\U0001F4AC Info VIP · Reclamos · Afiliación", "url": info}])
    else:
        teclado = teclado_contacto()
    opciones = {"reply_markup": teclado}
    if "qr_form" in datos or "con_qr" in datos:   # el panel siempre manda qr_form
        con_qr = marcado(datos, "con_qr")
    else:
        con_qr = clave in ("especial", "principal", "pago")
    if con_qr:
        foto, foto_url = qr_binance()
        if not (foto or foto_url):
            return responder_error(quiere_json, [
                "no hay QR de Binance: sube binance_qr.png al repositorio o configura BINANCE_QR"])
        opciones.update(foto=foto, foto_url=foto_url)
    resultados = despachar_telegram(tipo_grupo, {"gratis": texto, "vip": texto},
                                    gratis=opciones, vip=opciones)
    return responder_envio(quiere_json, resultados, {"plantilla": clave, "tipo_grupo": tipo_grupo})


@app.route("/webhook-foto", methods=["POST"])
def recibir_foto():
    error = chequeo_previo()
    if error:
        return error
    quiere_json, datos = leer_datos()
    tipo_grupo = campo(datos, "tipo_grupo", "gratis").lower()
    texto = campo(datos, "texto")
    foto_url = campo(datos, "foto_url")

    errores = []
    validar_destino(tipo_grupo, errores)
    validar_fuga(errores, texto)
    foto = None if quiere_json else leer_foto(errores, obligatoria=not foto_url)
    if quiere_json and not foto_url:
        errores.append("falta 'foto_url'")
    if errores:
        return responder_error(quiere_json, errores)

    mensaje = esc(texto)
    opciones = {"foto": foto, "foto_url": foto_url or None}
    resultados = despachar_telegram(tipo_grupo, {"gratis": mensaje, "vip": mensaje},
                                    gratis=opciones, vip=opciones)
    return responder_envio(quiere_json, resultados, {"tipo_grupo": tipo_grupo})


if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=puerto)
