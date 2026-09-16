# -*- coding: utf-8 -*-
"""
Intelligence Suite — Panel MLB + despacho verificado a Telegram.

Cambios respecto a la version anterior:
  1. Secretos fuera del codigo: TELEGRAM_TOKEN / CHAT_ID_* / WEBHOOK_SECRET vienen del entorno.
  2. Entrega verificada de verdad: se lee el status HTTP y el campo "ok" de Telegram,
     se guarda el message_id y se reintenta una vez ante fallos transitorios.
     El banner verde (alerta-exito) SOLO aparece si Telegram confirmo cada chat.
  3. Endpoint y panel autenticados con WEBHOOK_SECRET (distinto del token de Telegram).
  4. parse_mode HTML con escapado: se acaban los 400 "can't parse entities".
  5. Backstop anti-fuga: se rechaza el envio si el analisis trae ficha tecnica o P registrada.
  6. El registro CSV se escribe DESPUES del envio y anota si llego o no.
"""
import os
import csv
import hmac
import html
import datetime

import requests
from flask import Flask, request, jsonify, render_template_string, Response

app = Flask(__name__)

# ==========================================
# CONFIGURACION — TODO DESDE EL ENTORNO
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID_GRATIS = os.environ.get("CHAT_ID_GRATIS", "").strip()
CHAT_ID_VIP = os.environ.get("CHAT_ID_VIP", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()

WHATSAPP_PHONE = os.environ.get("WHATSAPP_PHONE", "").strip()
WHATSAPP_API_KEY = os.environ.get("WHATSAPP_API_KEY", "").strip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRO_PATH = os.path.join(BASE_DIR, "registro.csv")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_SEG = 20
MAX_INTENTOS = 2

# Errores de Telegram que no tiene sentido reintentar (config rota, no fallo transitorio)
ERRORES_PERMANENTES = {400, 401, 403, 404}

# Datos internos que jamas deben llegar al cliente
PATRONES_PROHIBIDOS = (
    "ficha_tecnica",
    "ficha tecnica",
    "ficha técnica",
    "p registrada",
    "prob registrada",
    "no selecciona",
)


# ==========================================
# AUTENTICACION
# ==========================================
def auth_ok() -> bool:
    """Acepta header X-Webhook-Secret (automatizacion) o Basic auth (panel web).

    Falla cerrado: si WEBHOOK_SECRET no esta configurado, nadie pasa.
    """
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

    return Response(
        "Acceso restringido.",
        401,
        {"WWW-Authenticate": 'Basic realm="Intelligence Suite"'},
    )


# ==========================================
# PANEL WEB
# ==========================================
PAGINA_INICIO = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Panel - Inteligencia MLB</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #0f172a; margin: 0; padding: 20px; display: flex; justify-content: center; color: #f8fafc; }
        .container { background: #1e293b; padding: 30px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); width: 100%; max-width: 520px; }
        h2 { text-align: center; color: #38bdf8; margin-bottom: 25px; }
        label { font-weight: bold; color: #cbd5e1; display: block; margin-top: 15px; }
        input, select, textarea { width: 100%; padding: 12px; margin-top: 5px; background: #334155; border: 1px solid #475569; border-radius: 6px; color: white; font-size: 14px; box-sizing: border-box; }
        button { width: 100%; background: #0284c7; color: white; padding: 14px; border: none; border-radius: 6px; font-size: 16px; font-weight: bold; margin-top: 25px; cursor: pointer; transition: background 0.2s; }
        button:hover { background: #0369a1; }
        .footer { text-align: center; margin-top: 25px; font-size: 11px; color: #64748b; }
        .alerta-exito { background: #16a34a; color: white; padding: 12px; border-radius: 6px; text-align: center; font-weight: bold; margin-bottom: 15px; }
        .alerta-error { background: #b91c1c; color: white; padding: 12px; border-radius: 6px; text-align: left; font-weight: bold; margin-bottom: 15px; }
        .detalle { font-weight: normal; font-size: 12px; margin-top: 8px; line-height: 1.5; font-family: monospace; }
        .aviso-config { background: #b45309; color: white; padding: 12px; border-radius: 6px; font-size: 13px; margin-bottom: 15px; }
    </style>
</head>
<body>
    <div class="container">
        <h2>&#9918; Panel de Control MLB</h2>

        {#  Marcador legible por maquina. Existe SOLO cuando Telegram confirmo cada chat.
            Se usa este token y no la clase CSS: ".alerta-exito" aparece siempre en el
            <style> de la pagina, asi que buscar esa cadena da un falso positivo eterno. #}
        {% if entregado_todo %}<!-- ENTREGA_CONFIRMADA -->{% elif detalle %}<!-- ENTREGA_FALLIDA -->{% endif %}

        {% if faltantes %}
        <div class="aviso-config">
            &#9888; Configuracion incompleta en el servidor: {{ faltantes|join(', ') }}.
            No se puede enviar hasta corregirlo en las variables de entorno de Render.
        </div>
        {% endif %}

        {% if entregado_todo %}
        <div class="alerta-exito">
            &#9989; ENTREGADO Y CONFIRMADO POR TELEGRAM
            <div class="detalle">{{ detalle }}</div>
        </div>
        {% elif detalle %}
        <div class="alerta-error">
            &#10060; NO ENTREGADO
            <div class="detalle">{{ detalle }}</div>
        </div>
        {% endif %}

        <form action="/webhook-pick" method="POST">
            <label>Destino de Publicacion:</label>
            <select name="tipo_grupo">
                <option value="ambos">&#128260; Ambos Grupos</option>
                <option value="vip">&#128274; Grupo VIP Premium</option>
                <option value="gratis">&#128275; Grupo Gratis / Publico</option>
            </select>

            <label>Partido / Evento:</label>
            <input type="text" name="evento" placeholder="Ej: Yankees vs Dodgers" required>

            <label>Pronostico:</label>
            <input type="text" name="pronostico" placeholder="Ej: Yankees Ganador" required>

            <label>Cuota:</label>
            <input type="text" name="cuota" placeholder="Ej: 1.85" required>

            <label>Stake / Unidades:</label>
            <input type="text" name="unidades" value="1.0" required>

            <label>Analisis Tecnico:</label>
            <textarea name="analisis" rows="4" placeholder="Estadisticas de la jugada..." required></textarea>

            <button type="submit">&#128640; Registrar y Publicar Pick</button>
        </form>
        <div class="footer">Envio manual verificado &middot; el banner verde solo aparece si Telegram confirmo la entrega</div>
    </div>
</body>
</html>
"""


# ==========================================
# UTILIDADES
# ==========================================
def config_faltante():
    faltantes = []
    if not TELEGRAM_TOKEN:
        faltantes.append("TELEGRAM_TOKEN")
    if not CHAT_ID_GRATIS:
        faltantes.append("CHAT_ID_GRATIS")
    if not CHAT_ID_VIP:
        faltantes.append("CHAT_ID_VIP")
    if not WEBHOOK_SECRET:
        faltantes.append("WEBHOOK_SECRET")
    return faltantes


def esc(valor) -> str:
    """Escapa para parse_mode HTML de Telegram."""
    return html.escape("" if valor is None else str(valor), quote=False)


def detectar_fuga(*textos):
    """Devuelve los patrones internos encontrados en el texto destinado al cliente."""
    unido = " ".join(str(t or "") for t in textos).lower()
    return [p for p in PATRONES_PROHIBIDOS if p in unido]


def es_stake_cero(valor) -> bool:
    try:
        return float(str(valor).replace(",", ".")) == 0.0
    except (TypeError, ValueError):
        return False


def construir_plantilla_mensaje(etiqueta, fecha, evento, pronostico, cuota, unidades, analisis):
    return (
        f"{etiqueta}\n\n"
        f"\U0001F4C5 <b>Fecha:</b> {esc(fecha)}\n"
        f"\U0001F525 <b>Evento:</b> {esc(evento)}\n"
        f"\U0001F3AF <b>Pronóstico:</b> {esc(pronostico)}\n"
        f"\U0001F4C8 <b>Cuota:</b> {esc(cuota)}\n"
        f"\U0001F4B0 <b>Stake Sugerido:</b> {esc(unidades)} U\n\n"
        f"\U0001F4CB <b>Análisis Técnico:</b>\n{esc(analisis)}"
    )


# ==========================================
# DESPACHO VERIFICADO
# ==========================================
def enviar_a_chat(chat_id, mensaje):
    """Envia a un chat y CONFIRMA la entrega leyendo la respuesta de Telegram.

    Devuelve dict con entregado True/False, message_id y el error exacto si fallo.
    """
    url = TELEGRAM_API.format(token=TELEGRAM_TOKEN)
    payload = {
        "chat_id": chat_id,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    ultimo_error = "no se intento"
    intentos_hechos = 0

    for intento in range(1, MAX_INTENTOS + 1):
        intentos_hechos = intento
        try:
            resp = requests.post(url, json=payload, timeout=TIMEOUT_SEG)
        except requests.RequestException as exc:
            ultimo_error = f"fallo de red: {type(exc).__name__}"
            continue

        try:
            cuerpo = resp.json()
        except ValueError:
            ultimo_error = f"respuesta no-JSON de Telegram (HTTP {resp.status_code})"
            continue

        if resp.status_code == 200 and cuerpo.get("ok") is True:
            return {
                "entregado": True,
                "message_id": (cuerpo.get("result") or {}).get("message_id"),
                "intentos": intentos_hechos,
                "error": None,
            }

        codigo = cuerpo.get("error_code", resp.status_code)
        ultimo_error = f"HTTP {resp.status_code} · error_code {codigo} · {cuerpo.get('description', 'sin descripcion')}"

        if codigo in ERRORES_PERMANENTES:
            break  # token revocado, bot expulsado, chat_id malo: reintentar no ayuda

    return {
        "entregado": False,
        "message_id": None,
        "intentos": intentos_hechos,
        "error": ultimo_error,
    }


def despachar_telegram(mensaje, destino):
    objetivos = []
    if destino in ("gratis", "ambos"):
        objetivos.append(("gratis", CHAT_ID_GRATIS))
    if destino in ("vip", "ambos"):
        objetivos.append(("vip", CHAT_ID_VIP))

    resultados = []
    for nombre, chat_id in objetivos:
        if not chat_id:
            resultados.append({
                "grupo": nombre, "chat_id": None, "entregado": False,
                "message_id": None, "intentos": 0,
                "error": f"CHAT_ID_{nombre.upper()} no configurado",
            })
            continue
        res = enviar_a_chat(chat_id, mensaje)
        res["grupo"] = nombre
        res["chat_id"] = chat_id
        resultados.append(res)

    return resultados


def despachar_whatsapp(mensaje, destino):
    if not WHATSAPP_API_KEY or not WHATSAPP_PHONE or WHATSAPP_API_KEY == "pendiente":
        return None
    if destino not in ("vip", "ambos"):
        return None
    url = (
        "https://api.callmebot.com/whatsapp.php"
        f"?phone={WHATSAPP_PHONE}"
        f"&text={requests.utils.quote(mensaje)}"
        f"&apikey={WHATSAPP_API_KEY}"
    )
    try:
        resp = requests.get(url, timeout=TIMEOUT_SEG)
        return {"entregado": resp.status_code == 200, "status": resp.status_code}
    except requests.RequestException as exc:
        return {"entregado": False, "status": f"fallo de red: {type(exc).__name__}"}


def guardar_en_registro(fecha, evento, pronostico, cuota, unidades, destino, resultados):
    """Se escribe DESPUES del envio, con el resultado real de la entrega."""
    entregados = [r["grupo"] for r in resultados if r["entregado"]]
    fallidos = [f"{r['grupo']}({r['error']})" for r in resultados if not r["entregado"]]
    estado = "ENTREGADO" if resultados and not fallidos else "FALLIDO"
    ids = ";".join(str(r["message_id"]) for r in resultados if r["message_id"])

    existe = os.path.exists(REGISTRO_PATH)
    try:
        with open(REGISTRO_PATH, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not existe:
                writer.writerow([
                    "Fecha", "Evento", "Pronostico", "Cuota", "Unidades",
                    "Grupo Destino", "Estado", "Entregados", "Fallidos", "Message IDs",
                ])
            writer.writerow([
                fecha, evento, pronostico, cuota, unidades, destino,
                estado, ",".join(entregados), ",".join(fallidos), ids,
            ])
    except OSError:
        pass  # el registro nunca debe tumbar un envio ya realizado


def resumir(resultados):
    partes = []
    for r in resultados:
        if r["entregado"]:
            partes.append(f"{r['grupo']}: OK (message_id {r['message_id']}, intento {r['intentos']})")
        else:
            partes.append(f"{r['grupo']}: FALLO -> {r['error']}")
    return " | ".join(partes)


# ==========================================
# RUTAS
# ==========================================
@app.route("/", methods=["GET"])
def inicio():
    if not auth_ok():
        return respuesta_no_autorizado()
    return render_template_string(
        PAGINA_INICIO, entregado_todo=False, detalle="", faltantes=config_faltante()
    )


@app.route("/health", methods=["GET"])
def health():
    """Chequeo de configuracion. No revela ningun valor secreto."""
    faltantes = config_faltante()
    return jsonify({
        "servicio": "activo",
        "configuracion_completa": not faltantes,
        "faltantes": faltantes,
    }), 200


@app.route("/webhook-pick", methods=["POST"])
def recibir_pick():
    if not auth_ok():
        return respuesta_no_autorizado()

    quiere_json = request.is_json

    faltantes = config_faltante()
    if faltantes:
        msg = f"Configuracion incompleta en el servidor: {', '.join(faltantes)}"
        if quiere_json:
            return jsonify({"ok": False, "entregado": False, "error": msg}), 503
        return render_template_string(
            PAGINA_INICIO, entregado_todo=False, detalle=msg, faltantes=faltantes
        ), 503

    datos = request.get_json(silent=True) if quiere_json else None
    if not isinstance(datos, dict):
        datos = request.form.to_dict()

    evento = (datos.get("evento") or "").strip()
    pronostico = (datos.get("pronostico") or "").strip()
    cuota = (datos.get("cuota") or "").strip()
    unidades = str(datos.get("unidades", "1.0")).strip()
    analisis = (datos.get("analisis") or "").strip()
    tipo_grupo = (datos.get("tipo_grupo") or "ambos").strip().lower()

    # --- Validaciones que bloquean el envio ---
    errores = []
    if tipo_grupo not in ("ambos", "vip", "gratis"):
        errores.append(f"tipo_grupo invalido: '{tipo_grupo}' (usa ambos | vip | gratis)")
    if not evento:
        errores.append("falta 'evento'")
    if not pronostico:
        errores.append("falta 'pronostico'")
    if not cuota:
        errores.append("falta 'cuota'")

    fugas = detectar_fuga(analisis, evento, pronostico)
    if fugas:
        errores.append(
            "el texto destinado al cliente contiene datos internos "
            f"({', '.join(fugas)}). Envio bloqueado."
        )

    if errores:
        detalle = " · ".join(errores)
        if quiere_json:
            return jsonify({"ok": False, "entregado": False, "errores": errores}), 422
        return render_template_string(
            PAGINA_INICIO, entregado_todo=False, detalle=detalle, faltantes=[]
        ), 422

    fecha_hoy = datetime.date.today().strftime("%Y-%m-%d")

    if es_stake_cero(unidades):
        etiqueta = "⚠️ <b>AVISO OPERATIVO DE APUESTAS</b>"
        unidades_msg = "0"
    else:
        etiqueta = {
            "ambos": "\U0001F680 <b>PICK OFICIAL GLOBAL</b>",
            "vip": "\U0001F512 <b>EXCLUSIVO VIP PREMIUM</b>",
            "gratis": "\U0001F513 <b>FREE PICK ACTIVO</b>",
        }[tipo_grupo]
        unidades_msg = unidades

    mensaje = construir_plantilla_mensaje(
        etiqueta, fecha_hoy, evento, pronostico, cuota, unidades_msg,
        analisis or "Sin analisis detallado.",
    )

    resultados = despachar_telegram(mensaje, tipo_grupo)
    wa = despachar_whatsapp(mensaje, tipo_grupo)

    guardar_en_registro(
        fecha_hoy, evento, pronostico, cuota, unidades_msg, tipo_grupo, resultados
    )

    entregado_todo = bool(resultados) and all(r["entregado"] for r in resultados)
    detalle = resumir(resultados)

    if quiere_json:
        return jsonify({
            "ok": entregado_todo,
            "entregado": entregado_todo,
            "fecha": fecha_hoy,
            "evento": evento,
            "tipo_grupo": tipo_grupo,
            "resultados": resultados,
            "whatsapp": wa,
            "detalle": detalle,
        }), (200 if entregado_todo else 502)

    return render_template_string(
        PAGINA_INICIO, entregado_todo=entregado_todo, detalle=detalle, faltantes=[]
    ), (200 if entregado_todo else 502)


if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=puerto)
