import json
import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import urllib.parse
import re
from datetime import datetime, timedelta
import time
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio

# =========================
# Configuración
# =========================
URL_LOGIN = "https://deportesweb.madrid.es/DeportesWeb/Login"
URL_HOME = "https://deportesweb.madrid.es/DeportesWeb/Home"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.5",
    "X-Requested-With": "XMLHttpRequest",
    "X-MicrosoftAjax": "Delta=true",
    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
    "Origin": "https://deportesweb.madrid.es",
    "Cache-Control": "no-cache",
    "Dnt": "1",
    "Sec-Gpc": "1",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

CLASES = [
    {"dia": "lunes", "hora": "15:45", "nombre": "Fitness"},
    {"dia": "lunes", "hora": "17:00", "nombre": "Pilates MesD"},
    {"dia": "lunes", "hora": "18:00", "nombre": "Entrenamiento en suspensión"},
    {"dia": "martes", "hora": "15:45", "nombre": "Fuerza en sala multitrabajo"},
    {"dia": "martes", "hora": "17:00", "nombre": "Entrenamiento Funcional"},
    {"dia": "miércoles", "hora": "15:45", "nombre": "Fuerza GAP"},
    {"dia": "miércoles", "hora": "17:00", "nombre": "Pilates MesD"},
    {"dia": "miércoles", "hora": "18:00", "nombre": "Entrenamiento en suspensión"},
    {"dia": "jueves", "hora": "15:45", "nombre": "Fuerza en sala multitrabajo"},
    {"dia": "jueves", "hora": "17:00", "nombre": "Entrenamiento Funcional"},
    {"dia": "viernes", "hora": "15:45", "nombre": "Pilates MesD"},
    {"dia": "viernes", "hora": "17:00", "nombre": "Entrenamiento Funcional"}
]

DIAS_SEMANA = {
    "lunes": 0, "martes": 1, "miércoles": 2, "jueves": 3,
    "viernes": 4, "sábado": 5, "domingo": 6
}

HORAS_ANTES_APERTURA = 49

# =========================
# Gestión de BD
# =========================

class DatabaseManager:
    def __init__(self, mongo_url: str):
        self.client = AsyncIOMotorClient(mongo_url)
        self.db = self.client["reservas_clases"]
        self.coleccion = self.db["clases_reservadas"]
        print("✅ Conectado a MongoDB")
    
    async def cargar_reservadas_recientes(self, dias_atras: int = 7):
        """Carga las clases ya reservadas para filtrarlas del plan"""
        fecha_inicio = (datetime.now() - timedelta(days=dias_atras)).strftime("%Y-%m-%d")
        cursor = self.coleccion.find({"fecha": {"$gte": fecha_inicio}})
        reservadas = await cursor.to_list(length=None)
        
        if reservadas:
            print(f"\n📚 Reservas en BD (últimos {dias_atras} días): {len(reservadas)}")
            for r in reservadas:
                print(f"   - {r['nombre']} | {r['fecha']} {r['hora']}")
        
        return reservadas
    
    async def guardar_reserva(self, clase: dict, fecha_clase: datetime) -> bool:
        """Guarda una reserva en la BD (llamar manualmente cuando confirmes la reserva)"""
        documento = {
            "nombre": clase["nombre"],
            "hora": clase["hora"],
            "dia": clase["dia"],
            "fecha": fecha_clase.strftime("%Y-%m-%d"),
            "timestamp": datetime.now()
        }
        
        existe = await self.coleccion.find_one({
            "nombre": documento["nombre"],
            "hora": documento["hora"],
            "fecha": documento["fecha"]
        })
        
        if not existe:
            await self.coleccion.insert_one(documento)
            print(f"💾 Guardada en BD: {documento['nombre']} - {documento['fecha']} {documento['hora']}")
            return True
        else:
            print(f"ℹ️ Ya existe en BD: {documento['nombre']} - {documento['fecha']} {documento['hora']}")
            return False
    
    def cerrar(self):
        self.client.close()
        print("👋 Conexión a MongoDB cerrada")

# =========================
# Cálculo de fechas
# =========================

def calcular_proxima_fecha_clase(dia_semana: str, hora: str) -> datetime:
    ahora = datetime.now()
    dia_num = DIAS_SEMANA[dia_semana.lower()]
    hora_int, minuto_int = map(int, hora.split(":"))
    
    dias_hasta = (dia_num - ahora.weekday() + 7) % 7
    
    if dias_hasta == 0:
        fecha_clase = ahora.replace(hour=hora_int, minute=minuto_int, second=0, microsecond=0)
        if ahora >= fecha_clase:
            dias_hasta = 7
    
    if dias_hasta == 0:
        fecha_clase = ahora.replace(hour=hora_int, minute=minuto_int, second=0, microsecond=0)
    else:
        fecha_clase = ahora + timedelta(days=dias_hasta)
        fecha_clase = fecha_clase.replace(hour=hora_int, minute=minuto_int, second=0, microsecond=0)
    
    return fecha_clase

def calcular_hora_apertura(fecha_clase: datetime) -> datetime:
    return fecha_clase - timedelta(hours=HORAS_ANTES_APERTURA)

def calcular_fecha_para_post(fecha_clase: datetime) -> str:
    hora_apertura = calcular_hora_apertura(fecha_clase)
    return hora_apertura.strftime("%Y-%m-%d")

async def preparar_plan_de_reservas(db_manager=None):
    ahora = datetime.now()
    plan = []
    
    # Solo considerar clases en los próximos 2 días completos (hasta el final del día +2)
    limite_fecha = (ahora + timedelta(days=2)).replace(hour=23, minute=59, second=59)
    
    reservadas = []
    if db_manager:
        reservadas = await db_manager.cargar_reservadas_recientes()
    
    for clase in CLASES:
        fecha_clase = calcular_proxima_fecha_clase(clase["dia"], clase["hora"])
        
        # Filtrar: solo clases dentro de los próximos 2 días completos
        if fecha_clase > limite_fecha:
            print(f"⏭️ Saltando {clase['nombre']} {fecha_clase.strftime('%d/%m')} {clase['hora']} (más de 2 días)")
            continue
        
        hora_apertura = calcular_hora_apertura(fecha_clase)
        fecha_para_post = calcular_fecha_para_post(fecha_clase)
        tiempo_hasta_apertura = (hora_apertura - ahora).total_seconds()
        
        ya_reservada = any(
            r["nombre"] == clase["nombre"] and
            r["hora"] == clase["hora"] and
            r["fecha"] == fecha_clase.strftime("%Y-%m-%d")
            for r in reservadas
        )
        
        if ya_reservada:
            print(f"⏭️ Saltando {clase['nombre']} {fecha_clase.strftime('%d/%m')} {clase['hora']} (ya en BD)")
            continue
        
        plan.append({
            "clase": clase,
            "fecha_clase": fecha_clase,
            "hora_apertura": hora_apertura,
            "fecha_para_post": fecha_para_post,
            "tiempo_hasta_apertura": tiempo_hasta_apertura,
            "ya_abierta": tiempo_hasta_apertura <= 0
        })
    
    plan.sort(key=lambda x: x["hora_apertura"])
    return plan

def mostrar_plan_de_reservas(plan):
    print("\n" + "="*80)
    print("📅 PLAN DE RESERVAS")
    print("="*80)
    
    ahora = datetime.now()
    abiertas = sum(1 for p in plan if p["ya_abierta"])
    cerradas = len(plan) - abiertas
    
    print(f"\n📊 Resumen: {abiertas} abiertas 🟢 | {cerradas} cerradas 🔴\n")
    
    for i, item in enumerate(plan, 1):
        clase = item["clase"]
        fecha_clase = item["fecha_clase"]
        hora_apertura = item["hora_apertura"]
        ya_abierta = item["ya_abierta"]
        
        if ya_abierta:
            tiempo_pasado = abs(item["tiempo_hasta_apertura"])
            horas = int(tiempo_pasado // 3600)
            minutos = int((tiempo_pasado % 3600) // 60)
            estado = f"🟢 Abierta hace {horas}h {minutos}m"
        else:
            tiempo_restante = item["tiempo_hasta_apertura"]
            horas = int(tiempo_restante // 3600)
            minutos = int((tiempo_restante % 3600) // 60)
            estado = f"🔴 Abre en {horas}h {minutos}m"
        
        print(f"{i}. {estado}")
        print(f"   📍 {clase['nombre']}")
        print(f"   📅 {clase['dia'].capitalize()} {fecha_clase.strftime('%d/%m/%Y')} {clase['hora']}")
        print(f"   🔓 Abre: {hora_apertura.strftime('%d/%m/%Y %H:%M')}")
        print(f"   📤 POST: {item['fecha_para_post']}")
        print()
    
    print("="*80 + "\n")

# =========================
# Funciones ASP.NET
# =========================

def parse_initial_state(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    state = {
        "__VIEWSTATE": soup.find("input", {"id": "__VIEWSTATE"})["value"],
        "__VIEWSTATEGENERATOR": soup.find("input", {"id": "__VIEWSTATEGENERATOR"})["value"],
    }
    ev_tag = soup.find("input", {"id": "__EVENTVALIDATION"})
    if ev_tag:
        state["__EVENTVALIDATION"] = ev_tag["value"]
    return state

def extract_hidden_field(delta_text: str, field: str) -> str | None:
    token = f"|hiddenField|{field}|"
    if token not in delta_text:
        return None
    return delta_text.split(token, 1)[1].split("|", 1)[0]

def update_state_from_delta(state: dict, delta_text: str) -> None:
    for key in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        new_val = extract_hidden_field(delta_text, key)
        if new_val:
            state[key] = new_val

def is_login_success(response_text: str, session: requests.Session) -> bool:
    if "pageRedirect" in response_text:
        return True
    if "Token" in session.cookies.get_dict():
        return True
    return False

# =========================
# Navegación
# =========================

def select_facility(session: requests.Session, facility_code: str, facility_name: str, state: dict):
    r = session.get(URL_HOME, headers={**HEADERS, "Referer": URL_HOME})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    state["__VIEWSTATE"] = soup.find("input", {"id": "__VIEWSTATE"})["value"]
    state["__VIEWSTATEGENERATOR"] = soup.find("input", {"id": "__VIEWSTATEGENERATOR"})["value"]
    ev_tag = soup.find("input", {"id": "__EVENTVALIDATION"})
    if ev_tag:
        state["__EVENTVALIDATION"] = ev_tag["value"]

    script_manager = soup.find("input", {"id": "ctl00_ScriptManager1"})
    script_manager_value = (
        f"{script_manager['id']}|{script_manager['id'].replace('_', '$')}|ContentPlaceHolder1_UpdatePanel"
        if script_manager else "ctl00$ContentFixedSection$uSecciones$uAlert$uplAlert|ContentFixedSection_uSecciones_uAlert_uplAlert"
    )

    post_data = {
        "ctl00$ScriptManager1": script_manager_value,
        "__EVENTTARGET": "ContentFixedSection_uSecciones_uAlert_uplAlert",
        "__EVENTARGUMENT": json.dumps({
            "action": "SelectFacility",
            "args": {
                "facility_code": facility_code,
                "facility_name": facility_name,
                "submenu_code": None
            }
        }),
        "__ASYNCPOST": "true",
        **state
    }
    if "__EVENTVALIDATION" in state:
        post_data["__EVENTVALIDATION"] = state["__EVENTVALIDATION"]

    r = session.post(URL_HOME, data=post_data, headers={**HEADERS, "Referer": URL_HOME})
    r.raise_for_status()
    return r.text

def select_centro_menu_post(session: requests.Session, token: str, menu_code: str, menu_title: str, state: dict):
    url_centro = f"https://deportesweb.madrid.es/DeportesWeb/Centro?token={token}"
    r = session.get(url_centro, headers={"User-Agent": HEADERS["User-Agent"], "Referer": URL_HOME})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    state["__VIEWSTATE"] = soup.find("input", {"id": "__VIEWSTATE"})["value"]
    state["__VIEWSTATEGENERATOR"] = soup.find("input", {"id": "__VIEWSTATEGENERATOR"})["value"]
    ev_tag = soup.find("input", {"id": "__EVENTVALIDATION"})
    if ev_tag:
        state["__EVENTVALIDATION"] = ev_tag["value"]

    script_manager_value = "ctl00$ContentFixedSection$uCentro$uSecciones$uAlert$uplAlert|ContentFixedSection_uCentro_uSecciones_uAlert_uplAlert"
    post_data = {
        "ctl00$ScriptManager1": script_manager_value,
        "__EVENTTARGET": "ContentFixedSection_uCentro_uSecciones_uAlert_uplAlert",
        "__EVENTARGUMENT": json.dumps({
            "action": "SelectMenu",
            "args": {
                "menu_code": menu_code,
                "menu_title": menu_title,
                "menu_type": 9,
                "submenu_code": None
            }
        }),
        "__ASYNCPOST": "true",
        **state
    }

    r = session.post(url_centro, data=post_data, headers={**HEADERS, "Referer": url_centro})
    r.raise_for_status()
    return r.text

def get_alta_eventos(session: requests.Session, token: str, referer: str):
    url_alta_eventos = f"https://deportesweb.madrid.es/DeportesWeb/Modulos/VentaServicios/Eventos/AltaEventos?token={token}"
    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": referer,
        "Dnt": "1",
        "Upgrade-Insecure-Requests": "1",
    }

    r = session.get(url_alta_eventos, headers=headers)
    r.raise_for_status()
    return r.text

def extraer_cod_sesion(html_response: str, nombre_clase: str, hora_clase: str, fecha_esperada: str) -> dict | None:
    """
    Extrae el COD_SESION de una clase específica del HTML de respuesta.
    Solo devuelve resultados si la clase tiene plazas disponibles (> 0) y coincide la fecha.
    
    Args:
        html_response: HTML de respuesta del POST de eventos
        nombre_clase: Nombre de la clase a buscar (ej: "Pilates MesD")
        hora_clase: Hora de la clase a buscar (ej: "18:00")
        fecha_esperada: Fecha esperada de la clase en formato "YYYY-MM-DD"
    
    Returns:
        Dict con datos de la sesión si se encuentra, tiene plazas y coincide la fecha, None en caso contrario
    """
    # Buscar todos los bloques .on('click', {...}) con los datos de las clases
    # El patrón busca el objeto JavaScript con los datos de cada clase
    pattern = r"\.on\('click',\s*\{([^}]+)\}"
    
    matches = re.findall(pattern, html_response)
    
    for match in matches:
        # Parsear los datos del objeto JavaScript
        datos = {}
        # Extraer cada par clave: 'valor'
        pares = re.findall(r"(\w+):\s*'([^']*)'", match)
        for clave, valor in pares:
            datos[clave] = valor
        
        # Verificar si es la clase que buscamos (nombre, hora Y fecha)
        if (datos.get("NOM_EVENTO", "").lower() == nombre_clase.lower() and 
            datos.get("HORA_DESDE") == hora_clase and
            datos.get("FECHA") == fecha_esperada):
            # Ahora necesitamos verificar las plazas disponibles
            # El HTML tiene la estructura: .append('X').append('/Y') donde X son plazas disponibles
            # Buscamos el patrón específico cerca de este evento
            
            cod_sesion = datos.get("COD_SESION")
            if not cod_sesion:
                continue
            
            # Buscar las plazas disponibles para esta sesión
            # El patrón está en: .append($('<span/>'...font-weight: bold' }).append('X'))
            # seguido de .append($('<span/>'...font-weight: bold' }).append('/Y'))
            
            # Buscar el bloque completo que contiene este COD_SESION y extraer plazas
            idx = html_response.find(f"COD_SESION: '{cod_sesion}'")
            if idx == -1:
                continue
            
            # Buscar hacia adelante para encontrar las plazas (dentro de los próximos 500 caracteres)
            bloque = html_response[idx:idx+800]
            
            # Patrón para extraer plazas disponibles y totales
            # .append('20') seguido de .append('/20')
            plazas_pattern = r"\.append\('(\d+)'\)\s*\)\s*\.append\(\$\('<span/>'.*?\.append\('/(\d+)'\)"
            plazas_match = re.search(plazas_pattern, bloque)
            
            if plazas_match:
                plazas_disponibles = int(plazas_match.group(1))
                plazas_totales = int(plazas_match.group(2))
            else:
                # Intentar otro patrón más simple
                simple_pattern = r"\.append\('(\d+)'\).*?\.append\('/(\d+)'\)"
                simple_match = re.search(simple_pattern, bloque, re.DOTALL)
                if simple_match:
                    plazas_disponibles = int(simple_match.group(1))
                    plazas_totales = int(simple_match.group(2))
                else:
                    print(f"   ⚠️ No se pudieron extraer plazas para {nombre_clase}")
                    plazas_disponibles = 0
                    plazas_totales = 0
            
            # Verificar que hay plazas disponibles
            if plazas_disponibles <= 0:
                print(f"   ❌ {nombre_clase} a las {hora_clase}: Sin plazas disponibles (0/{plazas_totales})")
                return None
            
            print(f"   ✅ {nombre_clase} a las {hora_clase}: {plazas_disponibles}/{plazas_totales} plazas disponibles")
            
            return {
                "cod_sesion": cod_sesion,
                "cod_sala": datos.get("COD_SALA"),
                "nom_sala": datos.get("NOM_SALA"),
                "cod_evento": datos.get("COD_EVENTO"),
                "nom_evento": datos.get("NOM_EVENTO"),
                "fecha": datos.get("FECHA"),
                "hora_desde": datos.get("HORA_DESDE"),
                "hora_hasta": datos.get("HORA_HASTA"),
                "plazas_disponibles": plazas_disponibles,
                "plazas_totales": plazas_totales,
                "habilitar_limite_reservas": datos.get("HABILITAR_LIMITE_RESERVAS"),
                "limite_reservas": datos.get("LIMITE_RESERVAS"),
                "salas_multiples": datos.get("SALAS_MULTIPLES")
            }
    
    print(f"   ❌ No se encontró la clase '{nombre_clase}' a las {hora_clase} en fecha {fecha_esperada}")
    return None


def load_events_for_date(session: requests.Session, token: str, fecha: str, state: dict):
    """Carga los eventos de una fecha específica"""
    url_alta_eventos = f"https://deportesweb.madrid.es/DeportesWeb/Modulos/VentaServicios/Eventos/AltaEventos?token={token}"
    
    event_argument = json.dumps({
        "action": "Load",
        "args": {
            "availability": False,
            "date": fecha
        }
    })
    
    post_data = {
        "ctl00$ScriptManager1": "ctl00$ContentFixedSection$uAltaEventos$uAltaEventosFechas$uAlert$uplAlert|ContentFixedSection_uAltaEventos_uAltaEventosFechas_uAlert_uplAlert",
        "__EVENTTARGET": "ContentFixedSection_uAltaEventos_uAltaEventosFechas_uAlert_uplAlert",
        "__EVENTARGUMENT": event_argument,
        "__VIEWSTATE": state["__VIEWSTATE"],
        "__VIEWSTATEGENERATOR": state["__VIEWSTATEGENERATOR"],
        "ContentFixedSection_uAltaEventos_uAltaEventosFechas_availability_filter": "on",
        "__ASYNCPOST": "true",
    }
    
    if "__EVENTVALIDATION" in state:
        post_data["__EVENTVALIDATION"] = state["__EVENTVALIDATION"]
    
    encoded_data = urllib.parse.urlencode(post_data)
    content_length = len(encoded_data)
    
    print(f"\n{'='*60}")
    print(f"📅 Cargando eventos para: {fecha}")
    print(f"📊 Content-Length: {content_length} bytes")
    
    headers = {
        **HEADERS,
        "Referer": url_alta_eventos,
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
    }
    
    r = session.post(url_alta_eventos, data=post_data, headers=headers)
    r.raise_for_status()
    
    update_state_from_delta(state, r.text)
    
    print(f"✅ Respuesta recibida ({len(r.text)} bytes)")
    print(f"{'='*60}\n")
    
    return r.text


def seleccionar_clase(session: requests.Session, token: str, sesion_data: dict, person_code: str, state: dict):
    """
    Hace el POST para seleccionar/reservar una clase específica.
    
    Args:
        session: Sesión de requests
        token: Token de AltaEventos
        sesion_data: Datos de la sesión obtenidos de extraer_cod_sesion
        person_code: Código de persona del usuario
        state: Estado de ASP.NET (viewstate, etc.)
    
    Returns:
        Texto de la respuesta del servidor
    """
    url_alta_eventos = f"https://deportesweb.madrid.es/DeportesWeb/Modulos/VentaServicios/Eventos/AltaEventos?token={token}"
    
    event_argument = json.dumps({
        "action": "Seleccionar",
        "args": {
            "room_code": sesion_data["cod_sala"],
            "room_name": sesion_data["nom_sala"],
            "event_code": sesion_data["cod_evento"],
            "event_name": sesion_data["nom_evento"],
            "session_code": sesion_data["cod_sesion"],
            "date": sesion_data["fecha"],
            "from_hour": sesion_data["hora_desde"],
            "to_hour": sesion_data["hora_hasta"],
            "enable_reservations_limit": sesion_data["habilitar_limite_reservas"],
            "reservations_limit": sesion_data["limite_reservas"],
            "multiple_rooms": sesion_data["salas_multiples"],
            "personCode": person_code
        }
    })
    
    post_data = {
        "ctl00$ScriptManager1": "ctl00$ContentFixedSection$uAltaEventos$uAltaEventosFechas$uAlert$uplAlert|ContentFixedSection_uAltaEventos_uAltaEventosFechas_uAlert_uplAlert",
        "__EVENTTARGET": "ContentFixedSection_uAltaEventos_uAltaEventosFechas_uAlert_uplAlert",
        "__EVENTARGUMENT": event_argument,
        "__VIEWSTATE": state["__VIEWSTATE"],
        "__VIEWSTATEGENERATOR": state["__VIEWSTATEGENERATOR"],
        "ContentFixedSection_uAltaEventos_uAltaEventosFechas_availability_filter": "on",
        "__ASYNCPOST": "true",
    }
    
    if "__EVENTVALIDATION" in state:
        post_data["__EVENTVALIDATION"] = state["__EVENTVALIDATION"]
    
    print(f"\n{'='*60}")
    print(f"🎫 Seleccionando clase: {sesion_data['nom_evento']}")
    print(f"   📅 Fecha: {sesion_data['fecha']}")
    print(f"   ⏰ Hora: {sesion_data['hora_desde']} - {sesion_data['hora_hasta']}")
    print(f"   📍 Sala: {sesion_data['nom_sala']}")
    print(f"   🔑 COD_SESION: {sesion_data['cod_sesion']}")
    
    headers = {
        **HEADERS,
        "Referer": url_alta_eventos,
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
    }
    
    r = session.post(url_alta_eventos, data=post_data, headers=headers)
    r.raise_for_status()
    
    update_state_from_delta(state, r.text)
    
    print(f"✅ Respuesta recibida ({len(r.text)} bytes)")
    print(f"{'='*60}\n")
    
    return r.text


def confirmar_carrito(session: requests.Session, referer: str, state: dict):
    """
    Hace el GET a CarritoConfirmar para cargar la página de confirmación.
    
    Args:
        session: Sesión de requests (ya tiene las cookies necesarias)
        referer: URL del referer (AltaEventos)
        state: Diccionario de estado ASP.NET (se actualizará con los nuevos valores)
    
    Returns:
        Texto HTML de la respuesta
    """
    url_carrito = "https://deportesweb.madrid.es/DeportesWeb/Modulos/VentaServicios/CarritoConfirmar"
    
    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": referer,
        "Dnt": "1",
        "Sec-Gpc": "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }
    
    print(f"\n{'='*60}")
    print(f"🛒 Accediendo a CarritoConfirmar...")
    
    r = session.get(url_carrito, headers=headers)
    r.raise_for_status()
    
    # Parsear el HTML para obtener el nuevo state
    soup = BeautifulSoup(r.text, "html.parser")
    state["__VIEWSTATE"] = soup.find("input", {"id": "__VIEWSTATE"})["value"]
    state["__VIEWSTATEGENERATOR"] = soup.find("input", {"id": "__VIEWSTATEGENERATOR"})["value"]
    ev_tag = soup.find("input", {"id": "__EVENTVALIDATION"})
    if ev_tag:
        state["__EVENTVALIDATION"] = ev_tag["value"]
    
    print(f"✅ Respuesta recibida ({len(r.text)} bytes)")
    print(f"{'='*60}\n")
    
    return r.text


def finalizar_reserva(session: requests.Session, state: dict, nombre: str, apellidos: str, correo: str):
    """
    Hace el POST final para confirmar la reserva en el carrito.
    
    Args:
        session: Sesión de requests
        state: Estado ASP.NET (viewstate, etc.)
        nombre: Nombre del usuario
        apellidos: Apellidos del usuario
        correo: Correo electrónico del usuario
    
    Returns:
        Texto de la respuesta del servidor
    """
    url_carrito = "https://deportesweb.madrid.es/DeportesWeb/Modulos/VentaServicios/CarritoConfirmar"
    
    event_argument = json.dumps({
        "action": "ConfirmCart",
        "args": {}
    })
    
    post_data = {
        "ctl00$ScriptManager1": "ctl00$ContentFixedSection$uCarritoConfirmar$uAlert$uplAlert|ContentFixedSection_uCarritoConfirmar_uAlert_uplAlert",
        "__EVENTTARGET": "ContentFixedSection_uCarritoConfirmar_uAlert_uplAlert",
        "__EVENTARGUMENT": event_argument,
        "__VIEWSTATE": state["__VIEWSTATE"],
        "__VIEWSTATEGENERATOR": state["__VIEWSTATEGENERATOR"],
        "ctl00$ContentFixedSection$uCarritoConfirmar$txtNombre": nombre,
        "ctl00$ContentFixedSection$uCarritoConfirmar$txtApellidos": apellidos,
        "ctl00$ContentFixedSection$uCarritoConfirmar$txtCorreoElectronico": correo,
        "__ASYNCPOST": "true",
    }
    
    if "__EVENTVALIDATION" in state:
        post_data["__EVENTVALIDATION"] = state["__EVENTVALIDATION"]
    
    headers = {
        **HEADERS,
        "Referer": url_carrito,
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
    }
    
    print(f"\n{'='*60}")
    print(f"✅ Finalizando reserva...")
    
    
    r = session.post(url_carrito, data=post_data, headers=headers)
    r.raise_for_status()
    
    update_state_from_delta(state, r.text)
    
    print(f"✅ Respuesta recibida ({len(r.text)} bytes)")
    print(f"{'='*60}\n")
    
    return r.text


# =========================
# MAIN
# =========================

async def main():
    load_dotenv()
    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")
    mongo_url = os.getenv("MONGO_URL")
    person_code = os.getenv("PERSON_CODE")
    nombre = os.getenv("NOMBRE")
    apellidos = os.getenv("APELLIDOS")
    
    if not email or not password:
        raise ValueError("Faltan EMAIL o PASSWORD en .env")
    
    if not person_code:
        raise ValueError("Falta PERSON_CODE en .env (ej: 11968794)")
    
    if not nombre or not apellidos:
        raise ValueError("Faltan NOMBRE o APELLIDOS en .env")
    
    db_manager = None
    if mongo_url:
        db_manager = DatabaseManager(mongo_url)
    else:
        print("⚠️ MONGO_URL no configurada. No se filtrarán clases ya reservadas.")

    print("\n🎯 SISTEMA DE RESERVAS AUTOMÁTICO")
    plan = await preparar_plan_de_reservas(db_manager)
    
    if not plan:
        print("\n✅ ¡Todas las clases ya están reservadas!")
        if db_manager:
            db_manager.cerrar()
        return
    
    mostrar_plan_de_reservas(plan)
    
    # Procesar todas las clases directamente (la espera se hará en el POST de reserva)
    proximas_a_procesar = plan
    
    session = requests.Session()
    
    print("\n" + "="*60)
    print("🔐 INICIANDO SESIÓN")
    print("="*60)
    
    r = session.get(URL_LOGIN, headers=HEADERS)
    r.raise_for_status()
    state = parse_initial_state(r.text)
    
    select_menu_data = {
        "ctl00$ScriptManager1": "ctl00$ContentFixedSection$uSecciones$uAlert$uplAlert|ContentFixedSection_uSecciones_uAlert_uplAlert",
        "__EVENTTARGET": "ContentFixedSection_uSecciones_uAlert_uplAlert",
        "__EVENTARGUMENT": json.dumps({
            "action": "SelectMenu",
            "args": {
                "menu_code": "5143",
                "menu_title": "Correo y contraseña",
                "menu_type": 29,
                "authentication_provider_code": "4",
                "submenu_code": None
            }
        }),
        "__ASYNCPOST": "true",
        **state
    }
    r = session.post(URL_LOGIN, data=select_menu_data, headers=HEADERS)
    r.raise_for_status()
    update_state_from_delta(state, r.text)
    
    login_data = {
        "ctl00$ScriptManager1": "ctl00$ContentFixedSection$uLogin$uAlert$uplAlert|ContentFixedSection_uLogin_uAlert_uplAlert",
        "__EVENTTARGET": "ContentFixedSection_uLogin_uAlert_uplAlert",
        "__EVENTARGUMENT": json.dumps({"action": "Login", "args": {"authentication_provider_code": "4"}}),
        "ctl00$ContentFixedSection$uLogin$txtIdentificador": email,
        "ctl00$ContentFixedSection$uLogin$txtContrasena": password,
        "ctl00$ContentFixedSection$uLogin$chkNoCerrarSesion": "on",
        "__ASYNCPOST": "true",
        **state
    }
    r = session.post(URL_LOGIN, data=login_data, headers=HEADERS)
    r.raise_for_status()
    update_state_from_delta(state, r.text)
    
    if not is_login_success(r.text, session):
        print("❌ LOGIN FALLIDO")
        if db_manager:
            db_manager.cerrar()
        return
    
    print("✅ LOGIN CORRECTO")
    
    print("\n" + "="*60)
    print("🏢 NAVEGANDO A LA FUNDI")
    print("="*60)
    
    ajax_response = select_facility(session, facility_code="2", facility_name="La Fundi", state=state)
    
    match = re.search(r"pageRedirect\|\|/DeportesWeb/Centro\?token=([A-Z0-9]+)", urllib.parse.unquote(ajax_response))
    if not match:
        print("❌ No se pudo extraer token de instalación")
        if db_manager:
            db_manager.cerrar()
        return
    
    token = match.group(1)
    print(f"✅ Token instalación: {token}")
    
    ajax_centro_response = select_centro_menu_post(
        session, token=token,
        menu_code="8580",
        menu_title="Oferta de actividades por día y centro",
        state=state
    )
    
    match2 = re.search(r"pageRedirect\|\|/DeportesWeb/Modulos/VentaServicios/Eventos/AltaEventos\?token=([A-Z0-9]+)", urllib.parse.unquote(ajax_centro_response))
    if not match2:
        print("❌ No se pudo extraer token AltaEventos")
        if db_manager:
            db_manager.cerrar()
        return
    
    alta_token = match2.group(1)
    print(f"✅ Token AltaEventos: {alta_token}")
    
    alta_eventos_html = get_alta_eventos(
        session, token=alta_token,
        referer=f"https://deportesweb.madrid.es/DeportesWeb/Centro?token={token}"
    )
    
    soup = BeautifulSoup(alta_eventos_html, "html.parser")
    state["__VIEWSTATE"] = soup.find("input", {"id": "__VIEWSTATE"})["value"]
    state["__VIEWSTATEGENERATOR"] = soup.find("input", {"id": "__VIEWSTATEGENERATOR"})["value"]
    ev_tag = soup.find("input", {"id": "__EVENTVALIDATION"})
    if ev_tag:
        state["__EVENTVALIDATION"] = ev_tag["value"]
    
    print("✅ Página AltaEventos cargada")
    
    # Separar clases abiertas y cerradas
    clases_abiertas = [p for p in proximas_a_procesar if p["ya_abierta"]]
    clases_cerradas = [p for p in proximas_a_procesar if not p["ya_abierta"]]
    
    print("\n" + "="*60)
    print(f"📊 RESUMEN: {len(clases_abiertas)} abiertas 🟢 | {len(clases_cerradas)} cerradas 🔴")
    print("="*60)
    
    # ========================================
    # FASE 1: Procesar todas las clases ABIERTAS
    # ========================================
    if clases_abiertas:
        print("\n" + "="*60)
        print(f"🟢 FASE 1: RESERVANDO {len(clases_abiertas)} CLASE(S) ABIERTA(S)")
        print("="*60)
        
        for item in clases_abiertas:
            clase = item["clase"]
            fecha_para_post = (datetime.strptime(item["fecha_para_post"], "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")
            fecha_clase = item["fecha_clase"]
            
            print(f"\n🎯 Procesando: {clase['nombre']}")
            print(f"   Clase: {fecha_clase.strftime('%d/%m/%Y')} {clase['hora']}")
            print(f"   Estado: 🟢 Abierta")
            
            response = load_events_for_date(
                session=session,
                token=alta_token,
                fecha=fecha_para_post,
                state=state
            )
            
            fecha_clase_str = fecha_clase.strftime("%Y-%m-%d")
            sesion_data = extraer_cod_sesion(
                html_response=response,
                nombre_clase=clase["nombre"],
                hora_clase=clase["hora"],
                fecha_esperada=fecha_clase_str
            )
            
            if sesion_data:
                print(f"   🎫 COD_SESION: {sesion_data['cod_sesion']}")
                
                # Hacer POST para seleccionar/reservar la clase
                response_seleccion = seleccionar_clase(
                    session=session,
                    token=alta_token,
                    sesion_data=sesion_data,
                    person_code=person_code,
                    state=state
                )
                
                if "pageRedirect" in response_seleccion and "CarritoConfirmar" in urllib.parse.unquote(response_seleccion):
                    print(f"   ✅ ¡RESERVA AÑADIDA AL CARRITO!")
                    
                    url_alta_eventos = f"https://deportesweb.madrid.es/DeportesWeb/Modulos/VentaServicios/Eventos/AltaEventos?token={alta_token}"
                    response_carrito = confirmar_carrito(
                        session=session,
                        referer=url_alta_eventos,
                        state=state
                    )
                    
                    response_final = finalizar_reserva(
                        session=session,
                        state=state,
                        nombre=nombre,
                        apellidos=apellidos,
                        correo=email
                    )
                    
                    if "pageRedirect" in response_final and "CarritoResultado" in urllib.parse.unquote(response_final):
                        print(f"   🎉 ¡RESERVA CONFIRMADA!")
                        if db_manager:
                            await db_manager.guardar_reserva(clase, fecha_clase)
                    else:
                        print(f"   ⚠️ Error en confirmación: {response_final[:300]}")
                else:
                    print(f"   ❌ Error: {response_seleccion[:300]}")
            else:
                print(f"   ⚠️ No se encontró la sesión")
    
    # ========================================
    # FASE 2: Esperar y reservar la PRIMERA clase cerrada (objetivo)
    # ========================================
    if clases_cerradas:
        clase_objetivo = clases_cerradas[0]  # La primera cerrada (más próxima a abrir)
        clase = clase_objetivo["clase"]
        fecha_para_post = (datetime.strptime(clase_objetivo["fecha_para_post"], "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")
        fecha_clase = clase_objetivo["fecha_clase"]
        hora_apertura = clase_objetivo["hora_apertura"]
        
        print("\n" + "="*60)
        print(f"🔴 FASE 2: ESPERANDO CLASE OBJETIVO")
        print("="*60)
        print(f"\n🎯 Clase objetivo: {clase['nombre']}")
        print(f"   Clase: {fecha_clase.strftime('%d/%m/%Y')} {clase['hora']}")
        print(f"   🔓 Abre: {hora_apertura.strftime('%d/%m/%Y %H:%M')}")
        
        # Cargar eventos para obtener el COD_SESION antes de esperar
        response = load_events_for_date(
            session=session,
            token=alta_token,
            fecha=fecha_para_post,
            state=state
        )
        
        fecha_clase_str = fecha_clase.strftime("%Y-%m-%d")
        sesion_data = extraer_cod_sesion(
            html_response=response,
            nombre_clase=clase["nombre"],
            hora_clase=clase["hora"],
            fecha_esperada=fecha_clase_str
        )
        
        if sesion_data:
            print(f"   🎫 COD_SESION: {sesion_data['cod_sesion']}")
            
            # Esperar hasta que abra
            ahora = datetime.now()
            tiempo_espera = (hora_apertura - ahora).total_seconds()
            
            if tiempo_espera > 0:
                horas = int(tiempo_espera // 3600)
                minutos = int((tiempo_espera % 3600) // 60)
                segundos = int(tiempo_espera % 60)
                print(f"\n   ⏳ Esperando {horas}h {minutos}m {segundos}s hasta que abra...")
                print(f"   🕐 Hora de apertura: {hora_apertura.strftime('%d/%m/%Y %H:%M:%S')}")
                time.sleep(tiempo_espera)
                print(f"   🔔 ¡Reserva abierta! Procediendo...")
            
            # Hacer POST para seleccionar/reservar la clase
            response_seleccion = seleccionar_clase(
                session=session,
                token=alta_token,
                sesion_data=sesion_data,
                person_code=person_code,
                state=state
            )
            
            if "pageRedirect" in response_seleccion and "CarritoConfirmar" in urllib.parse.unquote(response_seleccion):
                print(f"   ✅ ¡RESERVA AÑADIDA AL CARRITO!")
                
                url_alta_eventos = f"https://deportesweb.madrid.es/DeportesWeb/Modulos/VentaServicios/Eventos/AltaEventos?token={alta_token}"
                response_carrito = confirmar_carrito(
                    session=session,
                    referer=url_alta_eventos,
                    state=state
                )
                
                response_final = finalizar_reserva(
                    session=session,
                    state=state,
                    nombre=nombre,
                    apellidos=apellidos,
                    correo=email
                )
                
                if "pageRedirect" in response_final and "CarritoResultado" in urllib.parse.unquote(response_final):
                    print(f"   🎉 ¡CLASE OBJETIVO RESERVADA EXITOSAMENTE!")
                    if db_manager:
                        await db_manager.guardar_reserva(clase, fecha_clase)
                else:
                    print(f"   ⚠️ Error en confirmación: {response_final[:300]}")
            else:
                print(f"   ❌ Error: {response_seleccion[:300]}")
        else:
            print(f"   ⚠️ No se encontró la sesión para la clase objetivo")
    
    print("\n" + "="*60)
    print("✅ PROCESO COMPLETADO")
    print("="*60)
    
    if db_manager:
        db_manager.cerrar()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️ Interrumpido\n")
    except Exception as e:
        print(f"\n❌ Error: {e}\n")
        import traceback
        traceback.print_exc()