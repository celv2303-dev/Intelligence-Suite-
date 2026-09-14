# -*- coding: utf-8 -*-
import os
import csv
import datetime
from flask import Flask, request, jsonify, render_template_string
import requests

app = Flask(__name__)

# ==========================================
# CONFIGURACIÓN DE REDES SOCIALES (VIP Y GRATIS)
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "TU_TOKEN_DE_BOT")
CHAT_ID_VIP = os.environ.get("TELEGRAM_CHAT_ID_VIP", "ID_CANAL_VIP")
CHAT_ID_GRATIS = os.environ.get("TELEGRAM_CHAT_ID_GRATIS", "ID_CANAL_GRATIS")

WHATSAPP_PHONE = "TU_NUMERO_CON_CODIGO_DE_PAIS" 
WHATSAPP_API_KEY = "TU_API_KEY_DE_CALLMEBOT"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRO_PATH = os.path.join(BASE_DIR, "registro.csv")

# 🖥️ DISEÑO DE LA PÁGINA DE INICIO (PANEL WEB MANUAL)
PAGINA_INICIO = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Panel - Inteligencia MLB</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #0f172a; margin: 0; padding: 20px; display: flex; justify-content: center; color: #f8fafc; }
        .container { background: #1e293b; padding: 30px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); width: 100%; max-width: 500px; }
        h2 { text-align: center; color: #38bdf8; margin-bottom: 25px; }
        label { font-weight: bold; color: #cbd5e1; display: block; margin-top: 15px; }
        input, select, textarea { width: 100%; padding: 12px; margin-top: 5px; background: #334155; border: 1px solid #475569; border-radius: 6px; color: white; font-size: 14px; }
        button { width: 100%; background: #0284c7; color: white; padding: 14px; border: none; border-radius: 6px; font-size: 16px; font-weight: bold; margin-top: 25px; cursor: pointer; transition: background 0.2s; }
        button:hover { background: #0369a1; }
        .footer { text-align: center; margin-top: 25px; font-size: 11px; color: #64748b; }
    </style>
</head>
<body>
    <div class="container">
        <h2>⚾ Panel de Control MLB</h2>
        <form action="/webhook-pick" method="POST">
            <label>Formato de Envío Manual:</label>
            <select name="modo_manual">
                <option value="formulario">Enviar un Pick Único</option>
                <option value="no_hay_picks">Enviar Alerta: "Hoy No Hay Picks"</option>
            </select>
            
            <label>Partido / Evento:</label>
            <input type="text" name="evento" placeholder="Ej: Yankees vs Dodgers">

            <label>Pronóstico:</label>
            <input type="text" name="pronostico" placeholder="Ej: Yankees Ganador">

            <label>Cuota:</label>
            <input type="text" name="cuota" placeholder="Ej: 1.85">

            <label>Stake / Unidades:</label>
            <input type="text" name="unidades" value="1.0">

            <label>Análisis Técnico:</label>
            <textarea name="analisis" rows="3" placeholder="Estadísticas de la jugada..."></textarea>

            <button type="submit">🚀 Ejecutar Manualmente</button>
        </form>
        <div class="footer">Servidor Activo Sincronizado para Recepción Automática a las 11:00 AM</div>
    </div>
</body>
</html>
"""

# ==========================================
# FUNCIONES DE DESPACHO DE MENSAJES
# ==========================================
def despachar_telegram(mensaje, destino):
    if "TU_TOKEN" in TELEGRAM_TOKEN: return
    
    chats = []
    if destino in ["gratis", "ambos"]: chats.append(CHAT_ID_GRATIS)
    if destino in ["vip", "ambos"]: chats.append(CHAT_ID_VIP)

    for chat_id in chats:
        url = f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}
        try: requests.post(url, json=payload)
        except: pass

def despachar_whatsapp(mensaje, destino):
    if "TU_API_KEY" in WHATSAPP_API_KEY: return
    # WhatsApp se suele enfocar principalmente en clientes VIP o alertas generales
    if destino in ["vip", "ambos"]:
        url = f"https://callmebot.com{WHATSAPP_PHONE}&text={requests.utils.quote(mensaje)}&apikey={WHATSAPP_API_KEY}"
        try: requests.get(url)
        except: pass

def guardar_en_registro(fecha, evento, pronostico, cuota, unidades, destino):
    existe = os.path.exists(REGISTRO_PATH)
    with open(REGISTRO_PATH, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not existe:
            writer.writerow(["Fecha", "Evento", "Pronostico", "Cuota", "Unidades", "Grupo Destino"])
        writer.writerow([fecha, evento, pronostico, cuota, unidades, destino])

def construir_plantilla_mensaje(etiqueta, fecha, evento, pronostico, cuota, unidades, analisis):
    return (
        f"{etiqueta} *MLB*\n\n"
        f"📅 *Fecha:* {fecha}\n"
        f"🔥 *Partido:* {evento}\n"
        f"🎯 *Pronóstico:* {pronostico}\n"
        f"📈 *Cuota:* {cuota}\n"
        f"💰 *Stake Sugerido:* {unidades} U\n\n"
        f"📋 *Análisis de Datos:* {analisis}"
    )

# ==========================================
# RUTAS DEL PROCESADOR LÓGICO
# ==========================================
@app.route('/', methods=['GET'])
def inicio():
    return render_template_string(PAGINA_INICIO)

@app.route('/webhook-pick', methods=['POST'])
def recibir_pick_automatico():
    datos_recibidos = request.json or request.form
    fecha_hoy = datetime.date.today().strftime("%Y-%m-%d")

    # MODO MANUAL: Si usas el botón de "No hay picks" en la web
    if datos_recibidos.get('modo_manual') == 'no_hay_picks' or datos_recibidos.get('status_picks') == 'vacio':
        mensaje_no_hay = (
            f"⚠️ *AVISO IMPORTANTE DE APUESTAS - MLB*\n\n"
            f"📅 *Fecha:* {fecha_hoy}\n\n"
            f"Nuestro algoritmo y equipo analítico han revisado la pizarra del día de hoy. "
            f"Debido a condiciones de lanzadores o líneas sin valor estadístico, **HOY NO SE REGISTRARÁN PICKS OFICIALES**.\n\n"
            f"¡Cuidamos tu banca! Nos vemos mañana con la mejor selección avanzada. 🔒"
        )
        despachar_telegram(mensaje_no_hay, "ambos")
        despachar_whatsapp(mensaje_no_hay, "ambos")
        return jsonify({"status": "success", "message": "Mensaje de pizarra vacía enviado"}), 200

    # Capturar la lista de picks enviados por tu otra app
    picks_lista = datos_recibidos.get('picks', [])

    # Si tu otra app envía los datos como un solo pick directo (no en lista), lo convertimos en lista
    if not picks_lista and datos_recibidos.get('evento'):
        picks_lista = [datos_recibidos]

    cantidad_picks = len(picks_lista)

    # PANORAMA 1: No llegaron picks desde el generador automatizado
    if cantidad_picks == 0:
        mensaje_no_hay = (
            f"⚠️ *MLB INFORME DE PIZARRA*\n\n"
            f"📅 *Fecha:* {fecha_hoy}\n\n"
            f"El software de Inteligencia Suite no ha detectado anomalías o ventajas matemáticas claras en los partidos de hoy. "
            f"Mantenemos disciplina estricta sin forzar jugadas. ¡Regresamos mañana! 🎯"
        )
        despachar_telegram(mensaje_no_hay, "ambos")
        despachar_whatsapp(mensaje_no_hay, "ambos")
        return jsonify({"status": "success", "message": "Aviso de no-picks publicado en ambos grupos"}), 200

    # PANORAMA 2: Se generó UN SOLO PICK de alta confianza (Se va a ambos grupos)
    elif cantidad_picks == 1:
        p = picks_lista[0]
        mensaje = construir_plantilla_mensaje(
            "🚀 *PICK OFICIAL GLOBAL*", fecha_hoy, p.get('evento'), 
            p.get('pronostico'), p.get('cuota'), p.get('unidades', '1.0'), p.get('analisis', 'Análisis en desarrollo...')
        )
        guardar_en_registro(fecha_hoy, p.get('evento'), p.get('pronostico'), p.get('cuota'), p.get('unidades', '1.0'), "Ambos")
        despachar_telegram(mensaje, "ambos")
        despachar_whatsapp(mensaje, "ambos")
        return jsonify({"status": "success", "message": "Unico pick despachado de forma masiva"}), 200

    # PANORAMA 3: Se generaron VARIOS PICKS (El 1ero es gancho gratis, los demás van al VIP)
    else:
        for indice, p in enumerate(picks_lista):
            # El primero se define como el gancho Gratuito
            if indice == 0:
                destino = "gratis"
                etiqueta = "🔓 *LÍNEA GRATUITA GANCHO*"
            else:
                destino = "vip"
                etiqueta = "🔒 *EXCLUSIVO VIP PREMIUM*"

            mensaje = construir_plantilla_mensaje(
                etiqueta, fecha_hoy, p.get('evento'), 
                p.get('pronostico'), p.get('cuota'), p.get('unidades', '1.0'), p.get('analisis', 'Análisis de valor premium.')
            )
            guardar_en_registro(fecha_hoy, p.get('evento'), p.get('pronostico'), p.get('cuota'), p.get('unidades', '1.0'), destino)
            despachar_telegram(mensaje, destino)
            despachar_whatsapp(mensaje, destino)

        return jsonify({"status": "success", "message": f"Estrategia de división ejecutada. {cantidad_picks} picks procesados."}), 200

if __name__ == '__main__':
    puerto = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=puerto)


