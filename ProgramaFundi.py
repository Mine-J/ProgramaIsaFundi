import asyncio
from playwright.async_api import async_playwright
from dotenv import load_dotenv
import os
import datetime
import json
from motor.motor_asyncio import AsyncIOMotorClient

# --- Cargar variables del .env primero ---
load_dotenv()

# --- URL de MongoDB Atlas ---
MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise ValueError("No se encontró MONGO_URL en el .env")

# --- Conexión al cluster ---
client = AsyncIOMotorClient(MONGO_URL)
db = client["reservas_clases"]        # Base de datos
coleccion = db["clases_reservadas"]   # Colección

print("✅ Conectado a MongoDB Atlas correctamente")

# --- Cargar variables del .env ---
EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")

RESERVADAS_FILE = os.path.join(os.path.dirname(__file__), "clases_reservadas.json")


async def cargar_reservadas():
    fecha_inicio = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    cursor = coleccion.find({"fecha": {"$gte": fecha_inicio}})
    return await cursor.to_list(length=None)


async def guardar_reservada(clase, fecha_clase):
    clase_con_fecha = dict(clase)
    clase_con_fecha["fecha"] = fecha_clase.strftime("%Y-%m-%d")
    existe = await coleccion.find_one({
        "nombre": clase_con_fecha["nombre"],
        "hora": clase_con_fecha["hora"],
        "fecha": clase_con_fecha["fecha"]
    })
    if not existe:
        await coleccion.insert_one(clase_con_fecha)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, slow_mo=0)
        context = await browser.new_context()
        page = await context.new_page()

        # --- Abrir página de login ---
        await page.goto("https://deportesweb.madrid.es/DeportesWeb/login")
        print("✅ Página de login abierta")
        try:
            await page.click("div.navigation-section-widget-collection-item-image-icon-square")
            print("✅ Click realizado en el div de la clase.")
        except:
            print("❌ No se pudo hacer click en el div (puede que no sea necesario).")
        # --- Login automático ---
        await page.fill("input[name='ctl00$ContentFixedSection$uLogin$txtIdentificador']", EMAIL)
        await page.fill("input[name='ctl00$ContentFixedSection$uLogin$txtContrasena']", PASSWORD)
        await page.click("button#ContentFixedSection_uLogin_btnLogin")
        print("⌛ Login enviado, esperando a que cargue tu perfil...")

        # --- Esperar a que cargue el perfil ---
        try:
            await page.wait_for_selector("div#ctl00_divProfile", timeout=15000)
            print("✅ Login completado correctamente")
        except:
            print("❌ No se pudo iniciar sesión")
            await browser.close()
            return

        # Selector específico para el artículo "La Fundi"
        selector = "article.navigation-section-widget-collection-item h4[title='La Fundi']"
        try:
            await page.wait_for_selector(selector, timeout=5000)
            await page.click(selector)
            print("✅ Click correcto disparado en el artículo 'La Fundi'.")
        except Exception as e:
            print("❌ No se pudo hacer click en el artículo 'La Fundi'.", e)

        # Selector para el artículo "Oferta de actividades por día y centro"
        selector_actividades = "article.navigation-section-widget-collection-item h4[title='Oferta de actividades por día y centro']"
        try:
            await page.wait_for_selector(selector_actividades, timeout=5000)
            await page.click(selector_actividades)
            print("✅ Click correcto en 'Oferta de actividades por día y centro'.")
        except Exception as e:
            print("❌ No se pudo hacer click en 'Oferta de actividades por día y centro'.", e)

            
        # --- Definir las clases de interés ---
        CLASES = [
            {"dia": "lunes", "hora": "16:30", "nombre": "Fitness"},
            {"dia": "lunes", "hora": "17:30", "nombre": "Entrenamiento en suspensión"},
            {"dia": "lunes", "hora": "18:30", "nombre": "Yoga"},
            {"dia": "martes", "hora": "17:30", "nombre": "Fitness"},
            {"dia": "miércoles", "hora": "16:30", "nombre": "Fitness"},
            {"dia": "miércoles", "hora": "17:30", "nombre": "Entrenamiento en suspensión"},
            {"dia": "jueves", "hora": "17:30", "nombre": "Fitness"},
            {"dia": "viernes", "hora": "15:30", "nombre": "Pilates MesD"},
            {"dia": "viernes", "hora": "16:30", "nombre": "Funcional MesD"}
        ]


        DIAS = {
            "lunes": 0,
            "martes": 1,
            "miércoles": 2,
            "jueves": 3,
            "viernes": 4,
            "sábado": 5,
            "domingo": 6,
        }

        def proxima_fecha(dia_semana, hora):
            hoy = datetime.datetime.now()
            objetivo = hoy.replace(hour=int(hora.split(":")[0]), minute=int(hora.split(":")[1]), second=0, microsecond=0)
            dias_a_sumar = (DIAS[dia_semana] - hoy.weekday() + 7) % 7
            if dias_a_sumar == 0 and hoy < objetivo:
                return objetivo
            elif dias_a_sumar == 0:
                return objetivo + datetime.timedelta(days=7)
            else:
                return objetivo + datetime.timedelta(days=dias_a_sumar)

        async def seleccionar_dia(page, fecha):
            fecha_str = fecha.strftime("%d/%m/%Y")
            selector_dia = f"td.day[data-day='{fecha_str}']"
            try:
                await page.wait_for_selector(selector_dia, timeout=5000)
                await page.click(selector_dia)
                print(f"✅ Día seleccionado en el calendario: {fecha_str}")
            except Exception as e:
                print(f"❌ No se pudo seleccionar el día {fecha_str} en el calendario.", e)

        async def volver_a_fundi_y_actividades(page):
            selector_fundi = "article.navigation-section-widget-collection-item h4[title='La Fundi']"
            try:
                await page.wait_for_selector(selector_fundi, timeout=5000)
                await page.click(selector_fundi)
                print("✅ Click en 'La Fundi'.")
            except Exception as e:
                print("❌ No se pudo hacer click en 'La Fundi'.", e)
            selector_actividades = "article.navigation-section-widget-collection-item h4[title='Oferta de actividades por día y centro']"
            try:
                await page.wait_for_selector(selector_actividades, timeout=5000)
                await page.click(selector_actividades)
                print("✅ Click en 'Oferta de actividades por día y centro'.")
            except Exception as e:
                print("❌ No se pudo hacer click en 'Oferta de actividades por día y centro'.", e)

        async def reservar_clase(page, nombre, hora):
            selector_clase = f"li.media:has(h4.media-heading:has-text('{hora}'))"
            try:
                elemento = await page.query_selector(selector_clase)
                if elemento:
                    span_plazas = await elemento.query_selector("span")
                    if span_plazas:
                        plazas_texto = await span_plazas.inner_text()
                        if plazas_texto.strip() == "0":
                            print(f"⏭ Clase '{nombre} {hora}' sin plazas disponibles, saltando...")
                            return False
            except Exception as e:
                print("⚠️ Error al comprobar plazas, intentando reservar de todas formas", e)

            try:
                await page.wait_for_selector(selector_clase, timeout=5000)
                await page.click(selector_clase)
                print(f"✅ Clase '{nombre} {hora}' seleccionada.")
                boton_confirmar = "button#ContentFixedSection_uCarritoConfirmar_btnConfirmCart"
                try:
                    await page.wait_for_selector(boton_confirmar, timeout=5000)
                    await page.click(boton_confirmar)
                except Exception:
                    print("⚠️ No hay botón de confirmar, puede que ya esté reservada o no disponible.")
                    return False

                try:
                    await page.wait_for_selector("div.alert-danger", timeout=2000)
                    print("❌ Mensaje en rojo detectado: clase ya reservada o no disponible. Sigo con el flujo.")
                except Exception:
                    print("✅ No hay mensaje en rojo, la reserva probablemente fue correcta.")

                try:
                    boton_salir = "a.btn.btn-default[href='../../Home']"
                    await page.wait_for_selector(boton_salir, timeout=5000)
                    await page.click(boton_salir)
                    print("🔄 Has vuelto a la página principal.")
                except Exception:
                    await page.goto("https://deportesweb.madrid.es/DeportesWeb/Home")
                    print("🔄 Has vuelto a la página principal (por URL).")

                selector_home = "div#ctl00_divProfile"
                try:
                    await page.wait_for_selector(selector_home, timeout=15000)
                    print("🏠 Página Home cargada correctamente.")
                except Exception:
                    print("❌ No se pudo cargar la página Home, esperando indefinidamente.")
                    while True:
                        try:
                            await page.wait_for_selector(selector_home, timeout=60000)
                            print("🏠 Página Home cargada correctamente.")
                            break
                        except:
                            print("⏳ Aún no está la Home, sigo esperando...")

                await volver_a_fundi_y_actividades(page)
                return True
            except Exception as e:
                print(f"❌ No se pudo reservar la clase '{nombre} {hora}'.", e)
                return False

        # --- Espera inteligente hasta que falten exactamente 49 horas para la próxima clase ---
        ahora = datetime.datetime.now()
        proximas_fechas = [proxima_fecha(clase["dia"], clase["hora"]) for clase in CLASES]
        if proximas_fechas:
            fecha_proxima_clase = min(proximas_fechas)
            hora_apertura = fecha_proxima_clase - datetime.timedelta(hours=49)
            tiempo_espera = (hora_apertura - ahora).total_seconds()
            if tiempo_espera > 0 and tiempo_espera < 120:
                horas = int(tiempo_espera // 3600)
                minutos = int((tiempo_espera % 3600) // 60)
                print(f"⏳ Esperando {horas} horas y {minutos} minutos hasta que falten 49 horas para la próxima clase ({fecha_proxima_clase.strftime('%d/%m/%Y %H:%M')})...")
                print(f"🕒 Podrás reservar la clase el {hora_apertura.strftime('%d/%m/%Y %H:%M')}")
                await asyncio.sleep(tiempo_espera)
            else:
                print("🔔 Ya estamos dentro de la ventana de 49 horas para reservar la próxima clase.")
                print(f"🕒 Podrías reservar desde el {hora_apertura.strftime('%d/%m/%Y %H:%M')}")

        # --- Bucle sobre todas las clases durante 1 minutos ---
        tiempo_limite_global = datetime.datetime.now() + datetime.timedelta(minutes=1)
        clase_reservada = False

        while datetime.datetime.now() < tiempo_limite_global:
            ahora = datetime.datetime.now()
            reservadas = await cargar_reservadas()
            clases_pendientes = 0  # Contador de clases que se pueden intentar

            for clase in CLASES:
                fecha_clase = proxima_fecha(clase["dia"], clase["hora"])
                clase_con_fecha = dict(clase)
                clase_con_fecha["fecha"] = fecha_clase.strftime("%Y-%m-%d")

                def ya_reservada(clase, fecha_clase, reservadas):
                    for reservada in reservadas:
                        if reservada["nombre"].lower() == clase["nombre"].lower() and reservada["hora"] == clase["hora"]:
                            fecha_reservada = datetime.datetime.strptime(reservada["fecha"], "%Y-%m-%d")
                            if abs((fecha_clase - fecha_reservada).days) < 7:
                                return True
                    return False

                if clase_con_fecha in reservadas or ya_reservada(clase, fecha_clase, reservadas):
                    continue

                horas_hasta_clase = (fecha_clase - ahora).total_seconds() / 3600

                if 0 <= horas_hasta_clase <= 49:
                    clases_pendientes += 1
                    selector_clase = f"li.media:has(h4.media-heading:has-text('{clase['hora']}'))"
                    try:
                        await seleccionar_dia(page, fecha_clase)
                        elemento = await page.query_selector(selector_clase)
                        if elemento:
                            span_plazas = await elemento.query_selector("span")
                            if span_plazas:
                                plazas_texto = await span_plazas.inner_text()
                                if plazas_texto.strip() == "0":
                                    print(f"⏭ Clase '{clase['nombre']} {clase['hora']}' sin plazas disponibles, pasando a la siguiente clase...")
                                    continue
                            # Extraer el nombre real de la clase desde el HTML
                            nombre_web = await elemento.evaluate("el => el.closest('.panel-body').querySelector('.media-body .media-heading').innerText")
                            if nombre_web.strip().lower() != clase["nombre"].strip().lower():
                                print(f"⏭ Nombre en la web ('{nombre_web.strip()}') no coincide con el deseado ('{clase['nombre']}'), saltando...")
                                continue
                        exito = await reservar_clase(page, clase["nombre"], clase["hora"])
                        if exito:
                            await guardar_reservada(clase, fecha_clase)
                            clase_reservada = True
                            print(f"✅ Clase '{clase['nombre']} {clase['hora']}' reservada correctamente. Cerrando programa.")
                            await browser.close()
                            return 0
                    except Exception as e:
                        print(f"⚠️ Error comprobando plazas para '{clase['nombre']} {clase['hora']}':", e)
                else:
                    continue

            # Si no hay clases pendientes, termina el programa inmediatamente
            if clases_pendientes == 0:
                print("✅ No hay ninguna clase pendiente para reservar. Cerrando programa.")
                await browser.close()
                return 0

            await asyncio.sleep(2)  # Espera corta antes de volver a recorrer todas las clases

        print("⏹ No se pudo reservar ninguna clase en 2 minutos. Cerrando programa.")
        #await browser.close()
        #return 0

asyncio.run(main())