
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
#define TEMPERATURA_MINIMA_CALDERA 45   // °C
#define TEMPERATURA_MAXIMA_CALDERA 60   // °C
#define TEMPERATURA_MINIMA_PILETA 26    // °C
#define TEMPERATURA_MAXIMA_PILETA 30    // °C
#define TEMPERATURA_ENCENDIDO_BOMBA 55  // °C
#define TIEMPO_FUNCIONAMIENTO_BOMBA 30  // segundos

// Posiciones del buffer
enum Sensor {
  TEMPERATURA_PILETA = 0,
  TEMPERATURA_CALDERA = 1
};

typedef struct {
  uint32_t wcet;
  uint32_t periodo;
  UBaseType_t prioridad;
} TareaConfig;

// Buffer compartido: temperatura_pileta, temperatura_caldera
int bufferSensores[2] = { 30, 50 };

// Mutex para proteger el buffer
SemaphoreHandle_t mutexBuffer;

// Variables globales
int temperatura_caldera = 50;
int temperatura_pileta = 30;

bool bombaEncendida = false;
bool valvulaGasAbierta = false;
bool necesitaCalefaccion = false;

unsigned long momentoEncendido = 0;

// Configuracion de tareas
static const TareaConfig leerSerial = { 0, 1000, configMAX_PRIORITIES - 1 };

static const TareaConfig evaluarCaldera = { 0, 1000, configMAX_PRIORITIES - 2 };

static const TareaConfig evaluarBomba = { 0, 1000, configMAX_PRIORITIES - 3 };

static const TareaConfig leerSensorTemperaturaPileta = { 0, 6000, configMAX_PRIORITIES - 4 };

static const TareaConfig leerSensorTemperaturaCaldera = { 0, 15000, configMAX_PRIORITIES - 5 };

//Devuelve el último valor de un sensor
int obtenerSensor(Sensor sensor) {
  int valor = 0;
  //Toma el mutex antes de acceder al buffer
  if (xSemaphoreTake(mutexBuffer, portMAX_DELAY) == pdTRUE) {
    valor = bufferSensores[sensor];
    //Libera el mutex
    xSemaphoreGive(mutexBuffer);
  }
  return valor;
}

//Escribe una lectura simulada en el buffer.
void guardarSensor(Sensor sensor, int valor) {
  //Toma el mutex antes de acceder al buffer
  if (xSemaphoreTake(mutexBuffer, portMAX_DELAY) == pdTRUE) {
    bufferSensores[sensor] = valor;
    //Libera el mutex
    xSemaphoreGive(mutexBuffer);
  }
}

void setup() {
  Serial.begin(115200);

  mutexBuffer = xSemaphoreCreateMutex();

  //Comprobación de que el mutex se creó correctamente
  if (mutexBuffer == NULL) {
    Serial.println("Error al crear mutex");
    ESP.restart();
  }

  void LeerSerial(void *pvParameters);
  void LeerSensorTemperaturaPileta(void *pvParameters);
  void LeerSensorTemperaturaCaldera(void *pvParameters);
  void EvaluarBomba(void *pvParameters);
  void EvaluarCaldera(void *pvParameters);

  xTaskCreatePinnedToCore(
    LeerSerial,
    "Leer serial",
    4096,
    (void *)&leerSerial,
    leerSerial.prioridad,
    NULL,
    TASK_RUNNING_CORE);

  xTaskCreatePinnedToCore(
    LeerSensorTemperaturaPileta,
    "Leer sensor temperatura pileta",
    4096,
    (void *)&leerSensorTemperaturaPileta,
    leerSensorTemperaturaPileta.prioridad,
    NULL,
    TASK_RUNNING_CORE);

  xTaskCreatePinnedToCore(
    LeerSensorTemperaturaCaldera,
    "Leer sensor temperatura caldera",
    4096,
    (void *)&leerSensorTemperaturaCaldera,
    leerSensorTemperaturaCaldera.prioridad,
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
    EvaluarCaldera,
    "Evaluar caldera",
    4096,
    (void *)&evaluarCaldera,
    evaluarCaldera.prioridad,
    NULL,
    TASK_RUNNING_CORE);
}

void loop() {}


/*
 * Tarea encargada de leer los datos recibidos por el puerto serie.
 *
 * Formatos aceptados:
 *   P:25 significa temperatura de la pileta = 25 °C
 *   C:55 significa temperatura de la caldera = 55 °C
 *
 * La tarea: Lee los caracteres recibidos por serial, construye un mensaje completo.
 * comprueba que tenga un formato válido, identifica el sensor correspondiente y guarda
 * el valor en el buffer compartido.
 */

void LeerSerial(void *pvParameters) {

  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = 0;

  // Array donde se almacenan los caracteres de un mensaje recibido por serial
  // Capacidad: 31 caracteres + terminador '\0'
  char linea[32];

  // Indica cuántos caracteres se almacenaron hasta el momento en el array linea
  size_t posicion = 0;

  // Indica si se está descartando un mensaje porque superó la capacidad del array
  bool descartando = false;

  for (;;) {

    // Procesa todos los bytes que se encuentran en el serial
    while (Serial.available() > 0) {

      // Lee un único carácter del puerto serie
      char caracter = Serial.read();

      if (caracter == '\n') {

        // Filtra solo mensajes aptos para procesar
        if (!descartando && posicion > 0) {

          // Agrega el terminador nulo para que el array pueda tratarse como una cadena de caracteres de C.
          linea[posicion] = '\0';

          // Elimina '\r' si el mensaje termina en "\r\n", como ocurre con algunos monitores serie.
          if (posicion > 0 &&
              linea[posicion - 1] == '\r') {

            linea[posicion - 1] = '\0';
          }

          // Verifica el formato básico: Mínimo 3 caracteres y segundo carácter igual a ':'
          if (strlen(linea) >= 3 && linea[1] == ':') {
            // Puntero utilizado por strtol() para indicar dónde termina el número
            char *fin;

            // Convierte la parte numérica del mensaje (despues de ":") a un entero de tipo long.
            long valor = strtol(linea + 2, &fin, 10);

            // Comprueba que se haya convertido algún dígito y no queden caracteres después del número.
            if (fin != linea + 2 && *fin == '\0') {

              // Identifica el sensor utilizando el primer carácter del mensaje.
              switch (linea[0]) {

                // Temperatura de la pileta
                case 'P':
                  if (valor >= 0 && valor <= 100) {
                    guardarSensor(TEMPERATURA_PILETA, (int)valor);
                    Serial.printf("Buffer temperatura pileta: %ld\n", valor);
                  }
                  break;

                // Temperatura de la caldera
                case 'C':

                  if (valor >= 0 && valor <= 100) {
                    // Guarda la temperatura en el buffer compartido
                    guardarSensor(TEMPERATURA_CALDERA, (int)valor);
                    Serial.printf("Buffer temperatura caldera: %ld\n", valor);
                  }
                  break;
              }
            }
          }
        }

        // Reinicia la posición para comenzar a recibir el siguiente mensaje.
        posicion = 0;

        // Habilita nuevamente la recepción, incluso si el mensaje anterior fue largo.
        descartando = false;

      } else if (!descartando) {

        // Si todavía no llegó '\n', continúa almacenando los caracteres recibidos.

        // Comprueba que quede espacio en el array. Se reserva una posición para '\0'.
        if (posicion < sizeof(linea) - 1) {

          // Guarda el carácter recibido y luego incrementa la posición.
          linea[posicion++] = caracter;

        } else {

          // El mensaje excedió la capacidad, se descarta hasta encontrar '\n'
          posicion = 0;
          descartando = true;
        }
      }
    }

    vTaskDelayUntil(
        &inicio,
        pdMS_TO_TICKS(config->periodo)
    );
  }
}


/*
 * Lee la temperatura de la caldera desde el buffer cada 15 segundos
 */
void LeerSensorTemperaturaCaldera(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = 0;

  for (;;) {

    //Obtiene la última temperatura de la caldera simulada
    int valor = obtenerSensor(TEMPERATURA_CALDERA);

    temperatura_caldera = valor;

    vTaskDelayUntil(&inicio, pdMS_TO_TICKS(config->periodo));
  }
}

/*
 * Lee la temperatura de la pileta desde el buffer cada 6 segundos
 */
void LeerSensorTemperaturaPileta(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = 0;

  for (;;) {

    int valor = obtenerSensor(TEMPERATURA_PILETA);
 
    temperatura_pileta = valor;

    vTaskDelayUntil(&inicio, pdMS_TO_TICKS(config->periodo));
  }
}

void EvaluarBomba(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = 0;

  for (;;) {
    unsigned long ahora = millis();
    // Control ON/OFF de la pileta
    if (temperatura_pileta <= TEMPERATURA_MINIMA_PILETA) {
      necesitaCalefaccion = true;
    } else if (temperatura_pileta >= TEMPERATURA_MAXIMA_PILETA) {
      necesitaCalefaccion = false;
    }
    // la bomba de circulacion se prende a partir de los 55 ºC y se apaga por debajo de 45 ºC

    if (!bombaEncendida && necesitaCalefaccion && temperatura_caldera >= TEMPERATURA_ENCENDIDO_BOMBA && temperatura_caldera <= TEMPERATURA_MAXIMA_CALDERA) {

      bombaEncendida = true;
      momentoEncendido = ahora;

      Serial.println("Bomba circulación agua: ENCENDIDA");
    }


    // Apagado
    if (bombaEncendida && (temperatura_caldera < TEMPERATURA_MINIMA_CALDERA ||                                         //si la temperatura de la caldera baja de 45
                           temperatura_caldera > TEMPERATURA_MAXIMA_CALDERA ||                                         //si la temperatura de la caldera sube de 60
                           (!necesitaCalefaccion && ahora - momentoEncendido >= TIEMPO_FUNCIONAMIENTO_BOMBA * 1000UL)  //llegó a 30 grados y pasaron al menos 30 segundos
                           )) {

      bombaEncendida = false;
      Serial.println("Bomba circulación agua: APAGADA");
    }


    // si la temperatura de la pileta es menor a 26 y la de la caldera mayor a 45, prender la bomba de circulacion
    // idem pero la caldera menor a 45, prender caldera, esperar que llegue a 45 y prender bomba de circulacion


    vTaskDelayUntil(&inicio, pdMS_TO_TICKS(config->periodo));
  }
}

void EvaluarCaldera(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = 0;

  for (;;) {

    if ((temperatura_caldera <= TEMPERATURA_MINIMA_CALDERA) && !valvulaGasAbierta) {
      valvulaGasAbierta = true;
      Serial.println("Válvula gas: ABIERTA");
    } else if ((temperatura_caldera >= TEMPERATURA_MAXIMA_CALDERA) && valvulaGasAbierta) {
      valvulaGasAbierta = false;
      Serial.println("Válvula gas: CERRADA");
    }

    vTaskDelayUntil(&inicio, pdMS_TO_TICKS(config->periodo));
  }
}