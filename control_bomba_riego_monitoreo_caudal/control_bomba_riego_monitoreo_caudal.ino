
#include <Arduino.h>
#include <stdlib.h>
#include <string.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>

#if CONFIG_FREERTOS_UNICORE
#define TASK_RUNNING_CORE 0
#else
#define TASK_RUNNING_CORE 1
#endif

// Constantes
#define HUMEDAD_MINIMA_SUELO 30     // %
#define HUMEDAD_MAXIMA_SUELO 90     // %
#define RPM_MINIMAS_BOMBA 600       // RPM
#define TIEMPO_INTERVALO_RIEGO 30   // segundos
#define TIEMPO_ENCENDIDO_BOMBA 10   // segundos
#define TIEMPO_SIN_CAUDAL 5         // segundos
#define TIEMPO_VERIFICACION_RPM 1   // segundos
#define TIEMPO_ALARMA_CAUDAL 15     // segundos
#define TIEMPO_ALARMA_ROTACION 30   // segundos

#define LED_ALARMA 2

// Posiciones del buffer
enum Sensor {
  HUMEDAD = 0,
  CAUDAL = 1,
  VELOCIDAD = 2
};

typedef struct {
  uint32_t wcet;
  uint32_t periodo;
  UBaseType_t prioridad;
} TareaConfig;

// Buffer compartido: humedad, caudal, RPM
int bufferSensores[3] = {50, 1, 800};

// Mutex para proteger el buffer
SemaphoreHandle_t mutexBuffer;

// Variables globales
int humedadActual = 50;
int velocidadRPM = 800;

bool hayCaudal = true;
bool bombaEncendida = false;
bool necesidadRiego = false;
bool rpmVerificado = false;
bool contandoSinCaudal = false;

bool alarmaFaltaDeCaudalActiva = false;
bool alarmaRPMSinRotacionActiva = false;

unsigned long momentoEncendido = 0;
unsigned long momentoSinCaudal = 0;
unsigned long momentoAlarmaCaudal = 0;
unsigned long momentoAlarmaRotacion = 0;
unsigned long ultimoInicioRiego = 0;

// Configuracion de tareas
static const TareaConfig leerSerial = {0, 1000, configMAX_PRIORITIES - 1};

static const TareaConfig leerSensorCaudal = {0, 1000, configMAX_PRIORITIES - 2};

static const TareaConfig evaluarBomba = {0, 1000, configMAX_PRIORITIES - 3};

static const TareaConfig leerSensorHumedad = {0, 30000, configMAX_PRIORITIES - 4};

// Declaraciones
void LeerSerial(void *pvParameters);
void LeerSensorCaudal(void *pvParameters);
void LeerSensorHumedad(void *pvParameters);
void EvaluarBomba(void *pvParameters);

int obtenerSensor(Sensor sensor) {
  int valor = 0;

  if (xSemaphoreTake(mutexBuffer, portMAX_DELAY) == pdTRUE) {
    valor = bufferSensores[sensor];
    xSemaphoreGive(mutexBuffer);
  }

  return valor;
}

void guardarSensor(Sensor sensor, int valor) {
  if (xSemaphoreTake(mutexBuffer, portMAX_DELAY) == pdTRUE) {
    bufferSensores[sensor] = valor;
    xSemaphoreGive(mutexBuffer);
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(LED_ALARMA, OUTPUT);
  digitalWrite(LED_ALARMA, LOW);

  mutexBuffer = xSemaphoreCreateMutex();

  if (mutexBuffer == NULL) {
    Serial.println("Error al crear mutex");
    while (true) {
      delay(1000);
    }
  }

  // Permite iniciar el primer riego inmediatamente
  // cuando se detecte falta de humedad.
  ultimoInicioRiego = millis() - TIEMPO_INTERVALO_RIEGO * 1000UL;

  xTaskCreatePinnedToCore(
      LeerSerial,
      "Leer serial",
      4096,
      (void *)&leerSerial,
      leerSerial.prioridad,
      NULL,
      TASK_RUNNING_CORE);

  xTaskCreatePinnedToCore(
      LeerSensorCaudal,
      "Leer sensor caudal",
      4096,
      (void *)&leerSensorCaudal,
      leerSensorCaudal.prioridad,
      NULL,
      TASK_RUNNING_CORE);

  xTaskCreatePinnedToCore(
      EvaluarBomba,
      "Evaluar bomba",
      4096,
      (void *)&evaluarBomba,
      evaluarBomba.prioridad,
      NULL,
      TASK_RUNNING_CORE);

  xTaskCreatePinnedToCore(
      LeerSensorHumedad,
      "Leer sensor humedad",
      4096,
      (void *)&leerSensorHumedad,
      leerSensorHumedad.prioridad,
      NULL,
      TASK_RUNNING_CORE);
}

void loop() {
  vTaskDelay(pdMS_TO_TICKS(1000));
}

/*
 * Recibe mensajes:
 * H:25  -> humedad
 * C:1   -> caudal
 * B:800 -> velocidad RPM
 *
 * Es la unica tarea que lee Serial.
 */
void LeerSerial(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = xTaskGetTickCount();

  char linea[32];
  size_t posicion = 0;
  bool descartando = false;

  for (;;) {

    while (Serial.available() > 0) {
      char caracter = Serial.read();

      if (caracter == '\n') {
        if (!descartando && posicion > 0) {
          linea[posicion] = '\0';

          // Eliminar retorno de carro si existe
          if (posicion > 0 && linea[posicion - 1] == '\r') {
            linea[posicion - 1] = '\0';
          }

          if (strlen(linea) >= 3 && linea[1] == ':') {
            char *fin;
            long valor = strtol(linea + 2, &fin, 10);

            // Comprobar que el numero sea valido
            if (fin != linea + 2 && *fin == '\0') {

              switch (linea[0]) {
                case 'H':
                  if (valor >= 0 && valor <= 100) {
                    guardarSensor(HUMEDAD, (int)valor);
                    Serial.printf("Buffer humedad: %ld%%\n", valor);
                  }
                  break;

                case 'C':
                  if (valor == 0 || valor == 1) {
                    guardarSensor(CAUDAL, (int)valor);
                    Serial.printf("Buffer caudal: %ld\n", valor);
                  }
                  break;

                case 'B':
                  if (valor >= 0 && valor <= 100000) {
                    guardarSensor(VELOCIDAD, (int)valor);
                    Serial.printf("Buffer RPM: %ld\n", valor);
                  }
                  break;
              }
            }
          }
        }

        posicion = 0;
        descartando = false;

      } else if (!descartando) {
        if (posicion < sizeof(linea) - 1) {
          linea[posicion++] = caracter;
        } else {
          posicion = 0;
          descartando = true;
        }
      }
    }

    vTaskDelayUntil(&inicio, pdMS_TO_TICKS(config->periodo));
  }
}

/*
 * Lee el valor del caudal desde el buffer cada 1 s.
 */
void LeerSensorCaudal(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = xTaskGetTickCount();

  for (;;) {

    int valor = obtenerSensor(CAUDAL);

    hayCaudal = (valor == 1);

    vTaskDelayUntil(&inicio, pdMS_TO_TICKS(config->periodo));
  }
}

/*
 * Lee la humedad desde el buffer cada 30 s.
 */
void LeerSensorHumedad(void *pvParameters) {

  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = xTaskGetTickCount();

  for (;;) {

    humedadActual = obtenerSensor(HUMEDAD);

    Serial.printf("Humedad actual: %d%%\n", humedadActual);

    vTaskDelayUntil(&inicio, pdMS_TO_TICKS(config->periodo));
  }
}

/*
 * Controla bomba y alarmas.
 */
void EvaluarBomba(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = xTaskGetTickCount();

  bool estadoLed = false;

  for (;;) {
    unsigned long ahora = millis();

    // Obtener RPM del buffer
    velocidadRPM = obtenerSensor(VELOCIDAD);

    if (humedadActual < HUMEDAD_MINIMA_SUELO) {
      necesidadRiego = true;

    } else if (humedadActual > HUMEDAD_MAXIMA_SUELO) {
      necesidadRiego = false;

      if (bombaEncendida) {
        bombaEncendida = false;
        Serial.println("Bomba: APAGADA");
      }
    }

    // Encendido periodico
    if (necesidadRiego && !bombaEncendida && (ahora - ultimoInicioRiego >= TIEMPO_INTERVALO_RIEGO * 1000UL)) {

      bombaEncendida = true;
      ultimoInicioRiego = ahora;
      momentoEncendido = ahora;
      rpmVerificado = false;
      contandoSinCaudal = false;

      Serial.println("Bomba: ENCENDIDA");
    }

    // Control de caudal
    if (bombaEncendida && !hayCaudal) {

      if (!contandoSinCaudal) {
        momentoSinCaudal = ahora;
        contandoSinCaudal = true;
      }

      if (ahora - momentoSinCaudal >= TIEMPO_SIN_CAUDAL * 1000UL) {

        bombaEncendida = false;
        contandoSinCaudal = false;

        alarmaFaltaDeCaudalActiva = true;
        momentoAlarmaCaudal = ahora;

        Serial.println("Bomba: APAGADA");
        Serial.println(
            "ERROR: Bomba detenida por falta de caudal");
      }

    } else {
      contandoSinCaudal = false;
    }

    // Verificacion de RPM
    if (bombaEncendida && !rpmVerificado && (ahora - momentoEncendido >= TIEMPO_VERIFICACION_RPM * 1000UL)) {

      rpmVerificado = true;

      if (velocidadRPM < RPM_MINIMAS_BOMBA) {
        Serial.println("ERROR: Bomba sin rotacion");

        alarmaRPMSinRotacionActiva = true;
        momentoAlarmaRotacion = ahora;
      }
    }

    // Apagado despues de 10 segundos
    if (bombaEncendida && (ahora - momentoEncendido >= TIEMPO_ENCENDIDO_BOMBA * 1000UL)) {

      bombaEncendida = false;
      Serial.println("Bomba: APAGADA");
    }

    // Finalizar alarma caudal
    if (alarmaFaltaDeCaudalActiva && (ahora - momentoAlarmaCaudal >= TIEMPO_ALARMA_CAUDAL * 1000UL)) {

      alarmaFaltaDeCaudalActiva = false;
    }

    // Finalizar alarma RPM
    if (alarmaRPMSinRotacionActiva && (ahora - momentoAlarmaRotacion >= TIEMPO_ALARMA_ROTACION * 1000UL)) {

      alarmaRPMSinRotacionActiva = false;
    }

    if (alarmaFaltaDeCaudalActiva && alarmaRPMSinRotacionActiva) {
      estadoLed = !estadoLed;
    } else if (alarmaFaltaDeCaudalActiva || alarmaRPMSinRotacionActiva) {
      estadoLed = true;
    } else {
      estadoLed = false;
    }

    digitalWrite(LED_ALARMA, estadoLed ? HIGH : LOW);

    vTaskDelayUntil(&inicio, pdMS_TO_TICKS(config->periodo));
  }
}
