# Metricas del modelo de distribucion de especies (SDM), explicadas

Este documento explica, en lenguaje sencillo, las metricas con las que evaluamos
nuestro modelo de distribucion de especies (SDM, por sus siglas en ingles:
Species Distribution Model). El objetivo es que cualquier persona, sin necesidad
de ser especialista, entienda que medimos, que significa un "buen" resultado y
como le fue a nuestro modelo.

En una frase: un SDM aprende en que condiciones ambientales (clima, suelo,
relieve) vive una especie y, con eso, dibuja un mapa de "donde es probable
encontrarla". Las metricas son las notas del examen de ese mapa.

---

## Resumen ejecutivo

Estos son los resultados promedio de nuestro modelo sobre 14 especies, medidos
con validacion cruzada espacial (la forma honesta de evaluar, explicada mas
abajo).

| Metrica | Que mide en una frase | Rango | Umbral "bueno" | Nuestro modelo |
|---|---|---|---|---|
| TSS | Aciertos balanceando presencias y ausencias | -1 a 1 | > 0.7 bueno | 0.82 |
| AUC | Capacidad de ordenar sitios buenos por encima de malos | 0.5 a 1 | > 0.9 excelente | 0.94 |
| Boyce / CBI | Si las zonas mas idoneas concentran mas presencias reales | -1 a 1 | cercano a 1 | 0.68 |
| Brier | Error de las probabilidades (calibracion) | 0 a 1 | cuanto menor mejor | 0.04 |
| MESS | Donde el modelo extrapola (predice fuera de lo que vio) | mapa | --- | mapa diagnostico |

Lectura rapida: el modelo acierta mucho (TSS y AUC altos), esta muy bien
calibrado (Brier muy bajo) y, lo mas importante para nuestros datos, concentra
las presencias reales en las zonas que marca como idoneas (Boyce positivo y
solido). Ninguna de estas metricas se reporta sola; mas adelante explicamos por
que.

---

## Una analogia para todo el documento

Imagine que el modelo es un explorador que nunca vio la especie, pero estudio
miles de fotos del paisaje donde si vive. Le pedimos que, mirando un terreno
nuevo, diga: "aqui es muy probable encontrarla" o "aqui no".

- **TSS** mide cuantas veces acierta en sus dos tipos de juicio: detectar donde
  si esta y descartar donde no esta.
- **AUC** mide si, cuando comparamos dos sitios, sabe poner mas arriba al que de
  verdad tiene la especie.
- **Boyce** mide si los lugares que el explorador marca como "los mejores" son,
  en efecto, donde mas veces se ha visto la especie.
- **Brier** mide si cuando dice "80% de probabilidad", de verdad acierta el 80%
  de las veces (no que sea timido ni que sea fanfarron).
- **MESS** es el explorador admitiendo: "este paisaje no se parece a nada de lo
  que estudie; aqui mi opinion vale menos".

---

## Las metricas, una por una

### TSS (True Skill Statistic)

- **Que mide:** los aciertos del modelo, pero de forma balanceada. Premia por
  igual detectar bien las presencias (donde si esta la especie) y las ausencias
  (donde no esta). Esto evita que un modelo "tramposo" que dice "no esta en
  ningun lado" parezca bueno solo porque las ausencias son mayoria.
- **Analogia:** una nota de examen que cuenta por igual las dos preguntas
  ("donde si" y "donde no"), en vez de dejar que una sola domine.
- **Rango:** de -1 a 1. Un 0 equivale a adivinar al azar; valores negativos
  serian peor que el azar.
- **Que valor es bueno:** mayor a 0.5 es aceptable; mayor a 0.7 es bueno.
- **Nuestro modelo:** media de **0.82**, claramente en territorio bueno.

### AUC (area bajo la curva ROC)

- **Que mide:** la probabilidad de que, si tomamos al azar un sitio con presencia
  y otro sin ella, el modelo le asigne una puntuacion mas alta al que si tiene la
  especie. Es decir, mide su capacidad de **ordenar** correctamente.
- **Analogia:** un juez que, entre dos candidatos, casi siempre identifica al
  correcto, aunque no acierte el porcentaje exacto.
- **Rango:** de 0.5 (azar puro) a 1 (perfecto).
- **Que valor es bueno:** mayor a 0.8 es bueno; mayor a 0.9 es excelente.
- **Nuestro modelo:** media de **0.94**, excelente.
- **Nota importante:** a escala global el AUC puede "inflarse". Si la especie
  vive solo en una region pequena del mundo, distinguir su clima del de un
  desierto o un polo es facil y el numero sale alto sin que eso pruebe mucho. Por
  eso nunca lo reportamos solo: lo acompanamos de TSS, de Boyce y, sobre todo, de
  validacion espacial.

### Boyce / CBI (Continuous Boyce Index)

- **Que mide:** si las zonas que el modelo marca como **mas idoneas** son,
  efectivamente, donde se concentran mas presencias reales. Es la metrica mas
  honesta cuando solo tenemos datos de **presencia** (como los de GBIF, la base
  de datos global de observaciones), porque no requiere saber con certeza donde
  la especie esta ausente, dato que casi nunca tenemos.
- **Analogia:** marcamos en un mapa las "zonas premium" segun el modelo y luego
  contamos cuantos avistamientos reales caen ahi. Si las zonas premium concentran
  muchas mas observaciones que las zonas pobres, el modelo es coherente.
- **Rango:** de -1 a 1. Cercano a 1 es bueno (las zonas idoneas concentran las
  presencias); cercano a 0 significa que el modelo no separa mejor que el azar; y
  **negativo es malo**: las presencias aparecen justo donde el modelo decia que
  no deberian estar.
- **Que valor es bueno:** cuanto mas cerca de 1, mejor.
- **Nuestro modelo:** media de **0.68**, un valor positivo y solido. Suele ser la
  metrica mas exigente y mas baja de las tres de acierto, lo cual es esperable y
  saludable: nos da la lectura mas realista.

### Brier score

- **Que mide:** que tan buenas son las **probabilidades** que da el modelo, no
  solo si acierta el si/no. Es una medida de **calibracion**: cuando dice "70% de
  probabilidad", queremos que de verdad ocurra cerca del 70% de las veces.
- **Analogia:** un pronosticador del tiempo. No basta con acertar si llueve o no;
  si dice "90% de lluvia", debe llover casi siempre que lo dice. Un Brier bajo es
  un pronosticador en quien se puede confiar al pie de la letra.
- **Rango:** de 0 a 1. **Menor es mejor**; 0 seria perfecto.
- **Nuestro modelo:** media de **0.04**, muy bueno. Las probabilidades que
  entrega son confiables, no solo el orden de los sitios.

### MESS (Multivariate Environmental Similarity Surface) - extrapolacion

- **Que mide:** no es una nota de acierto, sino un **mapa de confianza**. Senala
  las zonas donde el modelo esta prediciendo en condiciones ambientales que
  **nunca vio al entrenar** (por ejemplo, temperaturas o lluvias fuera del rango
  conocido). En esas zonas el modelo esta extrapolando y su prediccion es **menos
  confiable**.
- **Analogia:** el explorador del ejemplo diciendo "este paisaje no se parece a
  nada de lo que estudie". Su opinion sigue ahi, pero con una advertencia.
- **Como se usa:** acompana a los mapas de idoneidad para marcar las regiones
  donde hay que tomar la prediccion con cautela. Es especialmente util para
  proyecciones a escenarios futuros de clima, donde la extrapolacion es inevitable.

---

## Validacion cruzada espacial: por que es la clave de todo

Para confiar en una nota, el examen tiene que ser justo. En modelos de
distribucion de especies, el examen justo es la **validacion cruzada espacial**.

**Que es:** dividimos el mundo en **regiones geograficas** y entrenamos el modelo
ocultandole una region completa. Luego lo evaluamos justamente en esa region que
**no vio**. Repetimos rotando las regiones. Asi medimos si el modelo realmente
**transfiere** su conocimiento a territorio nuevo, que es lo que de verdad nos
interesa.

**Por que importa (y por que NO usamos puntos al azar):** los puntos que estan
geograficamente cerca se parecen mucho entre si (clima, suelo y relieve casi
identicos). Si repartimos los puntos al azar (un k-fold aleatorio comun), es
casi seguro que un punto de "examen" tenga un vecino practicamente igual en el
grupo de "estudio". El modelo entonces no demuestra que aprendio, sino que
**memorizo al vecino de al lado**. El resultado: notas artificialmente altas que
se desploman en el mundo real.

**Bandera roja, no verde:** por eso, una metrica de **0.99 obtenida con k-fold
aleatorio** no es una buena noticia, es una **senal de alarma** (probable fuga de
informacion y sobreajuste). Un 0.82 honesto con validacion espacial vale mucho
mas que un 0.99 inflado. Todos los numeros de este documento provienen de
validacion espacial.

| Forma de evaluar | Que tan parecidos son examen y estudio | Que mide en realidad | Veredicto |
|---|---|---|---|
| K-fold aleatorio | Muy parecidos (puntos vecinos mezclados) | Memorizacion del vecino | Infla resultados; enganoso |
| Validacion cruzada espacial | Distintos (regiones separadas) | Capacidad real de transferir | Honesto; el que usamos |

---

## Como leer un caso bueno frente a uno problematico

Las metricas se entienden mejor con ejemplos reales de nuestras 14 especies.

### Caso bueno: Encelia canescens

| Metrica | Valor | Lectura |
|---|---|---|
| TSS | 0.95 | Acierta presencias y ausencias casi siempre |
| AUC | 0.99 | Ordena casi a la perfeccion sitios buenos sobre malos |
| Boyce | 0.97 | Las zonas idoneas concentran de forma muy clara las presencias reales |

Las tres metricas son altas **a la vez**. Esto es lo que esperamos de una especie
con un nicho ambiental bien definido y datos coherentes: el modelo aprende su
patron y lo transfiere bien a regiones nuevas.

### Caso problematico: Schinus areira

| Metrica | Valor | Lectura |
|---|---|---|
| Boyce | -0.31 | Negativo: las presencias caen donde el modelo decia que no deberian |

Aqui el Boyce es **negativo**, una senal de problema. La causa no es un error de
programacion, sino la biologia: Schinus areira es una **especie introducida**.
Vive en sitios a los que el ser humano la llevo, no necesariamente donde su clima
"natural" lo predeciria, y su patron **no transfiere bien entre regiones** (lo que
aprende el modelo en una zona no sirve en otra). Es un ejemplo perfecto de por que
miramos varias metricas y por que la validacion espacial es tan reveladora: un
caso asi pasaria desapercibido con un k-fold aleatorio, pero la evaluacion honesta
lo deja en evidencia y nos dice "cuidado con interpretar este mapa al pie de la
letra".

La leccion para el lector no especialista: un numero malo no siempre significa
"modelo mal hecho"; a veces significa "esta especie es dificil por su biologia", y
detectarlo es justamente parte del valor del analisis.

---

## Por que ninguna metrica sola basta

Cada metrica mira una cara distinta del problema y tiene un punto ciego. Por eso
reportamos un conjunto: donde una falla, otra avisa.

| Que reportamos | Que pregunta responde | Por que no basta sola |
|---|---|---|
| TSS | Acierta el si/no de forma balanceada? | No dice nada sobre que tan buenas son las probabilidades ni sobre el ordenamiento fino |
| AUC | Ordena bien los sitios buenos sobre los malos? | A escala global se infla; un valor alto puede dar falsa confianza |
| Boyce / CBI | Las zonas idoneas concentran las presencias reales? | Es la mas honesta con solo-presencia, pero es ruidosa y puede ser exigente; conviene leerla junto a las demas |
| Brier | Las probabilidades estan bien calibradas? | Mide calidad del numero, no la capacidad de separar presencia de ausencia |
| MESS | Donde la prediccion es menos confiable? | No es una nota de acierto; solo marca zonas de extrapolacion |
| Validacion espacial | El modelo transfiere a territorio nuevo? | Es el metodo de prueba, no una metrica; da sentido honesto a todas las anteriores |

**Conclusion:** miradas en conjunto, estas metricas dan una vision completa y
honesta. Nuestro modelo muestra acierto alto (TSS 0.82, AUC 0.94), buena
coherencia espacial de las presencias (Boyce 0.68), probabilidades confiables
(Brier 0.04) y un mapa de extrapolacion (MESS) que indica donde leer los
resultados con cautela. Y todo ello bajo validacion espacial, que es la vara de
medir mas exigente y realista disponible.
