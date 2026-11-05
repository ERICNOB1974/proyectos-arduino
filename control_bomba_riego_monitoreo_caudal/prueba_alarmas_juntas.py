
#!/usr/bin/env python3
import serial
import time
import sys
import threading

# Configuración de la comunicación serie con la ESP32
PUERTO = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
BAUDIOS = 115200

# Variables para registrar errores y mensajes de la ESP32
errores = []
eventos = []
lock = threading.Lock()
detener = threading.Event()

# Mensajes que se esperan recibir de la ESP32
INICIO = "Bomba: ENCENDIDA"
APAGADO = "Bomba: APAGADA"
ERROR_CAUDAL = "ERROR: Bomba detenida por falta de caudal"
ERROR_RPM = "ERROR: Bomba sin rotación"

# Establecer conexión con la ESP32
try:
    esp = serial.Serial(PUERTO, BAUDIOS, timeout=0.1)
except Exception as e:
    print("ATENCION, ERROR DE FUNCIONAMIENTO")
    print(f"No se pudo conectar: {e}")
    sys.exit(1)


def lector():
    # Lee continuamente los mensajes enviados por la ESP32
    # y registra el momento en que se recibe cada uno.
    while not detener.is_set():
        try:
            linea = esp.readline().decode(
                "utf-8", errors="replace"
            ).strip()

            if linea:
                instante = time.monotonic()

                with lock:
                    eventos.append((instante, linea))

                print(f"[ESP32] {linea}", flush=True)

        except Exception as e:
            print(f"Error leyendo Serial: {e}")
            detener.set()
            break


# Ejecutar la lectura serie en paralelo con las pruebas
threading.Thread(target=lector, daemon=True).start()


def enviar(mensaje):
    # Envía valores simulados de los sensores a la ESP32
    print(f"[PYTHON] {mensaje}", flush=True)
    esp.write((mensaje + "\n").encode())
    esp.flush()


def marca():
    # Guarda la posición actual del registro de eventos
    # para analizar solamente los mensajes nuevos.
    with lock:
        return len(eventos)


def buscar(texto, desde=0):
    # Busca un mensaje específico entre los eventos registrados.
    # Si lo encuentra, devuelve el instante en que ocurrió.
    with lock:
        for instante, linea in eventos[desde:]:
            if texto in linea:
                return instante
    return None


def esperar(texto, timeout, desde=0):
    # Espera hasta recibir un mensaje determinado.
    # Si se supera el tiempo máximo, devuelve None.
    limite = time.monotonic() + timeout

    while time.monotonic() < limite:
        encontrado = buscar(texto, desde)

        if encontrado is not None:
            return encontrado

        time.sleep(0.05)

    return None


def error(mensaje):
    # Registra los errores detectados durante las pruebas
    errores.append(mensaje)
    print("\nATENCION, ERROR DE FUNCIONAMIENTO")
    print(mensaje, "\n")


def preparar():
    print("\n===== PREPARACION =====")

    # Establecer condiciones iniciales sin necesidad de riego:
    # Humedad alta, caudal presente y velocidad correcta.
    enviar("H:95")
    enviar("C:1")
    enviar("B:800")

    # Esperar a que transcurra el período de control de humedad
    time.sleep(33)


def probar_alarmas_juntas():
    print("\n===== PRUEBA DE ALARMAS SIMULTANEAS =====")

    # PASO 1: Configurar RPM inferiores al mínimo permitido (600)
    # manteniendo inicialmente el caudal.
    enviar("C:1")
    enviar("B:450")

    time.sleep(2)

    # PASO 2: Simular suelo seco para provocar el encendido
    indice = marca()
    enviar("H:20")

    print("\nEsperando encendido de bomba...")

    # La bomba debería encenderse dentro de los próximos 35 s
    t_encendido = esperar(INICIO, 35, indice)

    if t_encendido is None:
        error("La bomba no encendió con humedad del 20%")
        return

    print("\nBomba encendida. Provocando falta de caudal.")

    # PASO 3: Quitar el caudal inmediatamente después del encendido
    # para generar una segunda condición de falla.
    t_corte = time.monotonic()
    enviar("C:0")

    # Esperar a que el sistema detecte ambas fallas
    time.sleep(9)

    # PASO 4: Buscar los mensajes de alarma y apagado
    t_rpm = buscar(ERROR_RPM, indice)
    if t_rpm is None:
        # También aceptar el mensaje sin tilde
        t_rpm = buscar("ERROR: Bomba sin rotacion", indice)

    t_caudal = buscar(ERROR_CAUDAL, indice)
    t_apagado = buscar(APAGADO, indice)

    print("\n===== RESULTADOS =====")

    # VERIFICACIÓN 1: La alarma de RPM debe activarse
    # aproximadamente 1 segundo después del encendido.
    if t_rpm is None:
        error("No se activó la alarma de RPM")
    else:
        demora = t_rpm - t_encendido
        print(f"Alarma RPM: {demora:.2f} s desde encendido")

        if demora < 0.5 or demora > 3.5:
            error(
                f"Alarma RPM fuera de tiempo: {demora:.2f} s"
            )

    # VERIFICACIÓN 2: La falta de caudal debe detectarse
    # después de aproximadamente 5 segundos continuos sin caudal.
    if t_caudal is None:
        error("No se activó la alarma de falta de caudal")
    else:
        demora = t_caudal - t_corte
        print(f"Alarma caudal: {demora:.2f} s desde corte")

        if demora < 4 or demora > 8:
            error(
                f"Alarma caudal fuera de tiempo: {demora:.2f} s"
            )

    # VERIFICACIÓN 3: La bomba debe apagarse cuando se detecta
    # la falta de caudal para evitar que funcione en vacío.
    if t_apagado is None:
        error("La bomba no se apagó por falta de caudal")
    else:
        demora = t_apagado - t_corte
        print(f"Apagado: {demora:.2f} s desde corte")

        if demora < 4 or demora > 8:
            error(
                f"Apagado fuera de tiempo: {demora:.2f} s"
            )

    # VERIFICACIÓN 4: La alarma de RPM debe aparecer primero,
    # porque se comprueba antes que la falta de caudal.
    if t_rpm and t_caudal:
        if t_rpm > t_caudal:
            error(
                "La alarma RPM se activó después de "
                "la alarma de caudal"
            )

    # Confirmar que ambas condiciones de falla fueron detectadas
    if t_rpm and t_caudal:
        print("\nAmbas alarmas fueron detectadas.")

    # VERIFICACIÓN 5: El apagado de la bomba y la alarma de caudal
    # deben ocurrir prácticamente al mismo tiempo.
    if t_apagado and t_caudal:
        if abs(t_apagado - t_caudal) > 1.5:
            error(
                "El apagado y la alarma de caudal "
                "ocurrieron con demasiado desfase"
            )


try:
    # Ejecutar la preparación y luego la prueba de fallas
    preparar()
    probar_alarmas_juntas()

except KeyboardInterrupt:
    print("\nPrueba interrumpida.")

except Exception as e:
    error(f"Excepción durante la prueba: {e}")

finally:
    # Restaurar condiciones normales de los sensores
    print("\nRestaurando sensores...")
    try:
        enviar("H:95")
        enviar("C:1")
        enviar("B:800")
    except Exception:
        pass

    # Finalizar la lectura y cerrar la conexión serie
    time.sleep(0.5)
    detener.set()
    esp.close()

    # Mostrar un resumen con los resultados obtenidos
    print("\n" + "=" * 55)
    print("RESUMEN FINAL")
    print("=" * 55)

    if errores:
        # Si se detectaron fallas, informar cada una
        print("ATENCION, ERROR DE FUNCIONAMIENTO")
        print(f"Se detectaron {len(errores)} problemas:")

        for numero, mensaje in enumerate(errores, 1):
            print(f"{numero}. {mensaje}")

        sys.exit(1)
    else:
        # Si no hubo errores registrados, aprobar las verificaciones
        print("PRUEBAS APROBADAS")
        print("No se detectaron errores en los eventos observables.")
        print("Pendiente: verificar duracion de alarmas.")
