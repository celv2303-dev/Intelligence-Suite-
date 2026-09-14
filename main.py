# -*- coding: utf-8 -*-
import os
import csv
import datetime
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# ==========================================
# CONFIGURACIÓN DE PLATAFORMAS ALTERNATIVAS
# ==========================================
# WhatsApp Gratis (CallMeBot)
WHATSAPP_PHONE = "TU_NUMERO_CON_CODIGO_DE_PAIS" 
WHATSAPP_API_KEY = "TU_API_KEY_DE_CALLMEBOT"

# Telegram 
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "TU_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "TU_CHAT_ID")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRO_PATH = os.path.join(BASE_DIR, "registro.csv")

def enviar_whatsapp_gratis(mensaje):
    url = f"https://callmebot.com{WHATSAPP_PHONE}&text={requests.utils.quote(mensaje)}&apikey={WHATSAPP_API_KEY}"
    try:
        response = requests.get(url)
        return response.status_code == 200
    except Exception as e:
        print(f"Error en WhatsApp: {e}")
        return False

def enviar_telegram(mensaje):
    if "TU_TOKEN" in TELEGRAM_TOKEN:
        return False
    url = f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
        return True
    except:
        return False

@app.route('/webhook-pick', methods=['GET', 'POST'])
def recibir_pick():
    if request.method == 'POST':
        datos = request.json or request.form
    else:
        datos = request.args

    evento = datos.get('evento')
    pronostico = datos.get('pronostico')
    cuota = datos.get('cuota')
    unidades = datos.get('unidades', '1.0')
    analisis = datos.get('analisis', 'Análisis en desarrollo...')

    if not evento or not pronostico or not cuota:
        return jsonify({"status": "error", "message": "Faltan datos obligatorios"}), 400

    fecha_hoy = datetime.date.today().strftime("%Y-%m-%d")

    existe_archivo = os.path.exists(REGISTRO_PATH)
    with open(REGISTRO_PATH, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not existe_archivo:
            writer.writerow(["Fecha", "Evento", "Pronostico", "Cuota", "Unidades"])
        writer.writerow([fecha_hoy, evento, pronostico, cuota, unidades])

    mensaje_vip = (
        f"⚾ *PICK OFICIAL MLB*\n\n"
        f"📅 *Fecha:* {fecha_hoy}\n"
        f"🔥 *Partido:* {evento}\n"
        f"🎯 *Pronóstico:* {pronostico}\n"
        f"📈 *Cuota:* {cuota}\n"
        f"💰 *Stake:* {unidades} U\n\n"
        f"📋 *Análisis:* {analisis}"
    )

    enviar_whatsapp_gratis(mensaje_vip)
    enviar_telegram(mensaje_vip)

    return jsonify({"status": "success", "message": "Pick enviado a tus redes VIP"}), 200

if __name__ == '__main__':
    puerto = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=puerto)
