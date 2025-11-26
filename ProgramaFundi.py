import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from dotenv import load_dotenv
import os
import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from typing import List, Dict, Optional, Tuple
import random

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

load_dotenv()

MONGO_URL = os.getenv("MONGO_URL")
EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")

if not all([MONGO_URL, EMAIL, PASSWORD]):
    raise ValueError("Faltan variables de entorno: MONGO_URL, EMAIL o PASSWORD")

# Constantes optimizadas
HORAS_ANTES_APERTURA = 49
TIMEOUT_SELECTOR = 10000
TIMEOUT_NAVEGACION = 30000
MAX_INTENTOS_RESERVA = 120
MAX_CLASES_POR_DIA = 3
REINTENTO_DELAY = 0.3  # Delay más rápido para reintentos

# ============================================================================
# DEFINICIÓN DE CLASES
# ============================================================================

CLASES = [
    {"dia": "lunes", "hora": "15:45", "nombre": "Fitness"},
    {"dia": "lunes", "hora": "17:00", "nombre": "Pilates MesD"},
    {"dia": "lunes", "hora": "18:00", "nombre": "Entrenamiento en suspensión"},
    {"dia": "martes", "hora": "15:45", "nombre": "Fuerza en sala multitrabajo"},
    {"dia": "martes", "hora": "18:00", "nombre": "Pilates MesD"},
    {"dia": "miércoles", "hora": "15:45", "nombre": "Fuerza GAP"},
    {"dia": "miércoles", "hora": "17:00", "nombre": "Pilates MesD"},
    {"dia": "miércoles", "hora": "18:00", "nombre": "Entrenamiento en suspensión"},
    {"dia": "jueves", "hora": "15:45", "nombre": "Fuerza en sala multitrabajo"},
    {"dia": "viernes", "hora": "15:45", "nombre": "Pilates MesD"},
    {"dia": "viernes", "hora": "17:00", "nombre": "Entrenamiento Funcional"}
]

DIAS_SEMANA = {
    "lunes": 0, "martes": 1, "miércoles": 2, "jueves": 3,
    "viernes": 4, "sábado": 5, "domingo": 6
}

# ============================================================================
# UTILIDADES ANTI-DETECCIÓN
# ============================================================================

async def espera_humana(minimo=0.3, maximo=0.8):
    """Espera aleatoria para simular comportamiento humano"""
    await asyncio.sleep(random.uniform(minimo, maximo))

async def click_humano(page, selector):
    """Realiza un click más humano con movimiento de ratón"""
    try:
        element = page.locator(selector).first
        box = await element.bounding_box()
        if box:
            # Posición aleatoria dentro del elemento
            x = box['x'] + random.uniform(box['width'] * 0.3, box['width'] * 0.7)
            y = box['y'] + random.uniform(box['height'] * 0.3, box['height'] * 0.7)
            await page.mouse.move(x, y, steps=random.randint(5, 15))
            await espera_humana(0.05, 0.15)
        await element.click()
    except:
        # Fallback a click normal
        await page.click(selector)

def crear_user_agent():
    """Genera un User-Agent realista"""
    versions = ["120.0.0.0", "119.0.0.0", "118.0.0.0"]
    return f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.choice(versions)} Safari/537.36"

# ============================================================================
# GESTIÓN DE BASE DE DATOS
# ============================================================================

class DatabaseManager:
    def __init__(self):
        self.client = AsyncIOMotorClient(MONGO_URL)
        self.db = self.client["reservas_clases"]
        self.coleccion = self.db["clases_reservadas"]
        print("✅ Conectado a MongoDB")
    
    async def cargar_reservadas_recientes(self, dias_atras: int = 7) -> List[Dict]:
        fecha_inicio = (datetime.datetime.now() - datetime.timedelta(days=dias_atras)).strftime("%Y-%m-%d")
        cursor = self.coleccion.find({"fecha": {"$gte": fecha_inicio}})
        return await cursor.to_list(length=None)
    
    async def guardar_reserva(self, clase: Dict, fecha: datetime.datetime) -> bool:
        documento = {
            "nombre": clase["nombre"],
            "hora": clase["hora"],
            "dia": clase["dia"],
            "fecha": fecha.strftime("%Y-%m-%d"),
            "timestamp": datetime.datetime.now()
        }
        
        existe = await self.coleccion.find_one({
            "nombre": documento["nombre"],
            "hora": documento["hora"],
            "fecha": documento["fecha"]
        })
        
        if not existe:
            await self.coleccion.insert_one(documento)
            print(f"💾 Guardada: {documento['nombre']} - {documento['fecha']} {documento['hora']}")
            return True
        return False
    
    def cerrar(self):
        self.client.close()

# ============================================================================
# UTILIDADES DE FECHA
# ============================================================================

def calcular_proxima_fecha(dia_semana: str, hora: str) -> datetime.datetime:
    ahora = datetime.datetime.now()
    hora_int, minuto_int = map(int, hora.split(":"))
    objetivo = ahora.replace(hour=hora_int, minute=minuto_int, second=0, microsecond=0)
    dias_a_sumar = (DIAS_SEMANA[dia_semana] - ahora.weekday() + 7) % 7
    
    if dias_a_sumar == 0:
        return objetivo if ahora < objetivo else objetivo + datetime.timedelta(days=7)
    return objetivo + datetime.timedelta(days=dias_a_sumar)

def calcular_hora_apertura(fecha_clase: datetime.datetime) -> datetime.datetime:
    return fecha_clase - datetime.timedelta(hours=HORAS_ANTES_APERTURA)

# ============================================================================
# NAVEGADOR WEB OPTIMIZADO
# ============================================================================

class WebNavigator:
    def __init__(self, page):
        self.page = page
    
    async def hacer_login(self) -> bool:
        try:
            print("🌐 Navegando a login...")
            await self.page.goto("https://deportesweb.madrid.es/DeportesWeb/login", timeout=TIMEOUT_NAVEGACION)
            await espera_humana(0.5, 1.0)
            
            # Intentar cerrar modal inicial (no crítico)
            try:
                await self.page.click("div.navigation-section-widget-collection-item-image-icon-square", timeout=2000)
            except:
                pass
            
            # Login con comportamiento humano
            await self.page.fill("input[name='ctl00$ContentFixedSection$uLogin$txtIdentificador']", EMAIL)
            await espera_humana(0.2, 0.4)
            await self.page.fill("input[name='ctl00$ContentFixedSection$uLogin$txtContrasena']", PASSWORD)
            await espera_humana(0.3, 0.6)
            await click_humano(self.page, "button#ContentFixedSection_uLogin_btnLogin")
            
            await self.page.wait_for_selector("div#ctl00_divProfile", timeout=TIMEOUT_NAVEGACION)
            print("✅ Login exitoso")
            return True
        except Exception as e:
            print(f"❌ Error en login: {e}")
            return False
    
    async def volver_a_home(self) -> bool:
        try:
            print("🏠 Volviendo a Home...")
            await self.page.goto("https://deportesweb.madrid.es/DeportesWeb/Home", timeout=TIMEOUT_NAVEGACION)
            await espera_humana(1.0, 1.5)
            return True
        except Exception as e:
            print(f"❌ Error volviendo a Home: {e}")
            return False
    
    async def navegar_a_actividades(self) -> bool:
        try:
            print("🔄 Navegando a actividades...")
            
            selector_fundi = "article.navigation-section-widget-collection-item h4[title='La Fundi']"
            await self.page.wait_for_selector(selector_fundi, timeout=TIMEOUT_SELECTOR)
            await click_humano(self.page, selector_fundi)
            await espera_humana(0.5, 1.0)
            
            selector_actividades = "article.navigation-section-widget-collection-item h4[title='Oferta de actividades por día y centro']"
            await self.page.wait_for_selector(selector_actividades, timeout=TIMEOUT_SELECTOR)
            await click_humano(self.page, selector_actividades)
            await espera_humana(1.0, 1.5)
            
            print("  ✓ En 'Oferta de actividades'")
            return True
        except Exception as e:
            print(f"❌ Error navegando: {e}")
            return False
    
    async def seleccionar_dia(self, fecha: datetime.datetime) -> bool:
        try:
            fecha_str = fecha.strftime("%d/%m/%Y")
            selector_dia = f"td.day[data-day='{fecha_str}']"
            
            await self.page.wait_for_selector(selector_dia, state="attached", timeout=TIMEOUT_SELECTOR)
            await click_humano(self.page, selector_dia)
            await self.page.wait_for_selector("div.panel-body", timeout=TIMEOUT_SELECTOR)
            await espera_humana(0.5, 0.8)
            
            print(f"  ✓ Día {fecha_str} seleccionado")
            return True
        except Exception as e:
            print(f"❌ Error seleccionando día: {e}")
            return False
    
    async def buscar_clase_en_paneles(self, nombre_clase: str, hora: str) -> Optional[Dict]:
        try:
            panels = self.page.locator("div.panel-body")
            count_panels = await panels.count()
            
            for i in range(count_panels):
                panel = panels.nth(i)
                
                try:
                    nombre_panel = await panel.locator("h4.media-heading").first.inner_text()
                    if nombre_panel.strip().lower() != nombre_clase.lower():
                        continue
                except:
                    continue
                
                slots = panel.locator(f"li.media:has-text('{hora}')")
                if await slots.count() == 0:
                    continue
                
                slot = slots.first
                
                try:
                    plazas_texto = await slot.locator("span").first.inner_text()
                    plazas_disponibles = plazas_texto.split("/")[0].strip() if "/" in plazas_texto else plazas_texto.strip()
                except:
                    plazas_disponibles = "?"
                
                return {
                    "encontrada": True,
                    "slot": slot,
                    "plazas": plazas_disponibles,
                    "panel_index": i
                }
            
            return None
        except Exception as e:
            print(f"❌ Error buscando clase: {e}")
            return None
    
    async def intentar_reservar_slot(self, slot) -> str:
        """Retorna: RESERVADA, YA_TIENE, NO_ABIERTA, ERROR"""
        try:
            await slot.click()
            await espera_humana(0.5, 1.0)
            
            boton_confirmar = "button#ContentFixedSection_uCarritoConfirmar_btnConfirmCart"
            
            try:
                await self.page.wait_for_selector(boton_confirmar, timeout=3000)
                await click_humano(self.page, boton_confirmar)
                await espera_humana(1.0, 1.5)
                
                # Esperar a que aparezca algún mensaje (éxito o error)
                await asyncio.sleep(1.0)
                
                # Verificar si hay mensaje de error específico
                try:
                    mensaje_error_selector = "div#ContentFixedSection_uAltaEventos_uAltaEventosFechas_uAlert_divAlertDanger"
                    await self.page.wait_for_selector(mensaje_error_selector, state="visible", timeout=2000)
                    
                    # Leer el mensaje exacto
                    mensaje_span = "span#ContentFixedSection_uAltaEventos_uAltaEventosFechas_uAlert_spnAlertDanger"
                    mensaje_texto = await self.page.locator(mensaje_span).inner_text()
                    
                    print(f"   ⚠️ Mensaje del sistema: {mensaje_texto}")
                    
                    # Detectar si ya tienes la reserva
                    mensajes_ya_tiene = [
                        "La sesión seleccionada no permite más de 1 reserva(s) por persona.",
                        "solo puedes tener",
                        "ya tienes",
                        "sólo puedes tener"
                    ]
                    
                    if any(msg in mensaje_texto.lower() for msg in mensajes_ya_tiene):
                        return "YA_TIENE"
                    
                    # Detectar si aún no está abierta
                    mensajes_no_abierta = [
                        "La sesión seleccionada estará disponible el",
                        "se abrirá",
                        "no está abierta",
                        "estará disponible"
                    ]
                    
                    if any(msg in mensaje_texto.lower() for msg in mensajes_no_abierta):
                        return "NO_ABIERTA"
                    
                    # Otro error
                    return "ERROR"
                    
                except PlaywrightTimeoutError:
                    # No hay mensaje de error = ÉXITO
                    print("   ✅ Sin mensaje de error - reserva exitosa")
                    return "RESERVADA"
                    
            except PlaywrightTimeoutError:
                # No apareció el botón confirmar - verificar si hay mensaje
                try:
                    mensaje_error_selector = "div#ContentFixedSection_uAltaEventos_uAltaEventosFechas_uAlert_divAlertDanger"
                    await self.page.wait_for_selector(mensaje_error_selector, state="visible", timeout=1000)
                    
                    mensaje_span = "span#ContentFixedSection_uAltaEventos_uAltaEventosFechas_uAlert_spnAlertDanger"
                    mensaje_texto = await self.page.locator(mensaje_span).inner_text()
                    
                    print(f"   ⚠️ Mensaje: {mensaje_texto}")
                    
                    if "no permite más de 1 reserva" in mensaje_texto.lower() or "ya tienes" in mensaje_texto.lower():
                        return "YA_TIENE"
                    
                    if "se abre a las" in mensaje_texto.lower() or "se abrirá" in mensaje_texto.lower():
                        return "NO_ABIERTA"
                    
                    return "ERROR"
                except:
                    return "ERROR"
            
        except Exception as e:
            print(f"⚠️ Error en reserva: {e}")
            return "ERROR"

# ============================================================================
# GESTOR DE RESERVAS OPTIMIZADO
# ============================================================================

class ReservasManager:
    def __init__(self, db_manager: DatabaseManager, navigator: WebNavigator):
        self.db = db_manager
        self.nav = navigator
        self.pagina_preparada = False
        self.dia_seleccionado = None
    
    async def preparar_clases_candidatas(self) -> List[Tuple[datetime.datetime, Dict, datetime.datetime]]:
        reservadas = await self.db.cargar_reservadas_recientes()
        ahora = datetime.datetime.now()
        candidatas = []
        
        for clase in CLASES:
            fecha_clase = calcular_proxima_fecha(clase["dia"], clase["hora"])
            hora_apertura = calcular_hora_apertura(fecha_clase)
            
            # Filtrar clases pasadas o ya reservadas
            if ahora > fecha_clase:
                continue
            
            ya_reservada = any(
                r["nombre"] == clase["nombre"] and
                r["hora"] == clase["hora"] and
                r["fecha"] == fecha_clase.strftime("%Y-%m-%d")
                for r in reservadas
            )
            
            if ya_reservada:
                print(f"⏭️ Saltando {clase['nombre']} {clase['hora']} (ya reservada)")
                continue
            
            candidatas.append((hora_apertura, clase, fecha_clase))
        
        candidatas.sort(key=lambda x: x[0])
        return candidatas
    
    async def reservar_clase(self, clase: Dict, fecha_clase: datetime.datetime) -> bool:
        print(f"\n{'='*60}")
        print(f"🎯 Reservando: {clase['nombre']} - {fecha_clase.strftime('%d/%m/%Y')} {clase['hora']}")
        print(f"{'='*60}\n")
        
        deadline = datetime.datetime.now() + datetime.timedelta(seconds=MAX_INTENTOS_RESERVA)
        intento = 0
        
        while datetime.datetime.now() < deadline:
            intento += 1
            
            info_clase = await self.nav.buscar_clase_en_paneles(clase["nombre"], clase["hora"])
            
            if not info_clase:
                print(f"⏳ Intento {intento}: Clase no encontrada, refrescando...")
                await asyncio.sleep(REINTENTO_DELAY)
                await self.nav.seleccionar_dia(fecha_clase)
                continue
            
            if info_clase["plazas"] == "0":
                print(f"🚫 Sin plazas - DESCARTANDO")
                return False
            
            print(f"🔍 Intento {intento}: Encontrada - Plazas: {info_clase['plazas']}")
            
            resultado = await self.nav.intentar_reservar_slot(info_clase["slot"])
            
            if resultado == "RESERVADA":
                await self.db.guardar_reserva(clase, fecha_clase)
                print(f"\n{'🎉'*20}")
                print(f"✅ ¡RESERVA EXITOSA!")
                print(f"   📌 {clase['nombre']} - {fecha_clase.strftime('%d/%m/%Y')} {clase['hora']}")
                print(f"{'🎉'*20}\n")
                return True
            
            elif resultado == "YA_TIENE":
                await self.db.guardar_reserva(clase, fecha_clase)
                print(f"ℹ️ Ya reservada (detectado por sistema)")
                return False
            
            elif resultado == "NO_ABIERTA":
                print(f"⏳ Intento {intento}: Aún cerrada...")
                await asyncio.sleep(REINTENTO_DELAY)
            
            else:
                print(f"⚠️ Error en intento {intento}")
                await asyncio.sleep(REINTENTO_DELAY * 2)
        
        print(f"❌ Tiempo agotado - DESCARTANDO")
        return False
    
    async def ejecutar_reservas(self):
        candidatas = await self.preparar_clases_candidatas()
        
        if not candidatas:
            print("⏹️ No hay clases candidatas")
            return
        
        ahora = datetime.datetime.now()
        
        # Identificar clase objetivo
        clase_objetivo_info = next(
            ((h, c, f) for h, c, f in candidatas if h >= ahora),
            candidatas[0] if candidatas else None
        )
        
        if clase_objetivo_info:
            _, clase_obj, fecha_obj = clase_objetivo_info
            print(f"\n🎯 OBJETIVO: {clase_obj['nombre']} - {fecha_obj.strftime('%d/%m/%Y')} {clase_obj['hora']}\n")
        
        # Mostrar resumen
        print(f"📊 Clases candidatas:")
        print(f"{'='*60}")
        for i, (hora_apertura, clase, fecha_clase) in enumerate(candidatas, 1):
            estado = "🟢" if hora_apertura <= ahora else "🔴"
            es_objetivo = (clase_objetivo_info and clase == clase_objetivo_info[1] and fecha_clase == clase_objetivo_info[2])
            marca = " ⭐" if es_objetivo else ""
            print(f"{i}. {estado} {clase['nombre']} - {fecha_clase.strftime('%d/%m')} {clase['hora']}{marca}")
        print(f"{'='*60}\n")
        
        clases_reservadas = 0
        dia_actual = None
        clases_reservadas_hoy = 0
        
        for hora_apertura, clase, fecha_clase in candidatas:
            es_objetivo = (clase_objetivo_info and 
                          clase == clase_objetivo_info[1] and 
                          fecha_clase == clase_objetivo_info[2])
            
            fecha_str = fecha_clase.strftime("%Y-%m-%d")
            if dia_actual != fecha_str:
                dia_actual = fecha_str
                clases_reservadas_hoy = 0
                print(f"\n📅 Día: {fecha_clase.strftime('%d/%m/%Y')}")
            
            if clases_reservadas_hoy >= MAX_CLASES_POR_DIA and not es_objetivo:
                print(f"⏭️ Límite alcanzado ({MAX_CLASES_POR_DIA} clases)")
                continue
            
            ahora = datetime.datetime.now()
            espera = (hora_apertura - ahora).total_seconds()
            
            # Preparar página
            if not self.pagina_preparada:
                if not await self.nav.navegar_a_actividades():
                    continue
                self.pagina_preparada = True
            
            if self.dia_seleccionado != fecha_str:
                if not await self.nav.seleccionar_dia(fecha_clase):
                    continue
                self.dia_seleccionado = fecha_str
            
            # Esperar apertura
            if espera > 0:
                horas, resto = divmod(int(espera), 3600)
                minutos, segundos = divmod(resto, 60)
                print(f"\n⏰ Esperando {horas:02d}:{minutos:02d}:{segundos:02d}{'⭐' if es_objetivo else ''}")
                await asyncio.sleep(espera)
            
            # Intentar reservar
            exito = await self.reservar_clase(clase, fecha_clase)
            
            if exito:
                clases_reservadas += 1
                clases_reservadas_hoy += 1
                
                if es_objetivo:
                    print(f"\n🎉 ¡OBJETIVO CONSEGUIDO! Total: {clases_reservadas}\n")
                    return
                
                # Resetear después de reserva
                await self.nav.volver_a_home()
                self.pagina_preparada = False
                self.dia_seleccionado = None
            else:
                if es_objetivo:
                    print(f"\n❌ OBJETIVO NO CONSEGUIDO\n")
                    return
        
        print(f"\n{'🎉' if clases_reservadas > 0 else '❌'} Finalizado - Reservadas: {clases_reservadas}\n")

# ============================================================================
# MAIN
# ============================================================================

async def main():
    print("\n" + "="*60)
    print("🏋️  SISTEMA DE RESERVAS")
    print("="*60 + "\n")
    
    db_manager = DatabaseManager()
    
    async with async_playwright() as p:
        # Configuración anti-detección mejorada
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-web-security"
            ]
        )
        
        try:
            # Context con perfil realista
            context = await browser.new_context(
                user_agent=crear_user_agent(),
                viewport={"width": 1920, "height": 1080},
                locale="es-ES",
                timezone_id="Europe/Madrid",
                permissions=["geolocation"],
                geolocation={"latitude": 40.4168, "longitude": -3.7038}  # Madrid
            )
            
            # Ocultar webdriver
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = {runtime: {}};
            """)
            
            page = await context.new_page()
            
            navigator = WebNavigator(page)
            reservas_manager = ReservasManager(db_manager, navigator)
            
            if not await navigator.hacer_login():
                print("❌ Login fallido")
                return
            
            await reservas_manager.ejecutar_reservas()
            
        finally:
            await browser.close()
            db_manager.cerrar()
            print("\n✅ Programa finalizado\n")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️ Interrumpido\n")
    except Exception as e:
        print(f"\n❌ Error fatal: {e}\n")