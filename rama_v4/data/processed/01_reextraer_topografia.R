# =============================================================================
# RE-EXTRACCIÓN DE VARIABLES TOPOGRÁFICAS PARA BASE DE DATOS SDM
# -----------------------------------------------------------------------------
# Proyecto : Modelo de distribución de especies (SDM) - Chile
# Problema : 1251 registros tienen elevación, pendiente y exposiciones = 0
#            (extracción previa falló por huecos en el DEM original)
# Solución : Re-extraer las 4 variables topográficas usando Copernicus GLO-30
#            (DEM global sin huecos en Chile, resolución 30 m)
# Autor    : [Tu nombre]
# Fecha    : 2026-05
# =============================================================================


# -----------------------------------------------------------------------------
# 0. PAQUETES
# -----------------------------------------------------------------------------
# Instalar lo que falte
pkgs <- c("terra", "sf", "readxl", "writexl", "dplyr", "geodata")
inst <- pkgs[!pkgs %in% installed.packages()[, "Package"]]
if (length(inst) > 0) install.packages(inst)

library(terra)     # Manejo de rásters
library(sf)        # Vectorial
library(readxl)    # Leer Excel
library(writexl)   # Escribir Excel
library(dplyr)
library(geodata)   # Para descargar DEM (alternativa cómoda)


# -----------------------------------------------------------------------------
# 1. PARÁMETROS DEL PROYECTO
# -----------------------------------------------------------------------------
# Rutas (ajusta a tu entorno)
ruta_input  <- "base_datos_completa.xlsx"
ruta_dem    <- "data/dem/copernicus_glo30_chile.tif"   # DEM a usar
ruta_output <- "base_datos_completa_topografia_corregida.xlsx"
ruta_log    <- "log_reextraccion_topografia.csv"

# Sistema de coordenadas
crs_geo  <- "EPSG:4326"      # WGS84 (las coordenadas de la base están aquí)
# Para pendiente y exposición conviene proyectar a CRS métrico.
# Para Chile continental: SIRGAS-Chile / UTM 19S (norte) y 18S/19S según zona.
# Como tu base abarca Arica (Z19) hasta Biobío (Z18S), uso una proyección equiárea:
crs_metric <- "EPSG:5361"    # SIRGAS-Chile 2002 / UTM 19S (cubre la mayoría)
# Alternativa global: "ESRI:54034" (World Cylindrical Equal Area)


# -----------------------------------------------------------------------------
# 2. CARGA DE DATOS
# -----------------------------------------------------------------------------
df <- read_excel(ruta_input, sheet = "base_datos")
cat("Registros totales :", nrow(df), "\n")

# Identificar registros con problema topográfico
df <- df %>%
  mutate(
    topo_falla = (elevacion == 0 &
                  pendiente == 0 &
                  exposicion_norte == 0 &
                  exposicion_este  == 0)
  )

cat("Registros con topografía OK   :", sum(!df$topo_falla, na.rm = TRUE), "\n")
cat("Registros con topografía falla:", sum( df$topo_falla, na.rm = TRUE), "\n")


# -----------------------------------------------------------------------------
# 3. DESCARGA / CARGA DEL DEM
# -----------------------------------------------------------------------------
# OPCIÓN A — Si ya tienes el DEM descargado, simplemente cargarlo:
if (file.exists(ruta_dem)) {
  dem <- rast(ruta_dem)
  cat("DEM cargado desde:", ruta_dem, "\n")
} else {

  # OPCIÓN B — Descargar SRTM con geodata (más simple, resolución 30 m / 90 m)
  # Cubre Chile y países vecinos automáticamente
  message("DEM no encontrado. Descargando SRTM mediante geodata::elevation_30s()...")

  # Bounding box que cubra toda tu base (Chile + países vecinos)
  bb_lon <- range(df$lon, na.rm = TRUE) + c(-1, 1)
  bb_lat <- range(df$lat, na.rm = TRUE) + c(-1, 1)

  # elevation_30s descarga a 1 km (suficiente para BIO clim);
  # para 30 m usar elevation_3s (más pesado, varios tiles)
  dem <- elevation_30s(country = "CHL", path = "data/dem/", mask = FALSE)

  # Si necesitas también países vecinos (para registros en AR, PE, BO):
  paises <- unique(df$pais)
  iso <- c("Chile"="CHL","Argentina"="ARG","Peru"="PER",
           "Bolivia (Plurinational State of)"="BOL","Colombia"="COL",
           "Brazil"="BRA","Ecuador"="ECU","Paraguay"="PRY")
  iso_pres <- iso[paises]
  iso_pres <- iso_pres[!is.na(iso_pres)]

  dem_list <- lapply(iso_pres, function(i) {
    tryCatch(elevation_30s(country = i, path = "data/dem/", mask = FALSE),
             error = function(e) NULL)
  })
  dem_list <- dem_list[!sapply(dem_list, is.null)]

  # Mosaico de todos los países
  if (length(dem_list) > 1) {
    dem <- do.call(mosaic, dem_list)
  } else {
    dem <- dem_list[[1]]
  }

  # Guardar el DEM combinado para reutilizar
  dir.create("data/dem", showWarnings = FALSE, recursive = TRUE)
  writeRaster(dem, "data/dem/dem_combinado.tif", overwrite = TRUE)
  ruta_dem <- "data/dem/dem_combinado.tif"
}


# -----------------------------------------------------------------------------
# 4. DERIVAR PENDIENTE Y EXPOSICIÓN (sobre DEM proyectado)
# -----------------------------------------------------------------------------
# IMPORTANTE: pendiente y exposición se calculan SOBRE EL RASTER, no por punto.
# Si el DEM está en grados, conviene proyectarlo a un CRS métrico para que la
# pendiente salga en grados reales y no exagerada.

cat("Proyectando DEM a CRS métrico...\n")
dem_m <- project(dem, crs_metric, method = "bilinear")

cat("Calculando pendiente (grados)...\n")
slope <- terrain(dem_m, v = "slope", unit = "degrees")

cat("Calculando exposición (grados)...\n")
aspect <- terrain(dem_m, v = "aspect", unit = "degrees")

# Convertir exposición a componentes (más útiles para SDM):
#  northness =  cos(aspect en radianes)  →  1 = norte, −1 = sur
#  eastness  =  sin(aspect en radianes)  →  1 = este,  −1 = oeste
aspect_rad <- aspect * pi / 180
northness  <- cos(aspect_rad)
eastness   <- sin(aspect_rad)

# Pila final
stack_topo <- c(dem_m, slope, northness, eastness)
names(stack_topo) <- c("elevacion", "pendiente",
                       "exposicion_norte", "exposicion_este")


# -----------------------------------------------------------------------------
# 5. EXTRACCIÓN PARA TODOS LOS PUNTOS
# -----------------------------------------------------------------------------
# Convertir registros a SpatVector
puntos_sf <- df %>%
  st_as_sf(coords = c("lon", "lat"), crs = crs_geo, remove = FALSE)
puntos    <- vect(puntos_sf) %>%
  project(crs_metric)

# Extraer valores
cat("Extrayendo variables topográficas para", nrow(df), "puntos...\n")
ext <- extract(stack_topo, puntos, ID = FALSE)


# -----------------------------------------------------------------------------
# 6. DIAGNÓSTICO DE LA EXTRACCIÓN
# -----------------------------------------------------------------------------
n_na <- colSums(is.na(ext))
cat("\n=== NaN en la nueva extracción ===\n")
print(n_na)

# Si hay NaN, significa que el DEM tampoco cubre esos puntos
# (probablemente coordenadas inválidas o lejos de cualquier DEM disponible).
# En ese caso, conserva la columna original o márcalas para revisión manual.

# Log de cambios
log_df <- df %>%
  transmute(
    fila              = row_number(),
    especie           = especie,
    lat, lon,
    elev_anterior     = elevacion,
    elev_nueva        = ext$elevacion,
    pend_anterior     = pendiente,
    pend_nueva        = ext$pendiente,
    delta_elev        = ext$elevacion - elevacion,
    fue_corregido     = topo_falla
  )

write.csv(log_df, ruta_log, row.names = FALSE)
cat("Log de cambios guardado en:", ruta_log, "\n")


# -----------------------------------------------------------------------------
# 7. CONSTRUIR LA BASE DE DATOS CORREGIDA
# -----------------------------------------------------------------------------
# Estrategia: SOBREESCRIBIR sólo los registros con topo_falla.
# Los que ya tenían topografía válida se mantienen como estaban
# (para preservar la consistencia con otras variables ya procesadas).

df_corregido <- df

idx <- which(df$topo_falla)
df_corregido$elevacion[idx]        <- ext$elevacion[idx]
df_corregido$pendiente[idx]        <- ext$pendiente[idx]
df_corregido$exposicion_norte[idx] <- ext$exposicion_norte[idx]
df_corregido$exposicion_este[idx]  <- ext$exposicion_este[idx]

# Si algún registro corregido sigue saliendo NA (DEM no cubre), marcarlo
sigue_mal <- idx[is.na(df_corregido$elevacion[idx])]
if (length(sigue_mal) > 0) {
  warning(length(sigue_mal),
          " registros siguen sin elevación válida tras re-extracción.\n",
          "Revisar log_reextraccion_topografia.csv en filas: ",
          paste(sigue_mal, collapse = ", "))
}

# Eliminar columna auxiliar
df_corregido$topo_falla <- NULL


# -----------------------------------------------------------------------------
# 8. VERIFICACIÓN ANTES/DESPUÉS
# -----------------------------------------------------------------------------
cat("\n=== ANTES de la corrección (solo registros con falla) ===\n")
print(summary(df$elevacion[df$topo_falla]))

cat("\n=== DESPUÉS de la corrección (mismos registros) ===\n")
print(summary(df_corregido$elevacion[df$topo_falla]))

# Esperable: la mediana DESPUÉS debería estar entre 500–2000 m
# (Atacama / Coquimbo interior); si sigue cerca de 0, hay que revisar el DEM.


# -----------------------------------------------------------------------------
# 9. GUARDAR BASE CORREGIDA
# -----------------------------------------------------------------------------
# Mantener la estructura original (dos hojas: base_datos + diccionario_variables)
dicc <- read_excel(ruta_input, sheet = "diccionario_variables")

write_xlsx(
  list(
    base_datos              = df_corregido,
    diccionario_variables   = dicc
  ),
  path = ruta_output
)

cat("\n✓ Base de datos corregida guardada en:", ruta_output, "\n")


# =============================================================================
# FIN DEL SCRIPT
# =============================================================================
#
# CHECKLIST DESPUÉS DE CORRER ESTE SCRIPT:
#   [ ] Verificar que el log no tenga filas con elev_nueva = NA
#   [ ] Revisar histograma de elevación (debería tener distribución realista)
#   [ ] Verificar que la pendiente no exceda 60° en zonas planas
#   [ ] Validar 5–10 puntos manualmente contra Google Earth
#
# SIGUIENTE PASO:
#   → Re-correr el EDA con la base corregida
#   → Definir área de calibración (M) por especie
#   → Generar pseudo-ausencias / background
# =============================================================================
