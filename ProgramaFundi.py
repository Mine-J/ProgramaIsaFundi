import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from dotenv import load_dotenv
import os
import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from typing import List, Dict, Optional, Tuple

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

load_dotenv()

# MongoDB
MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise ValueError("No se encontró MONGO_URL en el .env")

# Credenciales
EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")

if not EMAIL or not PASSWORD:
    raise ValueError("Faltan EMAIL o PASSWORD en el .env")

# Constantes
HORAS_ANTES_APERTURA = 49  # Las clases se abren 49 horas antes
TIMEOUT_SELECTOR = 10000  # 10 segundos
TIMEOUT_NAVEGACION = 30000  # 30 segundos
MAX_INTENTOS_RESERVA = 120  # 120 segundos = 2 minutos de reintentos
MAX_CLASES_POR_DIA = 3  # Máximo de clases que se pueden reservar en 1 día

# ============================================================================
# DEFINICIÓN DE CLASES
# ============================================================================

CLASES = [
    {"dia": "lunes", "hora": "15:45", "nombre": "Fitness"},
    {"dia": "lunes", "hora": "17:00", "nombre": "Pilates MesD"},
    {"dia": "lunes", "hora": "18:00", "nombre": "Entrenamiento en suspensión"},
    {"dia": "martes", "hora": "15:45", "nombre": "Fuerza en sala multitrabajo"},
    {"dia": "martes", "hora": "18:00", "nombre": "Pilates MesD"},
    {"dia": "miércoles", "hora": "18:00", "nombre": "Entrenamiento en suspensión"},
    {"dia": "miércoles", "hora": "19:00", "nombre": "Fitness"},
    {"dia": "jueves", "hora": "15:45", "nombre": "Fuerza en sala multitrabajo"},
    {"dia": "viernes", "hora": "15:45", "nombre": "Pilates MesD"},
    {"dia": "viernes", "hora": "17:00", "nombre": "Entrenamiento Funcional"}
]

DIAS_SEMANA = {
    "lunes": 0,
    "martes": 1,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sábado": 5,
    "domingo": 6,
}

# ============================================================================
# GESTIÓN DE BASE DE DATOS
# ============================================================================

class DatabaseManager:
    def __init__(self):
        self.client = AsyncIOMotorClient(MONGO_URL)
        self.db = self.client["reservas_clases"]
        self.coleccion = self.db["clases_reservadas"]
        print("✅ Conectado a MongoDB Atlas")
    
    async def cargar_reservadas_recientes(self, dias_atras: int = 7) -> List[Dict]:
        """Carga clases reservadas en los últimos N días"""
        fecha_inicio = (datetime.datetime.now() - datetime.timedelta(days=dias_atras)).strftime("%Y-%m-%d")
        cursor = self.coleccion.find({"fecha": {"$gte": fecha_inicio}})
        return await cursor.to_list(length=None)
    
    async def guardar_reserva(self, clase: Dict, fecha: datetime.datetime) -> bool:
        """Guarda una reserva en la BD si no existe"""
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
            print(f"💾 Guardada en BD: {documento['nombre']} - {documento['fecha']} {documento['hora']}")
            return True
        else:
            print(f"ℹ️ Ya existe en BD: {documento['nombre']} - {documento['fecha']} {documento['hora']}")
            return False
    
    def cerrar(self):
        self.client.close()

# ============================================================================
# UTILIDADES DE FECHA
# ============================================================================

def calcular_proxima_fecha(dia_semana: str, hora: str) -> datetime.datetime:
    """Calcula la próxima fecha para un día de la semana y hora dados"""
    ahora = datetime.datetime.now()
    hora_int, minuto_int = map(int, hora.split(":"))
    
    # Crear datetime objetivo para hoy
    objetivo = ahora.replace(hour=hora_int, minute=minuto_int, second=0, microsecond=0)
    
    # Calcular días hasta el día de la semana objetivo
    dias_a_sumar = (DIAS_SEMANA[dia_semana] - ahora.weekday() + 7) % 7
    
    # Si es hoy y aún no ha pasado la hora
    if dias_a_sumar == 0 and ahora < objetivo:
        return objetivo
    # Si es hoy pero ya pasó la hora, o es otro día
    elif dias_a_sumar == 0:
        return objetivo + datetime.timedelta(days=7)
    else:
        return objetivo + datetime.timedelta(days=dias_a_sumar)

def calcular_hora_apertura(fecha_clase: datetime.datetime) -> datetime.datetime:
    """Calcula cuándo se abre la reserva para una clase"""
    return fecha_clase - datetime.timedelta(hours=HORAS_ANTES_APERTURA)

# ============================================================================
# NAVEGADOR WEB
# ============================================================================

class WebNavigator:
    def __init__(self, page):
        self.page = page
    
    async def hacer_login(self) -> bool:
        """Realiza el login en la plataforma"""
        try:
            print("🌐 Navegando a página de login...")
            await self.page.goto("https://deportesweb.madrid.es/DeportesWeb/login", timeout=TIMEOUT_NAVEGACION)
            try:
                await self.page.click("div.navigation-section-widget-collection-item-image-icon-square")
                print("✅ Click realizado en el div de la clase.")
            except Exception:
                print("ℹ️ No se pudo hacer click en el div inicial (no crítico).")            
            # Llenar formulario
            await self.page.fill("input[name='ctl00$ContentFixedSection$uLogin$txtIdentificador']", EMAIL)
            await self.page.fill("input[name='ctl00$ContentFixedSection$uLogin$txtContrasena']", PASSWORD)
            await self.page.click("button#ContentFixedSection_uLogin_btnLogin")
            
            print("⌛ Esperando confirmación de login...")
            await self.page.wait_for_selector("div#ctl00_divProfile", timeout=TIMEOUT_NAVEGACION)
            
            print("✅ Login completado correctamente")
            return True
        except Exception as e:
            print(f"❌ Error en login: {e}")
            return False
    
    async def volver_a_home(self) -> bool:
        """Navega de vuelta a la página Home"""
        try:
            print("🏠 Volviendo a Home...")
            await self.page.goto("https://deportesweb.madrid.es/DeportesWeb/Home", timeout=TIMEOUT_NAVEGACION)
            await asyncio.sleep(2)
            print("  ✓ De vuelta en Home")
            return True
        except Exception as e:
            print(f"❌ Error volviendo a Home: {e}")
            return False
    
    async def navegar_a_actividades(self) -> bool:
        """Navega a la sección de oferta de actividades"""
        try:
            print("🔄 Navegando a 'La Fundi' > 'Oferta de actividades'...")
            
            # Click en "La Fundi"
            selector_fundi = "article.navigation-section-widget-collection-item h4[title='La Fundi']"
            await self.page.wait_for_selector(selector_fundi, timeout=TIMEOUT_SELECTOR)
            await self.page.click(selector_fundi)
            await asyncio.sleep(1)
            print("  ✓ Click en 'La Fundi'")
            
            # Click en "Oferta de actividades por día y centro"
            selector_actividades = "article.navigation-section-widget-collection-item h4[title='Oferta de actividades por día y centro']"
            await self.page.wait_for_selector(selector_actividades, timeout=TIMEOUT_SELECTOR)
            await self.page.click(selector_actividades)
            await asyncio.sleep(2)
            print("  ✓ Click en 'Oferta de actividades'")
            
            return True
        except Exception as e:
            print(f"❌ Error navegando a actividades: {e}")
            return False
    
    async def seleccionar_dia(self, fecha: datetime.datetime) -> bool:
        """Selecciona un día en el calendario"""
        try:
            fecha_str = fecha.strftime("%d/%m/%Y")
            selector_dia = f"td.day[data-day='{fecha_str}']"
            
            print(f"📅 Seleccionando día: {fecha_str}")
            
            # Esperar a que el día esté visible
            await self.page.wait_for_selector(selector_dia, state="attached", timeout=TIMEOUT_SELECTOR)
            
            # Click en el día
            await self.page.click(selector_dia)
            
            # Esperar a que carguen los paneles
            await self.page.wait_for_selector("div.panel-body", timeout=TIMEOUT_SELECTOR)
            await asyncio.sleep(1)
            
            print(f"  ✓ Día {fecha_str} seleccionado")
            return True
        except Exception as e:
            print(f"❌ Error seleccionando día {fecha.strftime('%d/%m/%Y')}: {e}")
            return False
    
    async def buscar_clase_en_paneles(self, nombre_clase: str, hora: str) -> Optional[Dict]:
        """
        Busca una clase específica en los paneles.
        Retorna dict con info si la encuentra, None si no.
        """
        try:
            panels = self.page.locator("div.panel-body")
            count_panels = await panels.count()
            
            for i in range(count_panels):
                panel = panels.nth(i)
                
                # Obtener nombre del panel
                try:
                    nombre_panel = await panel.locator("h4.media-heading").first.inner_text()
                    nombre_panel = nombre_panel.strip()
                except:
                    continue
                
                # Verificar si es el panel correcto
                if nombre_panel.lower() != nombre_clase.lower():
                    continue
                
                # Buscar slots con la hora específica
                slots = panel.locator(f"li.media:has-text('{hora}')")
                count_slots = await slots.count()
                
                if count_slots == 0:
                    continue
                
                # Examinar el primer slot encontrado
                slot = slots.first
                
                # Leer plazas disponibles (formato: "15/20" o "0/20")
                try:
                    span_plazas = slot.locator("span").first
                    plazas_texto = await span_plazas.inner_text()
                    plazas_texto = plazas_texto.strip()
                    
                    # Extraer el número antes del "/"
                    if "/" in plazas_texto:
                        plazas_disponibles = plazas_texto.split("/")[0].strip()
                    else:
                        plazas_disponibles = plazas_texto
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
        """
        Intenta reservar un slot específico.
        Retorna: "RESERVADA", "YA_TIENE", "NO_ABIERTA", "ERROR"
        """
        try:
            # Click en el slot
            await slot.click()
            await asyncio.sleep(1)
            
            # Buscar botón de confirmar
            boton_confirmar = "button#ContentFixedSection_uCarritoConfirmar_btnConfirmCart"
            
            try:
                await self.page.wait_for_selector(boton_confirmar, timeout=3000)
                await self.page.click(boton_confirmar)
                await asyncio.sleep(2)
                
                # Verificar resultado
                content = await self.page.content()
                content_lower = content.lower()
                
                # Detectar mensajes de "ya tienes reserva"
                mensajes_ya_tiene = [
                    "solo puedes tener", 
                    "ya tienes", 
                    "sólo puedes tener",
                    "la sesión seleccionada no permite más de 1 reserva(s) por persona"
                ]
                
                if any(msg in content_lower for msg in mensajes_ya_tiene):
                    return "YA_TIENE"
                
                # Si no hay error, asumimos éxito
                return "RESERVADA"
                
            except PlaywrightTimeoutError:
                # No apareció botón confirmar - verificar mensajes
                content = await self.page.content()
                content_lower = content.lower()
                
                if any(msg in content_lower for msg in ["se abre a las", "se abrirá", "no está abierta"]):
                    return "NO_ABIERTA"
                
                # Detectar mensajes de "ya tienes reserva" también aquí
                mensajes_ya_tiene = [
                    "solo puedes tener", 
                    "ya tienes", 
                    "sólo puedes tener",
                    "la sesión seleccionada no permite más de 1 reserva(s) por persona"
                ]
                
                if any(msg in content_lower for msg in mensajes_ya_tiene):
                    return "YA_TIENE"
                
                return "ERROR"
                
        except Exception as e:
            print(f"⚠️ Error en intentar_reservar_slot: {e}")
            return "ERROR"

# ============================================================================
# GESTOR DE RESERVAS
# ============================================================================

class ReservasManager:
    def __init__(self, db_manager: DatabaseManager, navigator: WebNavigator):
        self.db = db_manager
        self.nav = navigator
        self.pagina_preparada = False  # Control si ya navegamos a actividades
        self.dia_seleccionado = None   # Control del día actual seleccionado
    
    async def preparar_clases_candidatas(self) -> List[Tuple[datetime.datetime, Dict, datetime.datetime]]:
        """
        Prepara lista de clases candidatas para reservar.
        Retorna: [(hora_apertura, clase_dict, fecha_clase), ...]
        Ordenadas por hora de apertura (la más próxima primero)
        """
        reservadas = await self.db.cargar_reservadas_recientes()
        ahora = datetime.datetime.now()
        candidatas = []
        
        for clase in CLASES:
            fecha_clase = calcular_proxima_fecha(clase["dia"], clase["hora"])
            hora_apertura = calcular_hora_apertura(fecha_clase)
            
            # Verificar si ya está reservada
            ya_reservada = any(
                r["nombre"] == clase["nombre"] and
                r["hora"] == clase["hora"] and
                r["fecha"] == fecha_clase.strftime("%Y-%m-%d")
                for r in reservadas
            )
            
            # Verificar si ya pasó la clase
            if ahora > fecha_clase:
                continue
            
            if ya_reservada:
                print(f"⏭️ Saltando {clase['nombre']} {clase['hora']} (ya reservada)")
                continue
            
            candidatas.append((hora_apertura, clase, fecha_clase))
        
        # Ordenar por hora de apertura (la más próxima primero)
        candidatas.sort(key=lambda x: x[0])
        
        return candidatas
    
    async def reservar_clase(self, clase: Dict, fecha_clase: datetime.datetime) -> bool:
        """
        Intenta reservar una clase específica.
        Reintenta durante MAX_INTENTOS_RESERVA segundos si no está abierta.
        """
        print(f"\n{'='*60}")
        print(f"🎯 Intentando reservar: {clase['nombre']}")
        print(f"   📅 Fecha: {fecha_clase.strftime('%d/%m/%Y')}")
        print(f"   🕐 Hora: {clase['hora']}")
        print(f"{'='*60}\n")
        
        # Intentar reservar con reintentos
        deadline = datetime.datetime.now() + datetime.timedelta(seconds=MAX_INTENTOS_RESERVA)
        intento = 0
        
        while datetime.datetime.now() < deadline:
            intento += 1
            
            # Buscar la clase
            info_clase = await self.nav.buscar_clase_en_paneles(clase["nombre"], clase["hora"])
            
            if not info_clase:
                print(f"⏳ Intento {intento}: Clase no encontrada aún, refrescando...")
                await asyncio.sleep(1)
                # Refrescar día
                await self.nav.seleccionar_dia(fecha_clase)
                continue
            
            # Verificar plazas (si es "0" saltamos esta clase)
            if info_clase["plazas"] == "0":
                print(f"🚫 Sin plazas disponibles (0 plazas) - DESCARTANDO clase")
                return False
            
            print(f"🔍 Intento {intento}: Clase encontrada - Plazas: {info_clase['plazas']}")
            
            # Intentar reservar
            resultado = await self.nav.intentar_reservar_slot(info_clase["slot"])
            
            if resultado == "RESERVADA":
                await self.db.guardar_reserva(clase, fecha_clase)
                print(f"\n{'🎉'*20}")
                print(f"✅ ¡RESERVA EXITOSA!")
                print(f"   📌 {clase['nombre']}")
                print(f"   📅 {fecha_clase.strftime('%d/%m/%Y')} a las {clase['hora']}")
                print(f"{'🎉'*20}\n")
                return True
            
            elif resultado == "YA_TIENE":
                # Guardar en BD como ya reservada (detectada por el sistema)
                await self.db.guardar_reserva(clase, fecha_clase)
                print(f"ℹ️ El sistema indica que ya tienes esta clase reservada")
                print(f"💾 Guardada en BD para evitar intentos futuros")
                return False
            
            elif resultado == "NO_ABIERTA":
                print(f"⏳ Intento {intento}: Aún no está abierta, reintentando...")
                await asyncio.sleep(0.5)
            
            else:  # ERROR
                print(f"⚠️ Error en intento {intento}, reintentando...")
                await asyncio.sleep(1)
        
        print(f"❌ Tiempo agotado para {clase['nombre']} {clase['hora']} - DESCARTANDO")
        return False
    
    async def ejecutar_reservas(self):
        """
        Ejecuta el proceso completo de reservas.
        Objetivo: Reservar la clase más próxima a abrirse.
        Mientras tanto, intenta reservar todas las clases ya abiertas.
        """
        candidatas = await self.preparar_clases_candidatas()
        
        if not candidatas:
            print("⏹️ No hay clases candidatas para reservar")
            return
        
        ahora = datetime.datetime.now()
        
        # Identificar la clase OBJETIVO (la más próxima a abrirse)
        clase_objetivo_info = None
        for hora_apertura, clase, fecha_clase in candidatas:
            if hora_apertura >= ahora:
                clase_objetivo_info = (hora_apertura, clase, fecha_clase)
                break
        
        # Si no hay ninguna cerrada, la primera abierta es el objetivo
        if not clase_objetivo_info:
            clase_objetivo_info = candidatas[0] if candidatas else None
        
        if clase_objetivo_info:
            _, clase_obj, fecha_obj = clase_objetivo_info
            print(f"\n🎯 CLASE OBJETIVO: {clase_obj['nombre']} - {fecha_obj.strftime('%d/%m/%Y')} {clase_obj['hora']}")
            print(f"   El programa finalizará cuando reserve esta clase\n")
        
        print(f"\n📊 Resumen de clases candidatas:")
        print(f"{'='*60}")
        for i, (hora_apertura, clase, fecha_clase) in enumerate(candidatas, 1):
            estado = "🟢 ABIERTA" if hora_apertura <= ahora else "🔴 CERRADA"
            tiempo_apertura = ""
            if hora_apertura > ahora:
                segundos = (hora_apertura - ahora).total_seconds()
                horas, resto = divmod(int(segundos), 3600)
                minutos, _ = divmod(resto, 60)
                tiempo_apertura = f" (abre en {horas}h {minutos}m)"
            
            # Marcar la clase objetivo
            objetivo_mark = " ⭐ OBJETIVO" if clase_objetivo_info and clase == clase_objetivo_info[1] and fecha_clase == clase_objetivo_info[2] else ""
            
            print(f"{i}. {estado} {clase['nombre']} - {fecha_clase.strftime('%d/%m')} {clase['hora']}{tiempo_apertura}{objetivo_mark}")
        print(f"{'='*60}\n")
        
        clases_reservadas = 0
        dia_actual = None
        clases_reservadas_hoy = 0
        
        # Procesar clases en orden (la que abre primero es la primera)
        for hora_apertura, clase, fecha_clase in candidatas:
            # Verificar si es la clase objetivo
            es_objetivo = (clase_objetivo_info and 
                          clase == clase_objetivo_info[1] and 
                          fecha_clase == clase_objetivo_info[2])
            
            # Verificar si cambiamos de día
            fecha_str = fecha_clase.strftime("%Y-%m-%d")
            if dia_actual != fecha_str:
                # Nuevo día, resetear contador
                dia_actual = fecha_str
                clases_reservadas_hoy = 0
                print(f"\n📅 Procesando clases para el día: {fecha_clase.strftime('%d/%m/%Y')}")
            
            # Si ya reservamos 3 clases para este día, saltar (EXCEPTO si es la objetivo)
            if clases_reservadas_hoy >= MAX_CLASES_POR_DIA and not es_objetivo:
                print(f"⏭️ Límite alcanzado ({MAX_CLASES_POR_DIA} clases) para {fecha_clase.strftime('%d/%m/%Y')}, saltando...")
                continue
            
            ahora = datetime.datetime.now()
            espera = (hora_apertura - ahora).total_seconds()
            
            # PREPARAR PÁGINA (solo si es necesario)
            print(f"\n🔧 Preparando página para reserva...")
            
            # Solo navegar a actividades la PRIMERA VEZ
            if not self.pagina_preparada:
                if not await self.nav.navegar_a_actividades():
                    print("⚠️ Error navegando, saltando esta clase...")
                    continue
                self.pagina_preparada = True
            else:
                print("  ✓ Ya estamos en 'Oferta de actividades' (saltando navegación)")
            
            # Solo seleccionar día si es DIFERENTE al actual
            if self.dia_seleccionado != fecha_str:
                if not await self.nav.seleccionar_dia(fecha_clase):
                    print("⚠️ Error seleccionando día, saltando esta clase...")
                    continue
                self.dia_seleccionado = fecha_str
            else:
                print(f"  ✓ Ya estamos en el día {fecha_clase.strftime('%d/%m/%Y')} (saltando selección)")
            
            print(f"✅ Página lista para reservar")
            
            # Si no está abierta, esperar (YA ESTAMOS EN LA PÁGINA CORRECTA)
            if espera > 0:
                horas, resto = divmod(int(espera), 3600)
                minutos, segundos = divmod(resto, 60)
                objetivo_txt = " ⭐ OBJETIVO ⭐" if es_objetivo else ""
                print(f"\n⏰ Esperando {horas:02d}:{minutos:02d}:{segundos:02d} hasta apertura{objetivo_txt}")
                print(f"   📌 Clase: {clase['nombre']} - {clase['hora']}")
                print(f"   🕐 Abre: {hora_apertura.strftime('%d/%m/%Y %H:%M:%S')}")
                print(f"   ⚡ Página YA preparada - click inmediato cuando se abra\n")
                await asyncio.sleep(espera)
            
            # Intentar reservar (solo click + confirmar, sin navegar de nuevo)
            if es_objetivo:
                print(f"\n{'⭐'*30}")
                print(f"🎯 INTENTANDO RESERVAR CLASE OBJETIVO")
                print(f"{'⭐'*30}\n")
            
            exito = await self.reservar_clase(clase, fecha_clase)
            
            if exito:
                clases_reservadas += 1
                clases_reservadas_hoy += 1
                print(f"\n✅ Clase reservada ({clases_reservadas_hoy}/{MAX_CLASES_POR_DIA} para hoy)\n")
                
                # Si reservamos la OBJETIVO, finalizar programa
                if es_objetivo:
                    print(f"\n{'🎉'*30}")
                    print(f"✅ ¡CLASE OBJETIVO RESERVADA!")
                    print(f"🏁 Finalizando programa...")
                    print(f"{'🎉'*30}\n")
                    print(f"\n📊 Resumen final:")
                    print(f"   • Total de clases reservadas: {clases_reservadas}")
                    print(f"   • Clase objetivo conseguida: {clase['nombre']} - {fecha_clase.strftime('%d/%m/%Y')} {clase['hora']}\n")
                    return
                
                # IMPORTANTE: Después de reservar, volver a Home y resetear navegación
                print(f"\n🔄 Clase reservada exitosamente, reseteando navegación...")
                if not await self.nav.volver_a_home():
                    print("⚠️ Error volviendo a Home, pero continuamos...")
                
                # Resetear flags de navegación
                self.pagina_preparada = False
                self.dia_seleccionado = None
                print(f"✅ Navegación reseteada, listo para siguiente clase\n")
                
                # Si hemos reservado 3 clases para este día, continuar con el siguiente día
                if clases_reservadas_hoy >= MAX_CLASES_POR_DIA:
                    print(f"🎯 Límite de {MAX_CLASES_POR_DIA} clases alcanzado para {fecha_clase.strftime('%d/%m/%Y')}")
            else:
                print(f"\n⚠️ No se pudo reservar {clase['nombre']}, intentando siguiente clase...\n")
                
                # Si no pudimos reservar la OBJETIVO, el programa falla
                if es_objetivo:
                    print(f"\n{'❌'*30}")
                    print(f"❌ NO SE PUDO RESERVAR LA CLASE OBJETIVO")
                    print(f"{'❌'*30}\n")
                    print(f"\n📊 Resumen final:")
                    print(f"   • Total de clases reservadas: {clases_reservadas}")
                    print(f"   • Clase objetivo NO conseguida\n")
                    return
        
        # Si llegamos aquí, procesamos todas las clases
        if clases_reservadas > 0:
            print(f"\n🎉 Proceso finalizado - Total de clases reservadas: {clases_reservadas}\n")
        else:
            print("\n❌ No se pudo reservar ninguna clase de la lista\n")

# ============================================================================
# MAIN
# ============================================================================

async def main():
    """Función principal"""
    print("\n" + "="*60)
    print("🏋️  SISTEMA DE RESERVAS DE CLASES")
    print("="*60 + "\n")
    
    db_manager = DatabaseManager()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,  # Cambia a True para modo invisible
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        
        try:
            context = await browser.new_context()
            page = await context.new_page()
            
            navigator = WebNavigator(page)
            reservas_manager = ReservasManager(db_manager, navigator)
            
            # Login
            if not await navigator.hacer_login():
                print("❌ No se pudo iniciar sesión, abortando")
                return
            
            # Ejecutar reservas
            await reservas_manager.ejecutar_reservas()
            
        finally:
            await browser.close()
            db_manager.cerrar()
            print("\n✅ Programa finalizado\n")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️ Programa interrumpido por el usuario\n")
    except Exception as e:
        print(f"\n❌ Error fatal: {e}\n")