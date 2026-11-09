
#!/usr/bin/env python3
import serial
import time
import sys
import threading

PUERTO = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
BAUDIOS = 115200

errores = []
eventos = []
lock = threading.Lock()
detener = threading.Event()

INICIO = "Bomba: ENCENDIDA"
APAGADO = "Bomba: APAGADA"
ERROR_CAUDAL = "ERROR: Bomba detenida por falta de caudal"
ERROR_RPM = "ERROR: Bomba sin rotación"

try:
    esp = serial.Serial(PUERTO, BAUDIOS, timeout=0.1)
except Exception as e:
    sys.exit(f"No se pudo conectar: {e}")


def lector():
    # Lee los mensajes de la ESP32 en paralelo con las pruebas
    while not detener.is_set():
        try:
            linea = esp.readline().decode("utf-8", errors="replace").strip()
            if linea:
                with lock:
                    eventos.append((time.monotonic(), linea))
                print(f"[ESP32] {linea}", flush=True)
        except Exception as e:
            print(f"Error leyendo Serial: {e}")
            detener.set()


threading.Thread(target=lector, daemon=True).start()


def enviar(mensaje):
    print(f"[PYTHON] {mensaje}", flush=True)
    esp.write((mensaje + "\n").encode())
    esp.flush()


def marca():
    with lock:
        return len(eventos)


def buscar(texto, desde=0):
    with lock:
        for instante, linea in eventos[desde:]:
            if texto in linea:
                return instante
    return None


def esperar(texto, timeout, desde=0):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        encontrado = buscar(texto, desde)
        if encontrado is not None:
            return encontrado
        time.sleep(0.05)
    return None


def error(mensaje):
    errores.append(mensaje)
    print(f"\nATENCION, ERROR DE FUNCIONAMIENTO\n{mensaje}\n")


def preparar():
    print("\n===== PREPARACION =====")
    enviar("H:95")
    enviar("C:1")
    enviar("B:800")
    time.sleep(33)


def probar_alarmas_juntas():
    print("\n===== PRUEBA DE ALARMAS SIMULTANEAS =====")

    # RPM bajas y caudal normal antes del encendido
    enviar("C:1")
    enviar("B:450")
    time.sleep(2)

    indice = marca()
    enviar("H:20")

    print("\nEsperando encendido de bomba...")
    t_encendido = esperar(INICIO, 35, indice)

    if t_encendido is None:
        error("La bomba no encendió con humedad del 20%")
        return

    # Cortar el caudal apenas arranca la bomba
    print("\nBomba encendida. Cortando caudal...")
    t_corte = time.monotonic()
    enviar("C:0")
    time.sleep(9)

    t_rpm = buscar(ERROR_RPM, indice) or buscar("ERROR: Bomba sin rotacion", indice)
    t_caudal = buscar(ERROR_CAUDAL, indice)
    t_apagado = buscar(APAGADO, indice)

    print("\n===== RESULTADOS =====")

    # Control de tiempos de las alarmas
    pruebas = [
        ("RPM", t_rpm, t_encendido, 0.5, 3.5),
        ("Caudal", t_caudal, t_corte, 4, 8),
        ("Apagado", t_apagado, t_corte, 4, 8)
    ]

    for nombre, instante, referencia, minimo, maximo in pruebas:
        if instante is None:
            error(f"No se detectó: {nombre}")
            continue

        demora = instante - referencia
        print(f"{nombre}: {demora:.2f} s")

        if not minimo <= demora <= maximo:
            error(f"{nombre} fuera de tiempo: {demora:.2f} s")

    # La falla de RPM debe detectarse antes que la de caudal
    if t_rpm is not None and t_caudal is not None:
        if t_rpm > t_caudal:
            error("La alarma RPM se activó después de la de caudal")
        else:
            print("Ambas alarmas fueron detectadas")

    if t_apagado is not None and t_caudal is not None:
        if abs(t_apagado - t_caudal) > 1.5:
            error("Demasiado tiempo entre el apagado y la alarma de caudal")


try:
    preparar()
    probar_alarmas_juntas()

except KeyboardInterrupt:
    print("\nPrueba interrumpida")

except Exception as e:
    error(f"Error durante la prueba: {e}")

finally:
    # Dejar los sensores en condiciones normales
    print("\nRestaurando sensores...")
    try:
        enviar("H:95")
        enviar("C:1")
        enviar("B:800")
    except Exception:
        pass

    time.sleep(0.5)
    detener.set()
    esp.close()

    print("\n===== RESUMEN FINAL =====")

    if errores:
        print(f"ATENCION, ERROR DE FUNCIONAMIENTO: {len(errores)} errores")
        for i, mensaje in enumerate(errores, 1):
            print(f"{i}. {mensaje}")
        sys.exit(1)

    print("PRUEBAS APROBADAS")
    print("No se detectaron errores.")
